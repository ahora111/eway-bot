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
# --- توابع جدید برای پردازش قوانین انتخاب ---
# ==============================================================================
def get_all_descendants(parent_id, all_cats_map):
    """تمام نوادگان (زیرمجموعه‌های تمام سطوح) یک دسته را به صورت بازگشتی پیدا می‌کند."""
    descendants = set()
    children = [cat['id'] for cat in all_cats_map.values() if cat.get('parent_id') == parent_id]
    for child_id in children:
        descendants.add(child_id)
        descendants.update(get_all_descendants(child_id, all_cats_map))
    return descendants

def process_selection_rules(rule_string, all_cats, logger_instance):
    """
    رشته قوانین را پردازش کرده و دو لیست ID مجزا برمی‌گرداند.
    """
    structure_ids = set()
    product_ids = set()
    all_cats_map = {cat['id']: cat for cat in all_cats}

    for rule in rule_string.split('|'):
        rule = rule.strip()
        if not rule or ':' not in rule:
            continue

        try:
            parent_id_str, selections_str = rule.split(':', 1)
            parent_id = int(parent_id_str.strip())

            if parent_id not in all_cats_map:
                logger_instance.warning(f"⚠️ شناسه والد {parent_id} در قانون '{rule}' یافت نشد. رد شدن...")
                continue
            
            structure_ids.add(parent_id)

            for sel in selections_str.split(','):
                sel = sel.strip()
                if not sel: continue

                if sel == 'all':
                    direct_children = [cat['id'] for cat in all_cats if cat.get('parent_id') == parent_id]
                    structure_ids.update(direct_children)
                    product_ids.update(direct_children)
                elif sel == 'allz':
                    product_ids.add(parent_id)
                elif sel == 'all-allz':
                    product_ids.add(parent_id)
                    descendants = get_all_descendants(parent_id, all_cats_map)
                    structure_ids.update(descendants)
                    product_ids.update(descendants)
                else:
                    match = re.match(r'^(\d+)-(.+)$', sel)
                    if not match:
                        logger_instance.warning(f"⚠️ فرمت انتخاب '{sel}' در قانون '{rule}' نامعتبر است.")
                        continue
                    
                    child_id, command = int(match.group(1)), match.group(2)
                    if child_id not in all_cats_map:
                        logger_instance.warning(f"⚠️ شناسه فرزند {child_id} در قانون '{rule}' یافت نشد.")
                        continue

                    structure_ids.add(child_id)
                    if command == 'allz':
                        product_ids.add(child_id)
                    elif command == 'all-allz':
                        product_ids.add(child_id)
                        descendants = get_all_descendants(child_id, all_cats_map)
                        structure_ids.update(descendants)
                        product_ids.update(descendants)
                    else:
                        logger_instance.warning(f"⚠️ دستور '{command}' برای فرزند {child_id} نامعتبر است.")
        except Exception as e:
            logger_instance.error(f"❌ خطای جدی در پردازش قانون '{rule}': {e}")

    return list(structure_ids), list(product_ids)

# ==============================================================================
# --- تنظیمات لاگینگ ---
# ==============================================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# فایل لاگ با جزئیات DEBUG
file_handler = RotatingFileHandler('app.log', maxBytes=2*1024*1024, backupCount=5, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

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

CACHE_FILE = 'products_cache.json'

# ==============================================================================
# --- توابع کمکی و اصلی ---
# ==============================================================================
def login_eways(username, password):
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    })
    session.verify = False
    login_url = f"{BASE_URL}/User/Login"
    payload = {"UserName": username, "Password": password, "RememberMe": "true"}
    logger.info("⏳ در حال لاگین به پنل eways ...")
    try:
        resp = session.post(login_url, data=payload, timeout=30)
        resp.raise_for_status()
        if 'Aut' in session.cookies:
            logger.info("✅ لاگین موفق!")
            return session
        else:
            logger.error("❌ کوکی Aut دریافت نشد. لاگین ناموفق یا کپچا فعال است.")
            return None
    except requests.RequestException as e:
        logger.error(f"❌ لاگین ناموفق! خطای شبکه: {e}")
        return None

