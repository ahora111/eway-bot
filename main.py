import requests
import os
import re
import time
import json
import random
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from threading import Lock
import logging
from logging.handlers import RotatingFileHandler
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type

# ==============================================================================
# --- تنظیمات لاگینگ ---
# ==============================================================================
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=1024*1024, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# ==============================================================================
# --- اطلاعات ووکامرس و سایت مبدا ---
# ==============================================================================
BASE_URL = "https://panel.eways.co"
SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"
PRODUCT_LIST_URL_TEMPLATE = f"{BASE_URL}/Store/List/{{category_id}}/2/2/0/0/0/10000000000?page={{page}}"
PRODUCT_DETAIL_URL_TEMPLATE = f"{BASE_URL}/Store/Detail/{{cat_id}}/{{product_id}}"

WC_API_URL = os.environ.get("WC_API_URL") or "https://your-woocommerce-site.com/wp-json/wc/v3"
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY") or "ck_xxx"
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET") or "cs_xxx"

EWAYS_USERNAME = os.environ.get("EWAYS_USERNAME") or "شماره موبایل یا یوزرنیم"
EWAYS_PASSWORD = os.environ.get("EWAYS_PASSWORD") or "پسورد"

CACHE_FILE = 'products_cache.json'  # فایل کش

# ==============================================================================
# --- تابع لاگین اتوماتیک به eways ---
# ==============================================================================
def login_eways(username, password):
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f"{BASE_URL}/",
        'X-Requested-With': 'XMLHttpRequest',
        'Accept-Language': 'en-US,en;q=0.9,fa;q=0.8'
    })
    session.verify = False

    login_url = f"{BASE_URL}/User/Login"
    payload = {
        "UserName": username,
        "Password": password,
        "RememberMe": "true"
    }
    logger.info("⏳ در حال لاگین به پنل eways ...")
    resp = session.post(login_url, data=payload, timeout=30)
    if resp.status_code != 200:
        logger.error(f"❌ لاگین ناموفق! کد وضعیت: {resp.status_code}")
        return None

    if 'Aut' in session.cookies:
        logger.info("✅ لاگین موفق! کوکی Aut دریافت شد.")
        return session
    else:
        logger.error("❌ کوکی Aut دریافت نشد. لاگین ناموفق یا کپچا فعال است.")
        return None

# ==============================================================================
# --- توابع مربوط به سایت مبدا (eways) ---
# ==============================================================================
def get_and_parse_categories(session):
    logger.info(f"⏳ دریافت دسته‌بندی‌ها از: {SOURCE_CATS_API_URL}")
    try:
        response = session.get(SOURCE_CATS_API_URL, timeout=30)
        response.raise_for_status()
        try:
            data = response.json()
            logger.info("✅ پاسخ JSON است. در حال پردازش...")
            final_cats = []
            for c in data:
                real_id_match = re.search(r'/Store/List/(\d+)', c.get('url', ''))
                real_id = int(real_id_match.group(1)) if real_id_match else c.get('id')
                final_cats.append({
                    "id": real_id,
                    "name": c.get('name', '').strip(),
                    "parent_id": c.get('parent_id')
                })
            logger.info(f"✅ تعداد {len(final_cats)} دسته‌بندی از JSON استخراج شد.")
            return final_cats
        except json.JSONDecodeError:
            logger.warning("⚠️ پاسخ JSON نیست. تلاش برای پارس HTML...")

        soup = BeautifulSoup(response.text, 'lxml')
        all_menu_items = soup.select("li[id^='menu-item-']")
        if not all_menu_items:
            logger.error("❌ هیچ آیتم دسته‌بندی در HTML پیدا نشد.")
            return []
        logger.info(f"🔎 تعداد {len(all_menu_items)} آیتم منو پیدا شد. در حال پردازش...")

        cats_map = {}
        for item in all_menu_items:
            cat_id_raw = item.get('id', '')
            match = re.search(r'(\d+)', cat_id_raw)
            if not match: continue
            cat_menu_id = int(match.group(1))
            a_tag = item.find('a', recursive=False) or item.select_one("a")
            if not a_tag or not a_tag.get('href'): continue
            name = a_tag.text.strip()
            real_id_match = re.search(r'/Store/List/(\d+)', a_tag['href'])
            real_id = int(real_id_match.group(1)) if real_id_match else None
            if name and real_id and name != "#":
                cats_map[cat_menu_id] = {"id": real_id, "name": name, "parent_id": None}
        for item in all_menu_items:
            cat_id_raw = item.get('id', '')
            match = re.search(r'(\d+)', cat_id_raw)
            if not match: continue
            cat_menu_id = int(match.group(1))
            parent_li = item.find_parent("li", class_="menu-item-has-children")
            if parent_li:
                parent_id_raw = parent_li.get('id', '')
                parent_match = re.search(r'(\d+)', parent_id_raw)
                if parent_match:
                    parent_menu_id = int(parent_match.group(1))
                    if cat_menu_id in cats_map and parent_menu_id in cats_map:
                        cats_map[cat_menu_id]['parent_id'] = cats_map[parent_menu_id]['id']
        final_cats = list(cats_map.values())
        logger.info(f"✅ تعداد {len(final_cats)} دسته‌بندی معتبر استخراج شد.")
        return final_cats
    except requests.RequestException as e:
        logger.error(f"❌ خطا در دریافت دسته‌بندی‌ها: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ خطای ناشناخته در پردازش دسته‌بندی‌ها: {e}")
        return None

