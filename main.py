import requests
import os
import re
import time
import json
import random
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from threading import Lock, Thread
from queue import Queue
import logging
from logging.handlers import RotatingFileHandler
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type
from collections import defaultdict
import urllib3

# غیرفعال کردن هشدارهای مربوط به SSL Insecure
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# --- بخش ۱: توابع انتخاب منعطف دسته‌بندی‌ها ---
# این بخش به شما اجازه می‌دهد با یک رشته متنی، دسته‌بندی‌های مورد نظر خود را
# به صورت بسیار دقیق انتخاب کنید.
# ==============================================================================
def parse_selected_ids_string(selected_ids_string):
    """رشته انتخاب دسته‌بندی را به یک ساختار داده قابل فهم تبدیل می‌کند."""
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
            if not sel: continue
            if sel == 'all':
                selections.append({"id": parent_id, "type": "all_subcats"})
            elif sel == 'allz':
                selections.append({"id": parent_id, "type": "only_products"})
            elif sel == 'all-allz':
                selections.append({"id": parent_id, "type": "all_subcats_and_products"})
            elif re.match(r'^\d+-allz$', sel):
                sub_id = int(sel.split('-')[0])
                selections.append({"id": sub_id, "type": "only_products"})
            elif re.match(r'^\d+-all-allz$', sel):
                sub_id = int(sel.split('-')[0])
                selections.append({"id": sub_id, "type": "all_subcats_and_products"})
        result.append({"parent_id": parent_id, "selections": selections})
    return result

def get_direct_subcategories(parent_id, all_cats):
    """تمام زیرشاخه‌های مستقیم یک دسته را برمی‌گرداند."""
    return [cat['id'] for cat in all_cats if cat.get('parent_id') == parent_id]

def get_all_subcategories(parent_id, all_cats):
    """تمام زیرشاخه‌های یک دسته (مستقیم و غیرمستقیم) را به صورت بازگشتی پیدا می‌کند."""
    result = []
    direct = get_direct_subcategories(parent_id, all_cats)
    result.extend(direct)
    for sub_id in direct:
        result.extend(get_all_subcategories(sub_id, all_cats))
    return result

def get_selected_categories_according_to_selection(parsed_selection, all_cats):
    """بر اساس ساختار داده تولید شده، لیست نهایی آی‌دی دسته‌بندی‌ها را برمی‌گرداند."""
    selected_ids = set()
    for block in parsed_selection:
        parent_id = block['parent_id']
        for sel in block['selections']:
            cat_id_to_process = sel['id']
            if sel['type'] == 'all_subcats':
                selected_ids.add(cat_id_to_process)
                selected_ids.update(get_direct_subcategories(cat_id_to_process, all_cats))
            elif sel['type'] == 'only_products':
                selected_ids.add(cat_id_to_process)
            elif sel['type'] == 'all_subcats_and_products':
                selected_ids.add(cat_id_to_process)
                selected_ids.update(get_all_subcategories(cat_id_to_process, all_cats))
    return [cat for cat in all_cats if cat['id'] in selected_ids]

# ==============================================================================
# --- بخش ۲: تنظیمات سراسری و لاگینگ ---
# ==============================================================================
# تنظیمات لاگینگ برای ثبت وقایع در کنسول و فایل
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)
# ایجاد یک فایل لاگ که با حجم 5 مگابایت می‌چرخد
handler = RotatingFileHandler('eways_sync.log', maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# اطلاعات سایت مبدا (eways)
BASE_URL = "https://panel.eways.co"
SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"
PRODUCT_LIST_URL_TEMPLATE = f"{BASE_URL}/Store/List/{{category_id}}/2/2/0/0/0/10000000000?page={{page}}"
PRODUCT_DETAIL_URL_TEMPLATE = f"{BASE_URL}/Store/Detail/{{cat_id}}/{{product_id}}"

# اطلاعات سایت مقصد (ووکامرس) - بهتر است از متغیرهای محیطی خوانده شود
WC_API_URL = os.environ.get("WC_API_URL", "https://your-site.com/wp-json/wc/v3")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY", "ck_your_key")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET", "cs_your_secret")

