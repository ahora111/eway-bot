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
# برای جلوگیری از اضافه شدن چندباره هندلر در اجراهای مکرر (مفید در نوت‌بوک‌ها)
if not logger.handlers:
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
# غیرفعال کردن هشدارهای SSL
requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# --- توابع اصلی برنامه ---
# ==============================================================================
def login_eways(username, password):
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
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
    logger.info("⏳ دریافت دسته‌بندی‌ها از سایت مبدا...")
    try:
        response = session.get(SOURCE_CATS_API_URL, timeout=30)
        response.raise_for_status()
        try:
            data = response.json()
            # ... منطق پارس JSON ...
            return final_cats
        except json.JSONDecodeError:
            logger.warning("⚠️ پاسخ JSON نبود. تلاش برای پارس HTML...")
        soup = BeautifulSoup(response.text, 'lxml')
        # ... منطق پیچیده پارس HTML شما ...
        # ... (کد این بخش بدون تغییر باقی می‌ماند)
        return final_cats
    except requests.RequestException as e:
        logger.error(f"❌ خطا در دریافت دسته‌بندی‌ها: {e}")
        return None

# تابع جدید برای بررسی اولیه اتصال
def check_wc_connection():
    """یک درخواست ساده برای بررسی اتصال و کلیدهای API ووکامرس ارسال می‌کند."""
    logger.info("⏳ در حال بررسی اتصال به ووکامرس...")
    try:
        res = requests.get(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False, timeout=15)
        if res.status_code == 401:
            logger.error("❌ اتصال به ووکامرس ناموفق: خطای 401 Unauthorized. لطفاً کلیدهای API (Consumer Key/Secret) را بررسی کنید.")
            return False
        res.raise_for_status()
        logger.info("✅ اتصال به ووکامرس موفقیت‌آمیز است.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ اتصال به ووکامرس ناموفق: خطای شبکه. لطفاً آدرس API ({WC_API_URL}) را بررسی کنید. خطا: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ خطای ناشناخته در هنگام بررسی اتصال به ووکامرس: {e}")
        return False

# تابع اصلاح شده برای انتقال دسته‌ها با لاگ‌نویسی بهتر
def transfer_categories_to_wc(source_categories, all_cats_from_source):
    logger.info(f"\n⏳ شروع انتقال {len(source_categories)} دسته‌بندی به ووکامرس...")
    source_cat_map = {cat['id']: cat for cat in all_cats_from_source}
    sorted_cats = sorted(source_categories, key=lambda c: (source_cat_map.get(c.get('parent_id'), {}).get('name', ''), c['name']))
    source_to_wc_id_map = {}
    
    for cat in tqdm(sorted_cats, desc="انتقال دسته‌ها"):
        name = cat["name"].strip()
        source_parent_id = cat.get("parent_id")
        wc_parent_id = source_to_wc_id_map.get(source_parent_id, 0)
        
        logger.debug(f"  - پردازش '{name}' (ID: {cat['id']}) با والد WC ID: {wc_parent_id}")
        
        try:
            res_check = requests.get(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), 
                                     params={"search": name, "parent": wc_parent_id}, verify=False, timeout=20)
            res_check.raise_for_status()
            existing_cats = res_check.json()
            exact_match = next((wc_cat for wc_cat in existing_cats if wc_cat['name'].strip() == name and wc_cat['parent'] == wc_parent_id), None)
            
            if exact_match:
                source_to_wc_id_map[cat["id"]] = exact_match["id"]
                logger.debug(f"    -> دسته '{name}' وجود دارد. استفاده از WC ID: {exact_match['id']}")
                continue 

            logger.debug(f"    -> دسته '{name}' وجود ندارد. تلاش برای ساخت...")
            data = {"name": name, "parent": wc_parent_id}
            res = requests.post(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False, timeout=20)
            
            if res.status_code in [200, 201]:
                new_id = res.json()["id"]
                source_to_wc_id_map[cat["id"]] = new_id
                logger.debug(f"    -> ✅ دسته با موفقیت ساخته شد. WC ID جدید: {new_id}")
            else:
                error_data = res.json()
                if error_data.get("code") == "term_exists" and error_data.get("data", {}).get("resource_id"):
                    existing_id = error_data["data"]["resource_id"]
                    source_to_wc_id_map[cat["id"]] = existing_id
                    logger.warning(f"    -> دسته '{name}' به دلیل 'term_exists' با ID موجود {existing_id} مپ شد.")
                else:
                    logger.error(f"❌ خطا در ساخت دسته '{name}': {res.text}")
        except Exception as e:
            logger.error(f"❌ خطای جدی در حین پردازش دسته '{name}': {e}")
            return None # شکست خوردن تابع

    logger.info(f"✅ انتقال دسته‌بندی‌ها کامل شد. تعداد نگاشت‌شده: {len(source_to_wc_id_map)}")
    return source_to_wc_id_map

# ... بقیه توابع (get_product_details, get_products_from_category_page, ووکامرس, کش و ...)
# ... این توابع باید از کد قبلی شما کپی شوند و بدون تغییر باقی می‌مانند.
# ...
# ==============================================================================
# --- تابع اصلی (نسخه نهایی) ---
# ==============================================================================
def main():
    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session:
        return

    all_cats = get_and_parse_categories(session)
    if not all_cats:
        return
    logger.info(f"✅ مرحله ۱: بارگذاری کل دسته‌بندی‌ها کامل شد. تعداد: {len(all_cats)}")

    # مرحله جدید: بررسی اولیه اتصال به ووکامرس
    if not check_wc_connection():
        logger.error("برنامه به دلیل عدم امکان اتصال به ووکامرس خاتمه یافت.")
        return

    # --- تعریف و پردازش قوانین انتخاب ---
    SELECTED_IDS_STRING = "1582:14548-allz,1584-all-allz|16777:all-allz|4882:all-allz|16778:22570-all-allz"
    
    structure_cat_ids, product_cat_ids = process_selection_rules(SELECTED_IDS_STRING, all_cats, logger)
    
    cat_name_map = {cat['id']: cat['name'] for cat in all_cats}
    structure_cat_names = [cat_name_map.get(cat_id, f'ID ناشناخته:{cat_id}') for cat_id in sorted(list(structure_cat_ids))]
    product_cat_names = [cat_name_map.get(cat_id, f'ID ناشناخته:{cat_id}') for cat_id in sorted(list(product_cat_ids))]
    
    logger.info(f"✅ دسته‌بندی‌های ساختاری برای انتقال: {structure_cat_names}")
    logger.info(f"✅ دسته‌بندی‌های محصول برای استخراج: {product_cat_names}")

    # --- انتقال دسته‌های ساختاری به ووکامرس ---
    cats_for_wc_transfer = [cat for cat in all_cats if cat['id'] in structure_cat_ids]
    category_mapping = transfer_categories_to_wc(cats_for_wc_transfer, all_cats) 
    
    if not category_mapping:
        logger.error("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد. برنامه خاتمه می‌یابد.")
        return
    logger.info(f"✅ مرحله ۲: انتقال دسته‌بندی‌های ساختاری کامل شد.")

    # --- استخراج محصولات و ادامه فرآیند ---
    # ... بقیه کد شما از این بخش به بعد بدون تغییر است ...
    # ... (فراخوانی get_all_products یا حلقه روی product_cat_ids, مقایسه با کش، ارسال به ووکامرس)
    # ...

if __name__ == "__main__":
    main()
