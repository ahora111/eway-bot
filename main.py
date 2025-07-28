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
# --- رشته انتخاب IDها (اینجا IDها رو وارد کن - مثال فعلی) ---
# ==============================================================================
SELECTED_IDS_STRING = "1582:14548-allz,1584-all-allz|16777:all-allz|4882:all-allz|16778:22570-all-allz"

# ==============================================================================
# --- توابع انتخاب منعطف با SELECTED_IDS_STRING ---
# ==============================================================================
def parse_selected_ids_string(selected_ids_string):
    """
    تبدیل رشته SELECTED_IDS_STRING به لیست انتخاب با ساختار:
    [
        {"parent_id": 1582, "selections": [
            {"id": 14548, "type": "only_products"},
            {"id": 1584, "type": "all_subcats_and_products"}
        ]},
        ...
    ]
    """
    result = []
    for part in selected_ids_string.split('|'):
        part = part.strip()
        if not part or ':' not in part:
            continue
        parent_id_str, children_str = part.split(':', 1)
        parent_id = int(parent_id_str.strip())
        selections = []
        for sel in children_str.split(','):
            sel = sel.strip()
            if not sel:
                continue
            if sel == 'all':
                selections.append({"id": parent_id, "type": "all_subcats"})
            elif sel == 'allz':
                selections.append({"id": parent_id, "type": "only_products"})
            elif sel == 'all-allz':
                selections.append({"id": parent_id, "type": "all_subcats_and_products"})
            else:
                sub_parts = sel.split('-')
                sub_id_str = sub_parts[0]
                try:
                    sub_id = int(sub_id_str)
                except ValueError:
                    logger.warning(f"⚠️ زیر ID نامعتبر: {sub_id_str}")
                    continue
                if len(sub_parts) > 1:
                    config = '-'.join(sub_parts[1:]).lower()
                    if config == 'allz':
                        selections.append({"id": sub_id, "type": "only_products"})
                    elif config == 'all-allz':
                        selections.append({"id": sub_id, "type": "all_subcats_and_products"})
                    elif config == 'all':
                        selections.append({"id": sub_id, "type": "all_subcats"})
                    else:
                        logger.warning(f"⚠️ config نامعتبر: {config}")
                else:
                    logger.warning(f"⚠️ config برای {sub_id} مشخص نشده.")
        result.append({"parent_id": parent_id, "selections": selections})
    return result

def get_direct_subcategories(parent_id, all_cats):
    """زیرمجموعه‌های مستقیم یک دسته را برمی‌گرداند"""
    return [cat for cat in all_cats if cat['parent_id'] == parent_id]

def get_all_subcategories(parent_id, all_cats):
    """همه زیرمجموعه‌های یک دسته (بازگشتی)"""
    result = []
    direct = get_direct_subcategories(parent_id, all_cats)
    result.extend(direct)
    for sub in direct:
        result.extend(get_all_subcategories(sub['id'], all_cats))
    return result

def get_selected_category_ids(parsed_selection, all_cats):
    """
    خروجی: لیست ID دسته‌هایی که باید محصولاتشان جمع‌آوری شود
    """
    selected_ids = set()
    for block in parsed_selection:
        parent_id = block['parent_id']
        selected_ids.add(parent_id)  # همیشه اصلی رو اضافه کن
        for sel in block['selections']:
            sel_id = sel['id']
            selected_ids.add(sel_id)
            if sel['type'] == 'all_subcats':
                # all: همه زیرمجموعه‌های مستقیم
                direct_subs = get_direct_subcategories(sel_id, all_cats)
                for ds in direct_subs:
                    selected_ids.add(ds['id'])
            elif sel['type'] == 'all_subcats_and_products':
                # all-allz: همه زیرمجموعه‌های مستقیم + محصولات (زیرشاخه‌ها رو اضافه کن)
                direct_subs = get_direct_subcategories(sel_id, all_cats)
                for ds in direct_subs:
                    selected_ids.add(ds['id'])
            elif sel['type'] == 'only_products':
                # allz: فقط محصولات مستقیم (فقط ID رو نگه دار)
                pass
    return list(selected_ids)

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