# اطلاعات کاربری eways
EWAYS_USERNAME = os.environ.get("EWAYS_USERNAME", "your_eways_username")
EWAYS_PASSWORD = os.environ.get("EWAYS_PASSWORD", "your_eways_password")

# فایل‌های کش برای نگهداری وضعیت بین اجراها
CACHE_FILE = 'products_cache.json'
WC_PRODUCT_IDS_CACHE_FILE = 'wc_product_ids.json'

# ==============================================================================
# --- بخش ۳: توابع مربوط به سایت مبدا (eways) ---
# ==============================================================================
def login_eways(username, password):
    """برای لاگین به سایت eways و دریافت کوکی احراز هویت."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    })
    session.verify = False # غیرفعال کردن بررسی SSL

    login_url = f"{BASE_URL}/User/Login"
    payload = {"UserName": username, "Password": password, "RememberMe": "true"}
    logger.info("⏳ در حال لاگین به پنل eways ...")
    try:
        resp = session.post(login_url, data=payload, timeout=30)
        resp.raise_for_status()
        if 'Aut' in session.cookies:
            logger.info("✅ لاگین موفق! کوکی 'Aut' دریافت شد.")
            return session
        else:
            logger.error("❌ کوکی 'Aut' دریافت نشد. لطفاً اطلاعات کاربری یا وضعیت کپچا را بررسی کنید.")
            return None
    except requests.RequestException as e:
        logger.error(f"❌ خطای شبکه هنگام لاگین: {e}")
        return None

@retry(retry=retry_if_exception_type(requests.RequestException), stop=stop_after_attempt(5), wait=wait_random_exponential(multiplier=1, max=10))
def get_product_details(session, cat_id, product_id):
    """جزئیات و مشخصات فنی یک محصول را از صفحه آن استخراج می‌کند."""
    url = PRODUCT_DETAIL_URL_TEMPLATE.format(cat_id=cat_id, product_id=product_id)
    try:
        response = session.get(url, timeout=45)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'lxml')
        specs_table = soup.select_one('#link1 .table-responsive table, .table-responsive table, table.table')
        if not specs_table:
            return {}
        specs = {}
        for row in specs_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) == 2:
                key, value = cells[0].text.strip(), cells[1].text.strip()
                if key and value:
                    specs[key] = value
        return specs
    except requests.RequestException as e:
        logger.warning(f"      - خطای شبکه در دریافت جزئیات محصول {product_id}: {e}. تلاش مجدد...")
        raise
    except Exception as e:
        logger.warning(f"      - خطا در پردازش جزئیات محصول {product_id}: {e}")
        return {}

def get_and_parse_categories(session):
    """لیست تمام دسته‌بندی‌ها را از سایت مبدا دریافت می‌کند."""
    logger.info(f"⏳ دریافت دسته‌بندی‌ها از eways...")
    try:
        response = session.get(SOURCE_CATS_API_URL, timeout=30)
        response.raise_for_status()
        data = response.json()
        final_cats = []
        for c in data:
            real_id_match = re.search(r'/Store/List/(\d+)', c.get('url', ''))
            real_id = int(real_id_match.group(1)) if real_id_match else c.get('id')
            final_cats.append({
                "id": real_id,
                "name": c.get('name', '').strip(),
                "parent_id": c.get('parent_id')
            })
        logger.info(f"✅ تعداد {len(final_cats)} دسته‌بندی از API استخراج شد.")
        return final_cats
    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.error(f"❌ خطا در دریافت یا پردازش دسته‌بندی‌ها: {e}")
        return None

@retry(retry=retry_if_exception_type(requests.RequestException), stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=1.5, max=15))
def get_available_products_from_category_page(session, category_id, delay=0.4):
    """فقط محصولات موجود را از یک صفحه دسته‌بندی استخراج می‌کند."""
    available_products = []
    seen_product_ids = set()
    page_num = 1
    while True:
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page_num)
        try:
            response = session.get(url, timeout=45)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'lxml')
            
            product_blocks = soup.select(".goods-record:not(.noCount)")
            if not product_blocks: break

            page_had_new_products = False
            for block in product_blocks:
                if block.select_one(".goods-record-unavailable"): continue
                
                a_tag = block.select_one("a[href*='/Store/Detail/']")
                if not a_tag: continue
                
                match = re.search(r'/Store/Detail/\d+/(\d+)', a_tag['href'])
                product_id = match.group(1) if match else None
                if not product_id or product_id in seen_product_ids: continue
                
                seen_product_ids.add(product_id)
                page_had_new_products = True

                name = (block.select_one("span.goods-record-title").text.strip() if block.select_one("span.goods-record-title") else "بدون نام")
                price_tag = block.select_one("span.goods-record-price")
                price = re.sub(r'[^\d]', '', price_tag.text.strip()) if price_tag else "0"
                if int(price) <= 0: continue

                image_tag = block.select_one("img.goods-record-image")
                image_url = image_tag.get('data-src') or image_tag.get('src', '')
                specs = get_product_details(session, category_id, product_id)
                
                product = {
                    "id": product_id, "name": name, "price": price,
                    "image": image_url, "category_id": category_id, "specs": specs
                }
                available_products.append(product)
                logger.debug(f"      - محصول موجود یافت شد: {product_id} ({name})")
                time.sleep(random.uniform(delay, delay + 0.2))

            if not page_had_new_products: break
            page_num += 1
        except requests.RequestException as e:
            logger.error(f"    - خطای شبکه در پردازش صفحه {page_num} از دسته {category_id}: {e}. تلاش مجدد...")
            raise
    return available_products

# ==============================================================================
# --- بخش ۴: توابع مربوط به کش و ووکامرس ---
# ==============================================================================
def load_json_cache(file_path, description):
    """یک فایل کش JSON را با کنترل خطا بارگذاری می‌کند."""
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                cache = json.load(f)
                logger.info(f"✅ {description} بارگذاری شد. تعداد آیتم‌ها: {len(cache)}")
                return cache
            except json.JSONDecodeError:
                logger.warning(f"⚠️ فایل کش {file_path} خراب است. یک فایل جدید ساخته خواهد شد.")
                return {}
    return {}

def save_json_cache(data, file_path, description):
    """داده‌ها را در یک فایل کش JSON ذخیره می‌کند."""
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    logger.info(f"✅ {description} ذخیره شد. تعداد آیتم‌ها: {len(data)}")

def transfer_categories_to_wc(source_categories, wc_auth):
    """دسته‌بندی‌ها را از مبدا به ووکامرس منتقل کرده و نگاشت آی‌دی‌ها را برمی‌گرداند."""
    logger.info("\n⏳ شروع انتقال/بررسی دسته‌بندی‌ها به ووکامرس...")
    source_to_wc_id_map = {}
    wc_cats_by_name_parent = {}

    # مرتب‌سازی برای اطمینان از اینکه دسته‌های والد قبل از فرزندان ساخته می‌شوند
    sorted_cats = sorted(source_categories, key=lambda x: (x.get('parent_id') or 0, x['id']))

    for cat in tqdm(sorted_cats, desc="انتقال دسته‌ها"):
        wc_parent_id = source_to_wc_id_map.get(cat.get('parent_id'), 0)
        
        # چک کردن از طریق حافظه موقت برای کاهش درخواست‌ها
        if (cat['name'], wc_parent_id) in wc_cats_by_name_parent:
            source_to_wc_id_map[cat['id']] = wc_cats_by_name_parent[(cat['name'], wc_parent_id)]
            continue

        data = {"name": cat['name'], "parent": wc_parent_id}
        try:
            res = requests.post(f"{WC_API_URL}/products/categories", auth=wc_auth, json=data, verify=False, timeout=20)
            if res.status_code == 201: # Created
                new_cat = res.json()
                source_to_wc_id_map[cat['id']] = new_cat['id']
                wc_cats_by_name_parent[(cat['name'], wc_parent_id)] = new_cat['id']
            elif res.status_code == 400 and res.json().get("code") == "term_exists":
                existing_id = res.json()["data"]["resource_id"]
                source_to_wc_id_map[cat['id']] = existing_id
                wc_cats_by_name_parent[(cat['name'], wc_parent_id)] = existing_id
            else:
                logger.error(f"❌ خطا در ساخت دسته‌بندی '{cat['name']}': {res.text}")
        except requests.RequestException as e:
            logger.error(f"❌ خطای شبکه در ساخت دسته‌بندی '{cat['name']}': {e}")
            
    logger.info("✅ انتقال دسته‌بندی‌ها کامل شد.")
    return source_to_wc_id_map

@retry(retry=retry_if_exception_type(requests.RequestException), stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=1, max=10))
def batch_update_stock_status_in_wc(products_to_update, wc_ids_cache, wc_auth):
    """وضعیت انبار را برای گروهی از محصولات به صورت دسته‌ای در ووکامرس آپدیت می‌کند."""
    if not products_to_update: return 0
    update_payload = [
        {"id": wc_ids_cache[p['sku']], "stock_status": p['stock_status'], "stock_quantity": 0}
        for p in products_to_update if p['sku'] in wc_ids_cache
    ]
    if not update_payload: return 0

    logger.info(f"⏳ ارسال درخواست Batch برای آپدیت وضعیت انبار {len(update_payload)} محصول...")
    try:
        res = requests.post(f"{WC_API_URL}/products/batch", auth=wc_auth, json={"update": update_payload}, verify=False, timeout=120)
        res.raise_for_status()
        logger.info(f"✅ درخواست Batch با موفقیت انجام شد.")
        return len(update_payload)
    except requests.RequestException as e:
        logger.error(f"❌ خطای HTTP در درخواست Batch: {e.response.text if e.response else e}")
        return 0

def process_price(price_str):
    """قیمت ریالی را به تومان تبدیل کرده و بر اساس منطق مشخص شده، سود را اضافه می‌کند."""
    try:
        price = float(re.sub(r'[^\d.]', '', price_str)) / 10
        if price <= 1: return "0"
        elif price <= 7_000_000: new_price = price + 260_000
        elif price <= 10_000_000: new_price = price * 1.035
        elif price <= 20_000_000: new_price = price * 1.025
        elif price <= 30_000_000: new_price = price * 1.02
        else: new_price = price * 1.015
        return str(int(round(new_price, -4))) # رند کردن به ده هزار تومان
    except (ValueError, TypeError):
        return "0"

@retry(retry=retry_if_exception_type(requests.RequestException), stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=1, max=10))
def _send_to_woocommerce(sku, data, stats, wc_ids_cache, wc_auth):
    """یک محصول را به ووکامرس ارسال (ایجاد یا آپدیت) می‌کند."""
    wc_id = wc_ids_cache.get(sku)
    try:
        if wc_id: # آپدیت محصول موجود
            res = requests.put(f"{WC_API_URL}/products/{wc_id}", auth=wc_auth, json=data, verify=False, timeout=30)
            res.raise_for_status()
            with stats['lock']: stats['updated'] += 1
        else: # ایجاد محصول جدید
            res = requests.post(f"{WC_API_URL}/products", auth=wc_auth, json=data, verify=False, timeout=30)
            res.raise_for_status()
            new_wc_id = res.json()['id']
            with stats['lock']:
                wc_ids_cache[sku] = new_wc_id
                stats['created'] += 1
    except requests.RequestException as e:
        logger.error(f"   ❌ خطا در ارسال SKU {sku} به ووکامرس: {e.response.text if e.response else e}")
        with stats['lock']: stats['failed'] += 1
        raise

def process_product_wrapper(args):
    """یک محصول را برای ارسال به ووکامرس آماده‌سازی و ارسال می‌کند."""
    product, stats, category_mapping, cat_map, wc_ids_cache, wc_auth = args
    try:
        wc_cat_id = category_mapping.get(product['category_id'])
        if not wc_cat_id:
            logger.warning(f"   ⚠️ دسته برای محصول {product['id']} در ووکامرس پیدا نشد. رد کردن...")
            return

        attributes = [{"name": k, "options": [v], "visible": True, "variation": False} for k, v in product['specs'].items()]
        tags = [{"name": t} for t in {product['name'].split()[0], cat_map.get(product['category_id'], '')} if t]

        wc_data = {
            "name": product['name'],
            "type": "simple",
            "sku": f"EWAYS-{product['id']}",
            "regular_price": process_price(product['price']),
            "categories": [{"id": wc_cat_id}],
            "images": [{"src": product["image"]}] if product.get("image") else [],
            "stock_quantity": 10, # یا هر عدد دلخواه دیگر
            "manage_stock": True,
            "stock_status": "instock",
            "attributes": attributes,
            "tags": tags
        }
        _send_to_woocommerce(wc_data['sku'], wc_data, stats, wc_ids_cache, wc_auth)
    except Exception as e:
        logger.error(f"   ❌ خطای جدی در پردازش محصول {product['id']}: {e}")
        with stats['lock']: stats['failed'] += 1

# ==============================================================================
# --- بخش ۵: تابع اصلی برنامه ---
# ==============================================================================
def main():
    logger.info("🚀 --- شروع اسکریپت همگام‌سازی Eways با ووکامرس --- 🚀")
    
    # مرحله ۱: لاگین
    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session: return
    wc_auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
    
    # مرحله ۲: دریافت و فیلتر کردن دسته‌بندی‌ها
    all_cats_source = get_and_parse_categories(session)
    if not all_cats_source: return
    cat_map_by_id = {c['id']: c['name'] for c in all_cats_source}

    SELECTED_IDS_STRING = "1582:14548-allz,1584-all-allz|16777:all-allz|4882:all-allz|16778:22570-all-allz"
    parsed_selection = parse_selected_ids_string(SELECTED_IDS_STRING)
    filtered_categories = get_selected_categories_according_to_selection(parsed_selection, all_cats_source)
    logger.info(f"✅ تعداد {len(filtered_categories)} دسته‌بندی برای پردازش انتخاب شد.")

    # مرحله ۳: انتقال دسته‌بندی‌ها به ووکامرس
    category_mapping = transfer_categories_to_wc(filtered_categories, wc_auth)

    # مرحله ۴: بارگذاری کش‌ها و دریافت محصولات موجود
    cached_products = load_json_cache(CACHE_FILE, "کش محصولات (اجرای قبل)")
    wc_ids_cache = load_json_cache(WC_PRODUCT_IDS_CACHE_FILE, "کش آی‌دی‌های ووکامرس")
    
    logger.info("\n⏳ شروع جمع‌آوری فقط محصولات موجود از دسته‌های انتخابی...")
    all_available_products_now = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(get_available_products_from_category_page, session, cat['id']): cat['name'] for cat in filtered_categories}
        for future in tqdm(as_completed(futures), total=len(futures), desc="دریافت محصولات موجود"):
            try:
                for product in future.result():
                    all_available_products_now[f"EWAYS-{product['id']}"] = product
            except Exception as e:
                logger.error(f"⚠️ خطا در دریافت محصولات دسته '{futures[future]}': {e}")
    logger.info(f"✅ تعداد کل محصولات موجود یافت‌شده: {len(all_available_products_now)}")

    # مرحله ۵: مقایسه با کش و شناسایی تغییرات
    logger.info("\n⏳ مقایسه با کش و شناسایی تغییرات...")
    current_skus = set(all_available_products_now.keys())
    cached_skus = set(cached_products.keys())

    skus_to_make_unavailable = cached_skus - current_skus
    skus_to_create = current_skus - cached_skus
    skus_to_check_for_update = current_skus.intersection(cached_skus)

    products_to_update = []
    for sku in skus_to_check_for_update:
        current_p = all_available_products_now[sku]
        cached_p = cached_products[sku]
        if (current_p['price'] != cached_p.get('price') or current_p['name'] != cached_p.get('name') or current_p['specs'] != cached_p.get('specs')):
            products_to_update.append(current_p)
    
    logger.info("🔎 نتایج مقایسه:")
    logger.info(f"  - 🟢 محصولات جدید برای ایجاد: {len(skus_to_create)}")
    logger.info(f"  - 🔵 محصولات برای بروزرسانی (تغییر قیمت/مشخصات): {len(products_to_update)}")
    logger.info(f"  - 🟠 محصولات برای ناموجود کردن: {len(skus_to_make_unavailable)}")

    stats = {'created': 0, 'updated': 0, 'failed': 0, 'stock_updated': 0, 'lock': Lock()}
    
    # مرحله ۶: اجرای عملیات در ووکامرس
    if skus_to_make_unavailable:
        products_to_set_oos = [{"sku": sku, "stock_status": "outofstock"} for sku in skus_to_make_unavailable]
        stats['stock_updated'] = batch_update_stock_status_in_wc(products_to_set_oos, wc_ids_cache, wc_auth)

    products_to_process_queue = Queue()
    for sku in skus_to_create:
        products_to_process_queue.put(all_available_products_now[sku])
    for p in products_to_update:
        products_to_process_queue.put(p)

    if not products_to_process_queue.empty():
        logger.info(f"\n🚀 شروع ارسال {products_to_process_queue.qsize()} محصول (جدید/آپدیتی) به ووکامرس...")
        
        def worker():
            while not products_to_process_queue.empty():
                try:
                    product = products_to_process_queue.get_nowait()
                    process_product_wrapper((product, stats, category_mapping, cat_map_by_id, wc_ids_cache, wc_auth))
                    products_to_process_queue.task_done()
                except Queue.Empty: break
        
        threads = [Thread(target=worker) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()

    # مرحله ۷: ذخیره کش‌های جدید برای اجرای بعدی
    save_json_cache(all_available_products_now, CACHE_FILE, "کش محصولات جدید")
    save_json_cache(wc_ids_cache, WC_PRODUCT_IDS_CACHE_FILE, "کش آی‌دی‌های ووکامرس")

    logger.info("\n===============================")
    logger.info(f"📊 خلاصه نهایی عملیات:")
    logger.info(f"🟢 محصولات ایجاد شده: {stats['created']}")
    logger.info(f"🔵 محصولات آپدیت شده: {stats['updated']}")
    logger.info(f"🟠 محصولات ناموجود شده: {stats['stock_updated']}")
    logger.info(f"🔴 عملیات شکست‌خورده: {stats['failed']}")
    logger.info("===============================\n🏁 --- پایان اسکریپت --- 🏁")

if __name__ == "__main__":
    # اطمینان حاصل کنید که اطلاعات حساس در متغیرهای محیطی تنظیم شده‌اند
    if "your-site.com" in WC_API_URL or "your_key" in WC_CONSUMER_KEY:
        logger.critical("🚨 اطلاعات ووکامرس (URL/KEY) به درستی تنظیم نشده است. برنامه خاتمه می‌یابد.")
    elif "your_eways_username" in EWAYS_USERNAME:
        logger.critical("🚨 اطلاعات کاربری Eways تنظیم نشده است. برنامه خاتمه می‌یابد.")
    else:
        main()
