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
# --- توابع پردازش قوانین انتخاب ---
# ==============================================================================
def get_all_descendants(parent_id, all_cats_map):
    """تمام نوادگان یک دسته را به صورت بازگشتی پیدا می‌کند."""
    descendants = set()
    children = [cat['id'] for cat in all_cats_map.values() if cat.get('parent_id') == parent_id]
    for child_id in children:
        descendants.add(child_id)
        descendants.update(get_all_descendants(child_id, all_cats_map))
    return descendants

def process_selection_rules(rule_string, all_cats, logger_instance):
    """رشته قوانین را پردازش کرده و دو لیست ID مجزا برمی‌گرداند."""
    structure_ids, product_ids = set(), set()
    all_cats_map = {cat['id']: cat for cat in all_cats}
    for rule in rule_string.split('|'):
        rule = rule.strip()
        if not rule or ':' not in rule: continue
        try:
            parent_id_str, selections_str = rule.split(':', 1)
            parent_id = int(parent_id_str.strip())
            if parent_id not in all_cats_map:
                logger_instance.warning(f"⚠️ شناسه والد {parent_id} در قانون '{rule}' یافت نشد.")
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
                    if command == 'allz': product_ids.add(child_id)
                    elif command == 'all-allz':
                        product_ids.add(child_id)
                        descendants = get_all_descendants(child_id, all_cats_map)
                        structure_ids.update(descendants)
                        product_ids.update(descendants)
        except Exception as e:
            logger_instance.error(f"❌ خطای جدی در پردازش قانون '{rule}': {e}")
    return list(structure_ids), list(product_ids)

# ==============================================================================
# --- تنظیمات لاگینگ و اطلاعات کلی ---
# ==============================================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
file_handler = RotatingFileHandler('app.log', maxBytes=2*1024*1024, backupCount=5, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
    logger.addHandler(file_handler)

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
            final_cats = []
            for c in data:
                real_id_match = re.search(r'/Store/List/(\d+)', c.get('url', ''))
                real_id = int(real_id_match.group(1)) if real_id_match else c.get('id')
                final_cats.append({"id": real_id, "name": c.get('name', '').strip(), "parent_id": c.get('parent_id')})
            logger.info(f"✅ تعداد {len(final_cats)} دسته‌بندی از JSON استخراج شد.")
            return final_cats
        except json.JSONDecodeError:
            logger.warning("⚠️ پاسخ JSON نبود. تلاش برای پارس HTML...")
        soup = BeautifulSoup(response.text, 'lxml')
        all_menu_items = soup.select("li[id^='menu-item-']")
        if not all_menu_items:
            logger.error("❌ هیچ آیتم دسته‌بندی در HTML پیدا نشد.")
            return []
        cats_map = {}
        for item in all_menu_items:
            cat_id_raw = item.get('id', ''); match = re.search(r'(\d+)', cat_id_raw)
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
            cat_id_raw = item.get('id', ''); match = re.search(r'(\d+)', cat_id_raw)
            if not match: continue
            cat_menu_id = int(match.group(1))
            parent_li = item.find_parent("li", class_="menu-item-has-children")
            if parent_li:
                parent_id_raw = parent_li.get('id', ''); parent_match = re.search(r'(\d+)', parent_id_raw)
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

def check_wc_connection():
    logger.info("⏳ در حال بررسی اتصال به ووکامرس...")
    try:
        res = requests.get(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False, timeout=15)
        if res.status_code == 401:
            logger.error("❌ اتصال به ووکامرس ناموفق: خطای 401 Unauthorized. لطفاً کلیدهای API را بررسی کنید.")
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

def transfer_categories_to_wc(source_categories, all_cats_from_source):
    logger.info(f"\n⏳ شروع انتقال {len(source_categories)} دسته‌بندی به ووکامرس...")
    source_cat_map = {cat['id']: cat for cat in all_cats_from_source}
    source_to_wc_id_map = {}
    def get_depth(cat_id):
        depth = 0
        current_id = cat_id
        while current_id and source_cat_map.get(current_id, {}).get('parent_id'):
            depth += 1
            current_id = source_cat_map.get(current_id, {}).get('parent_id')
        return depth
    sorted_cats = sorted(source_categories, key=lambda c: get_depth(c['id']))
    for cat in tqdm(sorted_cats, desc="انتقال دسته‌ها"):
        name = cat["name"].strip()
        source_parent_id = cat.get("parent_id")
        wc_parent_id = source_to_wc_id_map.get(source_parent_id, 0)
        logger.debug(f"  - پردازش '{name}' (Source ID: {cat['id']}). والد مورد انتظار در WC: {wc_parent_id}")
        try:
            params = {"search": name, "parent": wc_parent_id, "per_page": 100}
            res_check = requests.get(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), params=params, verify=False, timeout=20)
            res_check.raise_for_status()
            existing_cats = res_check.json()
            exact_match = next((wc_cat for wc_cat in existing_cats if wc_cat['name'].strip() == name and wc_cat['parent'] == wc_parent_id), None)
            if exact_match:
                source_to_wc_id_map[cat["id"]] = exact_match["id"]
                logger.debug(f"    -> دسته '{name}' با والد صحیح ({wc_parent_id}) از قبل وجود دارد. WC ID: {exact_match['id']}")
                continue
            logger.debug(f"    -> دسته '{name}' با والد {wc_parent_id} وجود ندارد. تلاش برای ساخت...")
            data = {"name": name, "parent": wc_parent_id}
            res_create = requests.post(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False, timeout=20)
            if res_create.status_code in [200, 201]:
                new_id = res_create.json()["id"]
                source_to_wc_id_map[cat["id"]] = new_id
                logger.debug(f"    -> ✅ دسته با موفقیت ساخته شد. WC ID جدید: {new_id}")
            else:
                error_data = res_create.json()
                if error_data.get("code") == "term_exists" and error_data.get("data", {}).get("resource_id"):
                    existing_id = error_data["data"]["resource_id"]
                    source_to_wc_id_map[cat["id"]] = existing_id
                    logger.warning(f"    -> دسته '{name}' به دلیل 'term_exists' با ID موجود {existing_id} مپ شد.")
                else:
                    logger.error(f"❌ خطا در ساخت دسته '{name}' با والد {wc_parent_id}: {res_create.text}")
        except Exception as e:
            logger.error(f"❌ خطای جدی در حین پردازش دسته '{name}': {e}")
            return None
    logger.info(f"✅ انتقال دسته‌بندی‌ها کامل شد. تعداد نگاشت‌شده: {len(source_to_wc_id_map)}")
    return source_to_wc_id_map