def get_selected_categories_flexible(source_categories):
    if not source_categories:
        logger.warning("⚠️ هیچ دسته‌بندی برای انتخاب موجود نیست.")
        return []

    # پارس string (با | جدا شده برای گروه‌های اصلی)
    groups = SELECTED_IDS_STRING.split('|')
    all_selected_ids = set()
    selected = []

    for group in groups:
        if not group.strip(): continue
        parts = group.split(':')
        if len(parts) != 2: 
            logger.warning(f"⚠️ فرمت اشتباه: {group}")
            continue
        
        main_id_str = parts[0].strip()
        try:
            main_id = int(main_id_str)  # فرض ID عددی
        except ValueError:
            logger.warning(f"⚠️ ID نامعتبر: {main_id_str}")
            continue
        
        # پیدا کردن دسته اصلی
        main_cat = next((c for c in source_categories if c['id'] == main_id), None)
        if not main_cat:
            logger.warning(f"⚠️ ID اصلی {main_id} پیدا نشد.")
            continue
        selected.append(main_cat)
        all_selected_ids.add(main_id)
        
        # پارس تنظیمات (مثل 14548-allz,1584-all-allz)
        settings = parts[1].split(',')
        for setting in settings:
            setting = setting.strip()
            if not setting: continue
            
            # استخراج زیرشاخه‌ها (مثل 14548-allz)
            sub_parts = setting.split('-')
            sub_id_str = sub_parts[0].strip()  # مثل 14548
            try:
                sub_id = int(sub_id_str)
            except ValueError:
                logger.warning(f"⚠️ زیر ID نامعتبر: {sub_id_str}")
                continue
            
            # چک اگر زیرشاخه parent_id == main_id باشه
            sub_cat = next((c for c in source_categories if c['id'] == sub_id and c['parent_id'] == main_id), None)
            if not sub_cat:
                logger.warning(f"⚠️ زیر ID {sub_id} زیر {main_id} پیدا نشد.")
                continue
            selected.append(sub_cat)
            all_selected_ids.add(sub_id)
            
            # چک برای تنظیمات مثل allz یا all-allz
            if len(sub_parts) > 1:
                config = '-'.join(sub_parts[1:]).lower()
                if config == 'all':
                    # all: همه زیرمجموعه‌های مستقیم
                    direct_subs = [c for c in source_categories if c['parent_id'] == sub_id]
                    for ds in direct_subs:
                        if ds['id'] not in all_selected_ids:
                            selected.append(ds)
                            all_selected_ids.add(ds['id'])
                elif config == 'allz':
                    # allz: فقط محصولات مستقیم
                    pass  # محصولات از sub_id گرفته می‌شن
                elif config == 'all-allz':
                    # all-allz: همه زیرمجموعه‌های مستقیم + محصولات
                    direct_subs = [c for c in source_categories if c['parent_id'] == sub_id]
                    for ds in direct_subs:
                        if ds['id'] not in all_selected_ids:
                            selected.append(ds)
                            all_selected_ids.add(ds['id'])
            
            # اگر all یا allz در تنظیم باشه (برای زیرشاخه اصلی)
            if 'all' in setting or 'allz' in setting:
                sub_ids = get_all_category_ids([sub_cat], source_categories, [sub_id])
                for s_id in sub_ids:
                    if s_id not in all_selected_ids:
                        s_cat = next((c for c in source_categories if c['id'] == s_id), None)
                        if s_cat:
                            selected.append(s_cat)
                            all_selected_ids.add(s_id)
        
        # برای main اگر all-allz باشه
        if 'all' in parts[1] or 'allz' in parts[1]:
            main_sub_ids = get_all_category_ids([main_cat], source_categories, [main_id])
            for ms_id in main_sub_ids:
                if ms_id not in all_selected_ids:
                    ms_cat = next((c for c in source_categories if c['id'] == ms_id), None)
                    if ms_cat:
                        selected.append(ms_cat)
                        all_selected_ids.add(ms_id)

    logger.info(f"✅ دسته‌بندی‌های انتخاب‌شده: {[c['name'] for c in selected]} (IDها: {list(all_selected_ids)})")
    return selected

def get_all_category_ids(categories, all_cats, selected_ids):
    all_ids = set(selected_ids)
    to_process = list(selected_ids)
    while to_process:
        current_id = to_process.pop()
        for cat in all_cats:
            if cat['parent_id'] == current_id:
                all_ids.add(cat['id'])
                to_process.append(cat['id'])
    return list(all_ids)

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