# تابع استخراج زیرمجموعه‌ها (مستقیم یا allz)
def get_subcategories(all_cats, parent_id, allz=False):
    subs = [cat for cat in all_cats if cat['parent_id'] == parent_id]
    if allz:
        all_subs = subs.copy()
        for sub in subs:
            all_subs.extend(get_subcategories(all_cats, sub['id'], allz=True))
        return all_subs
    return subs

# تابع جدید برای نمایش درخت در لاگ
def print_category_tree(selected, all_cats, level=0):
    tree_log = []
    for cat in selected:
        tree_log.append('  ' * level + f"- {cat['name']} (ID: {cat['id']})")
        subs = get_subcategories(all_cats, cat['id'], allz=True)  # recursive برای نمایش عمق
        print_category_tree(subs, all_cats, level + 1)
    for line in tree_log:
        logger.info(line)

# تابع پارس فرمت جدید SELECTED_TREE (فیکس‌شده)
def parse_selected_tree(tree_str, source_categories):
    selected = []
    selected_ids = set()

    parts = tree_str.split(';')
    for part in parts:
        part = part.strip()
        if not part: continue
        # پارس با re برای جدا کردن mother_id:son_configs-sub_configs
        match = re.match(r'(\d+):?(.+?)-(.*)', part)
        if not match:
            logger.error(f"❌ فرمت نامعتبر: {part}")
            continue
        mid = int(match.group(1))
        son_configs = match.group(2).strip() if match.group(2) else 'all'  # اگر خالی باشه، 'all' فرض کن
        sub_configs = match.group(3).strip() if match.group(3) else 'all-allz'

        mother_cat = next((cat for cat in source_categories if cat['id'] == mid), None)
        if not mother_cat:
            logger.error(f"❌ ID مادر {mid} معتبر نیست.")
            continue
        selected.append(mother_cat)
        selected_ids.add(mid)
        logger.info(f"✅ شاخه مادر انتخاب‌شده: {mother_cat['name']} (ID: {mid})")

        # فرزندان
        if son_configs.lower() == 'all':
            chosen_sons = get_subcategories(source_categories, mid)
        else:
            try:
                son_ids = [int(s.strip()) for s in son_configs.split(',') if s.strip()]
                chosen_sons = [cat for cat in source_categories if cat['id'] in son_ids and cat['parent_id'] == mid]
            except ValueError as e:
                logger.error(f"❌ خطا در پارس فرزندان {son_configs}: {e}")
                chosen_sons = []

        selected.extend(chosen_sons)
        selected_ids.update(son['id'] for son in chosen_sons)
        logger.info(f"✅ فرزندان انتخاب‌شده برای {mother_cat['name']}: {[son['name'] for son in chosen_sons]} (تعداد: {len(chosen_sons)})")

        # زیرمجموعه‌ها (پارس گروه‌بندی با ( ) و +)
        sub_groups = re.split(r',(?![^(]*KATEX_INLINE_CLOSE)', sub_configs)
        for group in sub_groups:
            group = group.strip()
            if not group: continue
            if group.startswith('(') and group.endswith(')'):
                group = group[1:-1]
                sub_parts = group.split('+')
                for sub_part in sub_parts:
                    sub_part = sub_part.strip()
                    if '-' in sub_part:
                        sub_id_str, sub_type = sub_part.split('-', 1)
                        try:
                            sub_id = int(sub_id_str.strip())
                        except ValueError:
                            logger.error(f"❌ خطا در پارس زیرمجموعه ID {sub_id_str}")
                            continue
                        allz = sub_type.lower() == 'allz'
                        sub_cat = next((cat for cat in source_categories if cat['id'] == sub_id), None)
                        if sub_cat:
                            selected.append(sub_cat)
                            selected_ids.add(sub_id)
                            if allz:
                                allz_subs = get_subcategories(source_categories, sub_id, allz=True)
                                selected.extend(allz_subs)
                                selected_ids.update(s['id'] for s in allz_subs)
                                logger.info(f"✅ زیرمجموعه {sub_cat['name']} با allz: {len(allz_subs)} مورد اضافه شد.")
            else:
                if '-' in group:
                    sub_id_str, sub_type = group.split('-', 1)
                    try:
                        sub_id = int(sub_id_str.strip())
                    except ValueError:
                        logger.error(f"❌ خطا در پارس زیرمجموعه ID {sub_id_str}")
                        continue
                    allz = sub_type.lower() == 'allz'
                    sub_cat = next((cat for cat in source_categories if cat['id'] == sub_id), None)
                    if sub_cat:
                        selected.append(sub_cat)
                        selected_ids.add(sub_id)
                        if allz:
                            allz_subs = get_subcategories(source_categories, sub_id, allz=True)
                            selected.extend(allz_subs)
                            selected_ids.update(s['id'] for s in allz_subs)
                            logger.info(f"✅ زیرمجموعه {sub_cat['name']} با allz: {len(allz_subs)} مورد اضافه شد.")

    return selected