@retry(
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    stop=stop_after_attempt(5),
    wait=wait_random_exponential(multiplier=1, max_value=5),
    reraise=True
)
def get_product_details(session, cat_id, product_id):
    url = PRODUCT_DETAIL_URL_TEMPLATE.format(cat_id=cat_id, product_id=product_id)
    try:
        response = session.get(url, timeout=60)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'lxml')
        specs_table = soup.select_one('#link1 .table-responsive table') or soup.select_one('.table-responsive table') or soup.find('table', class_='table')
        if not specs_table:
            return {}
        specs = {}
        for row in specs_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) == 2:
                key = cells[0].text.strip()
                value = cells[1].text.strip()
                if key and value:
                    specs[key] = value
        return specs
    except requests.exceptions.RequestException as e:
        logger.warning(f"      - خطا در دریافت جزئیات محصول {product_id}: {e}. تلاش مجدد...")
        raise
    except Exception as e:
        logger.warning(f"      - خطا در استخراج مشخصات محصول {product_id}: {e}")
        return {}

def get_products_from_category_page(session, category_id, max_pages=100):
    all_products = []
    seen_product_ids = set()
    page_num = 1
    while page_num <= max_pages:
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page_num)
        logger.info(f"  - دریافت محصولات از دسته {category_id}، صفحه {page_num}...")
        try:
            response = session.get(url, timeout=30)
            if response.status_code != 200:
                break
            soup = BeautifulSoup(response.text, 'lxml')
            product_blocks = soup.select(".goods_item.goods-record")
            if not product_blocks:
                break
            page_has_new = False
            for block in product_blocks:
                try:
                    if block.select_one(".goods-record-unavailable"):
                        continue
                    a_tag = block.select_one("a")
                    href = a_tag['href'] if a_tag else None
                    match = re.search(r'/Store/Detail/\d+/(\d+)', href) if href else None
                    product_id = match.group(1) if match else None
                    if not product_id or product_id in seen_product_ids:
                        continue
                    page_has_new = True
                    seen_product_ids.add(product_id)
                    name = block.select_one("span.goods-record-title").text.strip()
                    price = re.sub(r'[^\d]', '', block.select_one("span.goods-record-price").text)
                    image_url = block.select_one("img.goods-record-image").get('data-src', '')
                    if not all([name, price, int(price) > 0]):
                        continue
                    specs = get_product_details(session, category_id, product_id)
                    time.sleep(random.uniform(0.3, 0.8))
                    product = {"id": product_id, "name": name, "price": price, "stock": 1, "image": image_url, "category_id": category_id, "specs": specs}
                    all_products.append(product)
                except Exception as e:
                    logger.warning(f"      - خطا در پردازش یک بلاک محصول: {e}")
            if not page_has_new:
                break
            page_num += 1
            time.sleep(random.uniform(0.5, 1.5))
        except requests.RequestException as e:
            logger.error(f"    - خطای شبکه در پردازش صفحه محصولات: {e}")
            break
    return all_products

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def save_cache(products):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(products, f, ensure_ascii=False, indent=4)
    except IOError as e:
        logger.error(f"❌ خطا در ذخیره فایل کش: {e}")