def get_and_parse_categories(session):
    logger.info(f"⏳ دریافت دسته‌بندی‌ها از سایت مبدا...")
    try:
        response = session.get(SOURCE_CATS_API_URL, timeout=30)
        response.raise_for_status()
        
        # تلاش برای پارس JSON
        try:
            data = response.json()
            logger.debug("پاسخ JSON است. در حال پردازش...")
            final_cats = []
            for c in data:
                real_id_match = re.search(r'/Store/List/(\d+)', c.get('url', ''))
                real_id = int(real_id_match.group(1)) if real_id_match else c.get('id')
                final_cats.append({"id": real_id, "name": c.get('name', '').strip(), "parent_id": c.get('parent_id')})
            logger.info(f"✅ تعداد {len(final_cats)} دسته‌بندی از JSON استخراج شد.")
            return final_cats
        except json.JSONDecodeError:
            logger.warning("⚠️ پاسخ JSON نبود. تلاش برای پارس HTML...")

        # پلن B: پارس کردن HTML
        soup = BeautifulSoup(response.text, 'lxml')
        all_menu_items = soup.select("li[id^='menu-item-']")
        if not all_menu_items:
            logger.error("❌ هیچ آیتم دسته‌بندی در HTML پیدا نشد.")
            return []
        
        cats_map = {}
        for item in all_menu_items:
            # ... (منطق پیچیده پارس HTML شما) ...
            a_tag = item.find('a', recursive=False) or item.select_one("a")
            if not a_tag or not a_tag.get('href'): continue
            name = a_tag.text.strip()
            real_id_match = re.search(r'/Store/List/(\d+)', a_tag['href'])
            real_id = int(real_id_match.group(1)) if real_id_match else None
            
            cat_id_raw = item.get('id', '')
            match = re.search(r'(\d+)', cat_id_raw)
            if not match: continue
            cat_menu_id = int(match.group(1))
            
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
        logger.info(f"✅ تعداد {len(final_cats)} دسته‌بندی معتبر از HTML استخراج شد.")
        return final_cats

    except requests.RequestException as e:
        logger.error(f"❌ خطا در دریافت دسته‌بندی‌ها: {e}")
        return None

def get_products_from_category_page(session, category_id, max_pages=100):
    # ... (بدون تغییر) ...
    all_products_in_category = []
    # ...
    return all_products_in_category
# (کد تابع get_products_from_category_page و get_product_details را برای خلاصه‌سازی حذف کردم، آنها بدون تغییر باقی می‌مانند)
# ...
def transfer_categories_to_wc(source_categories, all_cats_from_source):
    # ... (بدون تغییر) ...
    return {} # source_to_wc_id_map
# (کد توابع ووکامرس بدون تغییر باقی می‌مانند)
# ...
def load_cache():
    # ... (بدون تغییر) ...
    return {}
def save_cache(products):
    # ... (بدون تغییر) ...
    pass

def process_product_wrapper(args):
    # ... (بدون تغییر) ...
    pass