def get_selected_categories_flexible(source_categories):
    if not source_categories:
        logger.warning("⚠️ هیچ دسته‌بندی برای انتخاب موجود نیست.")
        return []

    selected = []

    try:
        # حالت تعاملی (local) – ورودی از کاربر
        main_categories = [cat for cat in source_categories if cat['parent_id'] is None or cat['parent_id'] == 0]
        logger.info("📋 لیست شاخه‌های مادر:")
        for i, cat in enumerate(main_categories):
            logger.info(f"{i+1}: {cat['name']} (ID: {cat['id']})")

        while True:
            mother_input = input("ID شاخه مادر مورد نظر را وارد کنید (مثل 4285 یا چند تا با کاما مثل 4285,1234) یا 'done' برای پایان: ").strip().lower()
            if mother_input == 'done':
                break
            mother_ids = [int(x.strip()) for x in mother_input.split(',') if x.strip()]

            for mid in mother_ids:
                mother_cat = next((cat for cat in main_categories if cat['id'] == mid), None)
                if not mother_cat:
                    logger.error(f"❌ ID {mid} معتبر نیست.")
                    continue

                logger.info(f"✅ شاخه مادر انتخاب‌شده: {mother_cat['name']} (ID: {mid})")
                selected.append(mother_cat)

                # انتخاب فرزندان (تمام یا بعضی)
                son_input = input(f"برای {mother_cat['name']}: 'all' برای تمام فرزندان، یا ID فرزندان با کاما (مثل 16777,5678) یا خالی برای هیچ: ").strip().lower()
                if son_input == 'all':
                    chosen_sons = get_subcategories(source_categories, mid)
                elif son_input:
                    son_ids = [int(x.strip()) for x in son_input.split(',') if x.strip()]
                    chosen_sons = [cat for cat in source_categories if cat['id'] in son_ids and cat['parent_id'] == mid]
                else:
                    chosen_sons = []

                selected.extend(chosen_sons)
                logger.info(f"✅ فرزندان انتخاب‌شده برای {mother_cat['name']}: {[son['name'] for son in chosen_sons]}")

                # برای هر فرزند، انتخاب زیرمجموعه‌ها (تمام/بعضی + allz)
                for son in chosen_sons:
                    sub_input = input(f"برای فرزند {son['name']}: 'all:allz' برای تمام زیرمجموعه‌ها با عمق، 'all' برای تمام مستقیم، IDها با کاما (مثل sub1,sub2:allz) یا خالی برای هیچ: ").strip().lower()
                    allz = ':allz' in sub_input
                    sub_input = sub_input.replace(':allz', '')

                    if sub_input == 'all':
                        chosen_subs = get_subcategories(source_categories, son['id'], allz=allz)
                    elif sub_input:
                        sub_ids = [int(x.strip()) for x in sub_input.split(',') if x.strip()]
                        chosen_subs = [cat for cat in source_categories if cat['id'] in sub_ids and cat['parent_id'] == son['id']]
                        if allz:
                            for sub in chosen_subs.copy():
                                chosen_subs.extend(get_subcategories(source_categories, sub['id'], allz=True))
                    else:
                        chosen_subs = []

                    selected.extend(chosen_subs)
                    logger.info(f"✅ زیرمجموعه‌های انتخاب‌شده برای {son['name']}: {[sub['name'] for sub in chosen_subs]} (allz: {allz})")
    except EOFError:
        # حالت غیرتعاملی (GitHub Actions) – استفاده از SELECTED_TREE یا پیش‌فرض
        logger.warning("⚠️ محیط غیرتعاملی. استفاده از SELECTED_TREE یا پیش‌فرض.")

        default_tree = "4285:all-allz;1234:far1-all-allz;5678:far3-zir1-allz,far4-(zir2-allz+zir3-allz+zir5-allz)"  # پیش‌فرض مثال شما
        tree_str = os.environ.get('SELECTED_TREE', default_tree)
        logger.info(f"استفاده از SELECTED_TREE: {tree_str}")

        selected = parse_selected_tree(tree_str, source_categories)

    if not selected:
        logger.error("❌ هیچ دسته‌ای انتخاب نشد.")
        return []

    selected_ids = [cat['id'] for cat in selected]
    logger.info(f"✅ دسته‌بندی‌های نهایی انتخاب‌شده: {[c['name'] for c in selected]} (تعداد: {len(selected)})")
    logger.info("✅ ساختار درختی دسته‌بندی‌های انتخاب‌شده:")
    print_category_tree(selected, source_categories)  # نمایش درخت
    return selected