@retry(retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)), stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=1, max=10), reraise=True)
def _send_to_woocommerce(sku, data, stats):
    try:
        auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
        check_url = f"{WC_API_URL}/products?sku={sku}"
        r_check = requests.get(check_url, auth=auth, verify=False, timeout=20)
        r_check.raise_for_status()
        existing = r_check.json()
        if existing:
            product_id = existing[0]['id']
            update_data = {
                "regular_price": data["regular_price"],
                "stock_quantity": data["stock_quantity"],
                "stock_status": data["stock_status"],
                "attributes": data["attributes"]
            }
            res = requests.put(f"{WC_API_URL}/products/{product_id}", auth=auth, json=update_data, verify=False, timeout=30)
            res.raise_for_status()
            with stats['lock']:
                stats['updated'] += 1
        else:
            res = requests.post(f"{WC_API_URL}/products", auth=auth, json=data, verify=False, timeout=30)
            res.raise_for_status()
            with stats['lock']:
                stats['created'] += 1
    except requests.exceptions.HTTPError as e:
        logger.error(f"   ❌ HTTP خطا برای SKU {sku}: {e.response.status_code} - Response: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"   ❌ خطای کلی در ارتباط با ووکامرس برای SKU {sku}: {e}")
        raise

def process_product_wrapper(args):
    product, stats, category_mapping = args
    try:
        wc_cat_id = category_mapping.get(product.get('category_id'))
        if not wc_cat_id:
            return
        attributes = [{"name": k, "options": [v], "position": i, "visible": True, "variation": False} for i, (k, v) in enumerate(product.get('specs', {}).items())]
        wc_data = {
            "name": product.get('name', 'بدون نام'),
            "type": "simple",
            "sku": f"EWAYS-{product.get('id')}",
            "regular_price": process_price(product.get('price', 0)),
            "categories": [{"id": wc_cat_id}],
            "images": [{"src": product.get("image")}] if product.get("image") else [],
            "stock_quantity": product.get('stock', 0),
            "manage_stock": True,
            "stock_status": "instock" if product.get('stock', 0) > 0 else "outofstock",
            "attributes": attributes
        }
        _send_to_woocommerce(wc_data['sku'], wc_data, stats)
        time.sleep(random.uniform(0.5, 1.5))
    except Exception as e:
        logger.error(f"   ❌ خطای جدی در پردازش محصول {product.get('id', '')}: {e}")
        with stats['lock']:
            stats['failed'] += 1

def process_price(price_value):
    try:
        price_value = float(re.sub(r'[^\d.]', '', str(price_value))) / 10
    except (ValueError, TypeError):
        return "0"
    if price_value <= 7000000:
        new_price = price_value + 260000
    elif price_value <= 10000000:
        new_price = price_value * 1.035
    elif price_value <= 20000000:
        new_price = price_value * 1.025
    elif price_value <= 30000000:
        new_price = price_value * 1.02
    else:
        new_price = price_value * 1.015
    return str(int(round(new_price, -4)))

# ==============================================================================
# --- تابع اصلی ---
# ==============================================================================
def main():
    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session:
        return

    all_cats = get_and_parse_categories(session)
    if not all_cats:
        return
    logger.info(f"✅ مرحله ۱: بارگذاری کل دسته‌بندی‌ها کامل شد. تعداد: {len(all_cats)}")

    if not check_wc_connection():
        logger.error("برنامه به دلیل عدم امکان اتصال به ووکامرس خاتمه یافت.")
        return

    SELECTED_IDS_STRING = "1582:14548-allz,1584-all-allz|16777:all-allz|4882:all-allz|16778:22570-all-allz"
    structure_cat_ids, product_cat_ids = process_selection_rules(SELECTED_IDS_STRING, all_cats, logger)
    
    cat_name_map = {cat['id']: cat['name'] for cat in all_cats}
    structure_cat_names = [cat_name_map.get(cat_id, f'ID ناشناخته:{cat_id}') for cat_id in sorted(list(structure_cat_ids))]
    product_cat_names = [cat_name_map.get(cat_id, f'ID ناشناخته:{cat_id}') for cat_id in sorted(list(product_cat_ids))]
    
    logger.info(f"✅ دسته‌بندی‌های ساختاری برای انتقال: {structure_cat_names}")
    logger.info(f"✅ دسته‌بندی‌های محصول برای استخراج: {product_cat_names}")

    cats_for_wc_transfer = [cat for cat in all_cats if cat['id'] in structure_cat_ids]
    category_mapping = transfer_categories_to_wc(cats_for_wc_transfer, all_cats)
    if not category_mapping:
        logger.error("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد. برنامه خاتمه می‌یابد.")
        return
    logger.info(f"✅ مرحله ۲: انتقال دسته‌بندی‌های ساختاری کامل شد.")

    cached_products = load_cache()
    all_products = {}
    logger.info("\n⏳ شروع فرآیند جمع‌آوری محصولات...")
    for cat_id in tqdm(product_cat_ids, desc="دریافت محصولات"):
        products_in_cat = get_products_from_category_page(session, cat_id)
        for product in products_in_cat:
            all_products[product['id']] = product
    
    new_products_list = list(all_products.values())
    logger.info(f"\n✅ مرحله ۳: استخراج محصولات کامل شد. تعداد: {len(new_products_list)}")

    products_to_send = []
    updated_cache_data = {}
    for p in new_products_list:
        pid = str(p['id'])
        cached_p = cached_products.get(pid)
        if not cached_p or cached_p.get('price') != p.get('price') or cached_p.get('specs') != p.get('specs'):
            products_to_send.append(p)
        updated_cache_data[pid] = p
        
    logger.info(f"✅ مرحله ۴: مقایسه با کش کامل شد. محصولات جدید/تغییرکرده: {len(products_to_send)}")
    save_cache(updated_cache_data)

    if not products_to_send:
        logger.info("🎉 هیچ محصول جدید یا تغییرکرده‌ای برای ارسال وجود ندارد!")
        return

    stats = {'created': 0, 'updated': 0, 'failed': 0, 'lock': Lock()}
    logger.info(f"\n🚀 شروع ارسال {len(products_to_send)} محصول به ووکامرس...")
    with ThreadPoolExecutor(max_workers=3) as executor:
        args_list = [(p, stats, category_mapping) for p in products_to_send]
        list(tqdm(executor.map(process_product_wrapper, args_list), total=len(products_to_send), desc="ارسال محصولات"))

    logger.info("\n===============================")
    logger.info("📦 خلاصه عملیات:")
    logger.info(f"🟢 ایجاد شده: {stats['created']}")
    logger.info(f"🔵 آپدیت شده: {stats['updated']}")
    logger.info(f"🔴 شکست‌خورده: {stats['failed']}")
    logger.info("===============================")

if __name__ == "__main__":
    main()