# ==============================================================================
# --- تابع اصلی (نسخه نهایی و بهبودیافته) ---
# ==============================================================================
def main():
    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session:
        return

    all_cats = get_and_parse_categories(session)
    if not all_cats:
        return
    logger.info(f"✅ مرحله ۱: بارگذاری کل دسته‌بندی‌ها کامل شد. تعداد: {len(all_cats)}")
    
    # ------------------------------------------------------------------------------------
    # --- ابزار کمکی برای یافتن ID صحیح ---
    # برای پیدا کردن ID های صحیح، این بخش را از کامنت خارج کرده و یک بار برنامه را اجرا کنید.
    # لیست کامل در فایل app.log ذخیره خواهد شد.
    # ------------------------------------------------------------------------------------
    # logger.info("="*20 + " لیست کامل دسته‌بندی‌های یافت شده " + "="*20)
    # for cat in sorted(all_cats, key=lambda x: x['name']):
    #     logger.info(f"نام: {cat['name']:<40} | شناسه: {cat['id']:<10} | شناسه والد: {cat.get('parent_id')}")
    # logger.info("="*80)
    # return # برای توقف برنامه بعد از نمایش لیست
    # ------------------------------------------------------------------------------------

    # --- تعریف و پردازش قوانین انتخاب ---
    # مقادیر زیر را بر اساس لاگ بالا و ID های صحیح، ویرایش کنید.
    # مثال: اگر شناسه "گوشی موبایل" 4286 است، به جای 2045 از آن استفاده کنید.

    
    
    SELECTED_IDS_STRING = "1582:14548-allz,1584-all-allz|16777:all-allz|4882:all-allz|16778:22570-all-allz"


    
    # پردازش قوانین برای گرفتن دو لیست مجزا
    structure_cat_ids, product_cat_ids = process_selection_rules(SELECTED_IDS_STRING, all_cats, logger)
    
    # ایجاد نقشه از ID به نام برای لاگ‌نویسی بهتر
    cat_name_map = {cat['id']: cat['name'] for cat in all_cats}
    
    structure_cat_names = [cat_name_map.get(cat_id, f'ID ناشناخته:{cat_id}') for cat_id in structure_cat_ids]
    product_cat_names = [cat_name_map.get(cat_id, f'ID ناشناخته:{cat_id}') for cat_id in product_cat_ids]
    
    logger.info(f"✅ دسته‌بندی‌های ساختاری برای انتقال: {structure_cat_names}")
    logger.info(f"✅ دسته‌بندی‌های محصول برای استخراج: {product_cat_names}")

    # --- انتقال دسته‌های ساختاری به ووکامرس ---
    cats_for_wc_transfer = [cat for cat in all_cats if cat['id'] in structure_cat_ids]
    category_mapping = transfer_categories_to_wc(cats_for_wc_transfer, all_cats) # این تابع باید مثل قبل باشد
    if not category_mapping:
        logger.error("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد. برنامه خاتمه می‌یابد.")
        return
    logger.info(f"✅ مرحله ۲: انتقال دسته‌بندی‌های ساختاری کامل شد.")

    # --- استخراج محصولات و ادامه فرآیند ---
    cached_products = load_cache()
    
    all_products = {}
    logger.info("\n⏳ شروع فرآیند جمع‌آوری تمام محصولات...")
    for cat_id in tqdm(product_cat_ids, desc="دریافت محصولات"):
        products_in_cat = get_products_from_category_page(session, cat_id) # این تابع باید مثل قبل باشد
        for product in products_in_cat:
            all_products[product['id']] = product
    
    new_products_list = list(all_products.values())
    logger.info(f"\n✅ مرحله ۳: استخراج محصولات کامل شد. تعداد کل محصولات یکتا: {len(new_products_list)}")

    products_to_send = []
    updated_cache_data = {}
    for p in new_products_list:
        pid = str(p['id']) # اطمینان از اینکه کلیدها رشته‌ای هستند
        cached_p = cached_products.get(pid)
        if not cached_p or cached_p.get('price') != p.get('price') or cached_p.get('specs') != p.get('specs'):
            products_to_send.append(p)
        updated_cache_data[pid] = p
        
    logger.info(f"✅ مرحله ۴: مقایسه با کش کامل شد. تعداد محصولات تغییرکرده/جدید برای ارسال: {len(products_to_send)}")
    save_cache(updated_cache_data)

    if not products_to_send:
        logger.info("🎉 هیچ محصول جدید یا تغییرکرده‌ای برای ارسال وجود ندارد. کار تمام شد!")
        return

    stats = {'created': 0, 'updated': 0, 'failed': 0, 'lock': Lock()}
    logger.info(f"\n🚀 شروع پردازش و ارسال {len(products_to_send)} محصول به ووکامرس...")
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        args_list = [(p, stats, category_mapping) for p in products_to_send]
        list(tqdm(executor.map(process_product_wrapper, args_list), total=len(products_to_send), desc="ارسال محصولات")) # این تابع باید مثل قبل باشد

    logger.info("\n===============================")
    logger.info(f"📦 خلاصه عملیات:")
    logger.info(f"🟢 ایجاد شده: {stats['created']}")
    logger.info(f"🔵 آپدیت شده: {stats['updated']}")
    logger.info(f"🔴 شکست‌خورده: {stats['failed']}")
    logger.info("===============================\nتمام!")

if __name__ == "__main__":
    main()