def get_all_category_ids(categories, all_cats, selected_ids):
    return selected_ids  # IDها از selected استخراج شدن

# ==============================================================================
# --- تابع اصلی (بدون زمان‌بندی) ---
# ==============================================================================
def main():
    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session:
        logger.error("❌ لاگین به پنل eways انجام نشد. برنامه خاتمه می‌یابد.")
        return

    all_cats = get_and_parse_categories(session)
    if not all_cats:
        logger.error("❌ دسته‌بندی‌ها بارگذاری نشد.")
        return
    logger.info(f"✅ مرحله 1: بارگذاری دسته‌بندی‌ها کامل شد. تعداد: {len(all_cats)}")

    filtered_categories = get_selected_categories_flexible(all_cats)
    if not filtered_categories:
        logger.info("✅ هیچ دسته‌بندی انتخاب نشد. برنامه خاتمه می‌یابد.")
        return
    logger.info(f"✅ مرحله 2: انتخاب دسته‌بندی‌ها کامل شد. تعداد انتخاب‌شده: {len(filtered_categories)}")

    selected_ids = [cat['id'] for cat in filtered_categories]
    all_relevant_ids = get_all_category_ids(filtered_categories, all_cats, selected_ids)
    logger.info(f"✅ مرحله 3: استخراج IDهای مرتبط کامل شد. تعداد: {len(all_relevant_ids)}")

    relevant_cats = [cat for cat in all_cats if cat['id'] in all_relevant_ids]
    logger.info(f"✅ مرحله 4: استخراج دسته‌های مرتبط کامل شد. تعداد: {len(relevant_cats)}")

    category_mapping = transfer_categories_to_wc(relevant_cats)
    if not category_mapping:
        logger.error("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد. برنامه خاتمه می‌یابد.")
        return
    logger.info(f"✅ مرحله 5: انتقال دسته‌بندی‌ها کامل شد. تعداد نگاشت‌شده: {len(category_mapping)}")

    # بارگذاری کش
    cached_products = load_cache()

    # استخراج محصولات جدید
    new_products = get_all_products(session, filtered_categories, all_cats)
    logger.info(f"✅ مرحله 6: استخراج محصولات کامل شد. تعداد استخراج‌شده: {len(new_products)}")

    # ادغام با کش و شناسایی تغییرات
    updated_products = {}
    changed_count = 0
    for p in new_products:
        pid = p['id']
        if pid in cached_products and cached_products[pid]['price'] == p['price'] and cached_products[pid]['stock'] == p['stock'] and cached_products[pid]['specs'] == p['specs']:
            # بدون تغییر
            updated_products[pid] = cached_products[pid]
        else:
            # تغییر کرده یا جدید
            updated_products[pid] = p
            changed_count += 1
    logger.info(f"✅ مرحله 7: ادغام با کش کامل شد. تعداد محصولات تغییرشده/جدید برای ارسال: {changed_count}")

    # ذخیره کش جدید
    save_cache(updated_products)

    stats = {'created': 0, 'updated': 0, 'failed': 0, 'lock': Lock()}
    logger.info(f"\n🚀 شروع پردازش و ارسال {changed_count} محصول (تغییرشده/جدید) به ووکامرس...")
    with ThreadPoolExecutor(max_workers=3) as executor:
        args_list = [(p, stats, category_mapping) for p in updated_products.values() if p['id'] not in cached_products or updated_products[p['id']] != cached_products.get(p['id'])]
        list(tqdm(executor.map(process_product_wrapper, args_list), total=changed_count, desc="ارسال محصولات"))

    logger.info("\n===============================")
    logger.info(f"📦 محصولات پردازش شده: {changed_count}")
    logger.info(f"🟢 ایجاد شده: {stats['created']}")
    logger.info(f"🔵 آپدیت شده: {stats['updated']}")
    logger.info(f"🔴 شکست‌خورده: {stats['failed']}")
    logger.info("===============================\nتمام!")

if __name__ == "__main__":
    main()
