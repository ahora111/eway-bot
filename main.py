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

# ==============================================================================
# --- توابع انتخاب منعطف با SELECTED_IDS_STRING ---
# ==============================================================================
def parse_selected_ids_string(selected_ids_string):
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
            elif re.match(r'^\d+-allz$', sel):
                sub_id = int(sel.split('-')[0])
                selections.append({"id": sub_id, "type": "only_products"})
            elif re.match(r'^\d+-all-allz$', sel):
                sub_id = int(sel.split('-')[0])
                selections.append({"id": sub_id, "type": "all_subcats_and_products"})
        result.append({"parent_id": parent_id, "selections": selections})
    return result

def get_direct_subcategories(parent_id, all_cats):
    return [cat['id'] for cat in all_cats if cat['parent_id'] == parent_id]

def get_all_subcategories(parent_id, all_cats):
    result = []
    direct = get_direct_subcategories(parent_id, all_cats)
    result.extend(direct)
    for sub_id in direct:
        result.extend(get_all_subcategories(sub_id, all_cats))
    return result

def get_selected_categories_according_to_selection(parsed_selection, all_cats):
    selected_ids = set()
    for block in parsed_selection:
        parent_id = block['parent_id']
        selected_ids.add(parent_id)
        for sel in block['selections']:
            if sel['type'] == 'all_subcats' and sel['id'] == parent_id:
                selected_ids.update(get_direct_subcategories(parent_id, all_cats))
            elif sel['type'] == 'only_products' and sel['id'] == parent_id:
                selected_ids.add(parent_id)
            elif sel['type'] == 'all_subcats_and_products' and sel['id'] == parent_id:
                direct_subs = get_direct_subcategories(parent_id, all_cats)
                selected_ids.update(direct_subs)
                for sub_id in direct_subs:
                    selected_ids.update(get_all_subcategories(sub_id, all_cats))
                selected_ids.add(parent_id)
            elif sel['type'] == 'only_products' and sel['id'] != parent_id:
                selected_ids.add(sel['id'])
            elif sel['type'] == 'all_subcats_and_products' and sel['id'] != parent_id:
                selected_ids.add(sel['id'])
                selected_ids.update(get_all_subcategories(sel['id'], all_cats))
    return [cat for cat in all_cats if cat['id'] in selected_ids]

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
        logger.error(f"❌ لاگین ناموفق! کد وضعیت: {resp.status_code} - متن پاسخ: {resp.text[:200]}")
        return None

    if 'Aut' in session.cookies:
        logger.info("✅ لاگین موفق! کوکی Aut دریافت شد.")
        return session
    else:
        if "کپچا" in resp.text or "captcha" in resp.text.lower():
            logger.error("❌ کوکی Aut دریافت نشد. کپچا فعال است.")
        elif "نام کاربری" in resp.text or "رمز عبور" in resp.text:
            logger.error("❌ کوکی Aut دریافت نشد. نام کاربری یا رمز عبور اشتباه است.")
        else:
            logger.error("❌ کوکی Aut دریافت نشد. لاگین ناموفق یا دلیل نامشخص.")
        return None

# ==============================================================================
# --- توابع مربوط به سایت مبدا (eways) ---
# ==============================================================================

@retry(
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    stop=stop_after_attempt(5),
    wait=wait_random_exponential(multiplier=1, max=5),
    reraise=True
)
def get_product_details(session, cat_id, product_id):
    url = PRODUCT_DETAIL_URL_TEMPLATE.format(cat_id=cat_id, product_id=product_id)
    try:
        response = session.get(url, timeout=60)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'lxml')
        specs_table = soup.select_one('#link1 .table-responsive table')
        if not specs_table:
            specs_table = soup.select_one('.table-responsive table')
            if not specs_table:
                specs_table = soup.find('table', class_='table')
                if not specs_table:
                    logger.debug(f"      - هیچ جدولی پیدا نشد. HTML خام صفحه: {soup.prettify()[:1000]}...")
                    return {}
        specs = {}
        rows = specs_table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) == 2:
                key = cells[0].text.strip()
                value = cells[1].text.strip()
                if key and value:
                    specs[key] = value
        if not specs:
            logger.debug(f"      - هیچ ردیفی در جدول پیدا نشد. HTML خام جدول: {specs_table.prettify()}")
        logger.debug(f"      - مشخصات استخراج‌شده برای {product_id} (کامل): {json.dumps(specs, ensure_ascii=False, indent=4)}")
        return specs
    except requests.exceptions.RequestException as e:
        logger.warning(f"      - خطا در دریافت جزئیات محصول {product_id}: {e}. Retry...")
        raise
    except Exception as e:
        logger.warning(f"      - خطا در استخراج مشخصات محصول {product_id}: {e}")
        return {}

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

# ==============================================================================
# --- گرفتن محصولات هر دسته با کنترل خطا و @retry ---
# ==============================================================================

MAX_ERRORS_PER_CATEGORY = 3

@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)),
    stop=stop_after_attempt(4),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True
)
def get_products_from_category_page(session, category_id, max_pages=10, delay=0.5):
    all_products_in_category = []
    seen_product_ids = set()
    page_num = 1
    error_count = 0
    while page_num <= max_pages:
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page_num)
        logger.info(f"  - در حال دریافت محصولات از: {url}")
        try:
            response = session.get(url, timeout=30)
            if response.status_code in [429, 503, 403]:
                raise requests.exceptions.HTTPError(f"Blocked or rate limited: {response.status_code}", response=response)
            if response.status_code != 200: break
            soup = BeautifulSoup(response.text, 'lxml')
            product_blocks = soup.select(".goods_item.goods-record")
            logger.info(f"    - تعداد بلاک‌های محصول پیدا شده: {len(product_blocks)}")
            if not product_blocks:
                logger.info("    - هیچ محصولی در این صفحه یافت نشد. پایان صفحه‌بندی.")
                break
            current_page_product_ids = []
            for block in product_blocks:
                try:
                    unavailable = block.select_one(".goods-record-unavailable")
                    if unavailable:
                        continue
                    a_tag = block.select_one("a")
                    href = a_tag['href'] if a_tag else None
                    product_id = None
                    if href:
                        match = re.search(r'/Store/Detail/\d+/(\d+)', href)
                        product_id = match.group(1) if match else None
                    if not product_id or product_id in seen_product_ids or product_id.startswith('##'):
                        continue
                    name_tag = block.select_one("span.goods-record-title")
                    name = name_tag.text.strip() if name_tag else None
                    price_tag = block.select_one("span.goods-record-price")
                    price = re.sub(r'[^\d]', '', price_tag.text.strip()) if price_tag else None
                    image_tag = block.select_one("img.goods-record-image")
                    image_url = image_tag.get('data-src', '') if image_tag else ''
                    if not name or not price or int(price) <= 0:
                        logger.debug(f"      - محصول {product_id} نامعتبر (نام: {name}, قیمت: {price})")
                        continue
                    stock = 1
                    specs = get_product_details(session, category_id, product_id)
                    time.sleep(random.uniform(delay, delay + 0.2))
                    product = {
                        "id": product_id,
                        "name": name,
                        "price": price,
                        "stock": stock,
                        "image": image_url,
                        "category_id": category_id,
                        "specs": specs
                    }
                    seen_product_ids.add(product_id)
                    current_page_product_ids.append(product_id)
                    all_products_in_category.append(product)
                    logger.info(f"      - محصول {product_id} ({product['name']}) اضافه شد با قیمت {product['price']} و {len(specs)} مشخصه فنی.")
                except Exception as e:
                    logger.warning(f"      - خطا در پردازش یک بلاک محصول: {e}. رد شدن...")
            if not current_page_product_ids:
                logger.info("    - محصول جدیدی در این صفحه یافت نشد، توقف صفحه‌بندی.")
                break
            page_num += 1
            time.sleep(random.uniform(delay, delay + 0.2))
            error_count = 0  # اگر موفق بود، شمارنده خطا ریست شود
        except Exception as e:
            error_count += 1
            logger.error(f"    - خطا در پردازش صفحه محصولات: {e} (تعداد خطا: {error_count})")
            if error_count >= MAX_ERRORS_PER_CATEGORY:
                logger.critical(f"🚨 تعداد خطاهای متوالی در دسته {category_id} به {error_count} رسید! توقف پردازش این دسته.")
                break
            time.sleep(2)
    logger.info(f"    - تعداد کل محصولات استخراج‌شده از دسته {category_id}: {len(all_products_in_category)}")
    return all_products_in_category

# ==============================================================================
# --- کش برای محصولات (کلید ترکیبی id|category_id) ---
# ==============================================================================
def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            cache = json.load(f)
            logger.info(f"✅ کش بارگذاری شد. تعداد محصولات در کش: {len(cache)}")
            return cache
    logger.info("⚠️ کش پیدا نشد. استخراج کامل انجام می‌شود.")
    return {}

def save_cache(products):
    with open(CACHE_FILE, 'w') as f:
        json.dump(products, f, ensure_ascii=False, indent=4)
    logger.info(f"✅ کش ذخیره شد. تعداد محصولات: {len(products)}")

# ==============================================================================
# --- توابع ووکامرس ---
# ==============================================================================
def get_wc_categories():
    wc_cats, page = [], 1
    while True:
        try:
            res = requests.get(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), params={"per_page": 100, "page": page}, verify=False)
            res.raise_for_status()
            data = res.json()
            if not data: break
            wc_cats.extend(data)
            if len(data) < 100: break
            page += 1
        except Exception as e:
            logger.error(f"❌ خطا در دریافت دسته‌بندی‌های ووکامرس: {e}")
            break
    logger.info(f"✅ تعداد دسته‌بندی‌های ووکامرس بارگذاری‌شده: {len(wc_cats)}")
    return wc_cats

def check_existing_category(name, parent):
    try:
        res = requests.get(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), params={
            "search": name, "per_page": 1, "parent": parent
        }, verify=False)
        res.raise_for_status()
        data = res.json()
        for cat in data:
            if cat["name"].strip() == name and cat["parent"] == parent:
                return cat["id"]
        return None
    except Exception as e:
        logger.debug(f"⚠️ خطا در چک وجود دسته '{name}' (parent: {parent}): {e}")
        return None

def transfer_categories_to_wc(source_categories):
    logger.info("\n⏳ شروع انتقال دسته‌بندی‌ها به ووکامرس...")
    wc_cats = get_wc_categories()
    wc_cats_map = {}  # tuple (name, parent) -> id
    for cat in wc_cats:
        key = (cat["name"].strip(), cat.get("parent", 0))
        wc_cats_map[key] = cat["id"]
    sorted_cats = []
    id_to_cat = {cat['id']: cat for cat in source_categories}
    def add_with_parents(cat):
        if cat['parent_id'] and cat['parent_id'] in id_to_cat:
            parent_cat = id_to_cat[cat['parent_id']]
            if parent_cat not in sorted_cats:
                add_with_parents(parent_cat)
        if cat not in sorted_cats:
            sorted_cats.append(cat)
    for cat in source_categories:
        add_with_parents(cat)
    source_to_wc_id_map = {}
    transferred = 0
    for cat in tqdm(sorted_cats, desc="انتقال دسته‌ها"):
        name = cat["name"].strip()
        parent_id = cat.get("parent_id") or 0
        wc_parent = source_to_wc_id_map.get(parent_id, 0)
        lookup_key = (name, wc_parent)
        existing_id = check_existing_category(name, wc_parent)
        if existing_id:
            source_to_wc_id_map[cat["id"]] = existing_id
            logger.debug(f"✅ دسته '{name}' (parent: {wc_parent}) قبلاً وجود دارد (ID: {existing_id}). استفاده از موجود.")
            transferred += 1
            continue
        data = {"name": name, "parent": wc_parent}
        try:
            res = requests.post(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
            if res.status_code in [200, 201]:
                new_id = res.json()["id"]
                source_to_wc_id_map[cat["id"]] = new_id
                wc_cats_map[lookup_key] = new_id
                logger.debug(f"✅ دسته '{name}' (parent: {wc_parent}) ساخته شد (ID: {new_id}).")
                transferred += 1
            else:
                error_data = res.json()
                if error_data.get("code") == "term_exists" and "data" in error_data and "resource_id" in error_data["data"]:
                    existing_id = error_data["data"]["resource_id"]
                    source_to_wc_id_map[cat["id"]] = existing_id
                    wc_cats_map[lookup_key] = existing_id
                    logger.info(f"✅ دسته '{name}' (parent: {wc_parent}) وجود داشت (ID: {existing_id}). استفاده از resource_id موجود.")
                    transferred += 1
                else:
                    logger.error(f"❌ خطا در ساخت دسته‌بندی '{name}' (parent: {wc_parent}): {res.text}")
        except Exception as e:
            logger.error(f"❌ خطای شبکه در ساخت دسته‌بندی '{name}': {e}")
    logger.info(f"✅ انتقال دسته‌بندی‌ها کامل شد. تعداد منتقل‌شده: {transferred}/{len(source_categories)}")
    return source_to_wc_id_map

def process_price(price_value):
    try:
        price_value = float(re.sub(r'[^\d.]', '', str(price_value)))
        price_value /= 10
    except (ValueError, TypeError): return "0"
    if price_value <= 1: return "0"
    elif price_value <= 7000000: new_price = price_value + 260000
    elif price_value <= 10000000: new_price = price_value * 1.035
    elif price_value <= 20000000: new_price = price_value * 1.025
    elif price_value <= 30000000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price, -4)))

@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True
)
def _send_to_woocommerce(sku, data, stats):
    try:
        auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
        logger.debug(f"   - چک SKU {sku}...")
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
                "attributes": data["attributes"],
                "tags": data.get("tags", [])
            }
            logger.debug(f"   - آپدیت محصول {product_id} با {len(update_data['attributes'])} مشخصه فنی...")
            res = requests.put(f"{WC_API_URL}/products/{product_id}", auth=auth, json=update_data, verify=False, timeout=20)
            res.raise_for_status()
            response_json = res.json()
            logger.debug(f"   ✅ آپدیت موفق برای {sku}. Attributes ذخیره‌شده در پاسخ: {response_json.get('attributes', 'خالی')} (تعداد: {len(response_json.get('attributes', []))})")
            with stats['lock']: stats['updated'] += 1
        else:
            logger.debug(f"   - ایجاد محصول جدید با {sku} و {len(data['attributes'])} مشخصه فنی...")
            res = requests.post(f"{WC_API_URL}/products", auth=auth, json=data, verify=False, timeout=20)
            res.raise_for_status()
            response_json = res.json()
            logger.debug(f"   ✅ ایجاد موفق برای {sku}. Attributes ذخیره‌شده در پاسخ: {response_json.get('attributes', 'خالی')} (تعداد: {len(response_json.get('attributes', []))})")
            with stats['lock']: stats['created'] += 1
    except requests.exceptions.HTTPError as e:
        logger.error(f"   ❌ HTTP خطا برای SKU {sku}: {e.response.status_code} - Response: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"   ❌ خطای کلی در ارتباط با ووکامرس برای SKU {sku}: {e}")
        raise

# ==============================================================================
# --- برچسب‌گذاری هوشمند سئو محور ---
# ==============================================================================
def smart_tags_for_product(product, cat_map):
    tags = set()
    name = product.get('name', '')
    specs = product.get('specs', {})
    cat_id = product.get('category_id')
    cat_name = cat_map.get(cat_id, '').strip()
    price = int(product.get('price', 0))

    # 1. برند و مدل از نام محصول (اولین و دومین کلمه غیرتکراری و غیرعمومی)
    name_parts = [w for w in re.split(r'\s+', name) if w and len(w) > 2]
    common_words = {'گوشی', 'موبایل', 'تبلت', 'لپتاپ', 'لپ‌تاپ', 'مدل', 'محصول', 'کالا', 'جدید'}
    for part in name_parts[:2]:
        if part not in common_words:
            tags.add(part)

    # 2. نام دسته‌بندی (اگر خیلی عمومی نیست)
    if cat_name and cat_name not in common_words:
        tags.add(cat_name)

    # 3. ویژگی‌های مهم (رنگ، حافظه، ظرفیت، سایز)
    important_keys = ['رنگ', 'Color', 'حافظه', 'ظرفیت', 'اندازه', 'سایز', 'Size', 'مدل', 'برند']
    for key, value in specs.items():
        if any(imp in key for imp in important_keys):
            val = value.strip()
            if 2 < len(val) < 30 and val not in common_words:
                tags.add(val)

    # 4. برچسب قیمت (اقتصادی/لوکس)
    if price > 0:
        if price < 5000000:
            tags.add('اقتصادی')
        elif price > 20000000:
            tags.add('لوکس')

    # 5. برچسب‌های عمومی سئو (فقط یک بار)
    tags.add('خرید آنلاین')
    tags.add('گارانتی دار')

    # 6. حذف برچسب‌های تکراری و اسپم
    tags = {t for t in tags if t and len(t) <= 30 and t.lower() not in ['test', 'spam', 'محصول', 'کالا']}

    # 7. تبدیل به فرمت ووکامرس
    return [{"name": t} for t in sorted(tags)]

# ==============================================================================
# --- ارسال محصول به ووکامرس با برچسب هوشمند ---
# ==============================================================================
def process_product_wrapper(args):
    product, stats, category_mapping, cat_map = args
    try:
        wc_cat_id = category_mapping.get(product.get('category_id'))
        if not wc_cat_id:
            logger.warning(f"   ⚠️ دسته برای محصول {product.get('id')} پیدا نشد. رد کردن...")
            with stats['lock']:
                stats['no_category'] = stats.get('no_category', 0) + 1
            return
        specs = product.get('specs', {})
        if not specs:
            logger.warning(f"   ⚠️ مشخصات برای محصول {product.get('id')} خالی است. ارسال بدون attributes.")
        attributes = []
        position = 0
        for key, value in specs.items():
            attributes.append({
                "name": key,
                "options": [value],
                "position": position,
                "visible": True,
                "variation": False
            })
            position += 1

        # برچسب‌های هوشمند سئو
        tags = smart_tags_for_product(product, cat_map)

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
            "attributes": attributes,
            "tags": tags
        }
        _send_to_woocommerce(wc_data['sku'], wc_data, stats)
        time.sleep(random.uniform(0.5, 1.5))
    except Exception as e:
        logger.error(f"   ❌ خطای جدی در پردازش محصول {product.get('id', '')}: {e}")
        with stats['lock']: stats['failed'] += 1

# ==============================================================================
# --- پرینت محصولات به صورت شاخه‌ای و مرتب ---
# ==============================================================================
def print_products_tree(products, categories):
    cat_map = {cat['id']: cat['name'] for cat in categories}
    tree = defaultdict(list)
    for key, product in products.items():
        pid, catid = key.split('|')
        tree[catid].append(product)
    for catid in sorted(tree, key=lambda x: int(x)):
        logger.info(f"دسته [{catid}] {cat_map.get(int(catid), 'نامشخص')}:")
        for p in sorted(tree[catid], key=lambda x: int(x['id'])):
            logger.info(f"   - {p['name']} (ID: {p['id']})")

# ==============================================================================
# --- ناموجود کردن محصولات حذف‌شده از eways در ووکامرس ---
# ==============================================================================
def mark_deleted_products_outofstock(all_products, wc_api_url, wc_consumer_key, wc_consumer_secret):
    logger.info("🔎 بررسی محصولات حذف‌شده و ناموجود کردن آن‌ها در ووکامرس...")
    page = 1
    wc_skus = set()
    wc_ids = {}
    while True:
        res = requests.get(
            f"{wc_api_url}/products",
            auth=(wc_consumer_key, wc_consumer_secret),
            params={"per_page": 100, "page": page, "sku": "EWAYS-"},
            verify=False
        )
        res.raise_for_status()
        data = res.json()
        if not data:
            break
        for p in data:
            sku = p.get("sku", "")
            if sku.startswith("EWAYS-"):
                wc_skus.add(sku)
                wc_ids[sku] = p["id"]
        if len(data) < 100:
            break
        page += 1

    current_skus = set(f"EWAYS-{p['id']}" for p in all_products.values())
    deleted_skus = wc_skus - current_skus
    logger.info(f"🟠 تعداد محصولات حذف‌شده از eways که باید ناموجود شوند: {len(deleted_skus)}")

    for sku in tqdm(deleted_skus, desc="ناموجود کردن محصولات حذف‌شده"):
        product_id = wc_ids[sku]
        try:
            res = requests.put(
                f"{wc_api_url}/products/{product_id}",
                auth=(wc_consumer_key, wc_consumer_secret),
                json={"stock_quantity": 0, "stock_status": "outofstock"},
                verify=False
            )
            if res.status_code in [200, 201]:
                logger.info(f"✅ محصول {sku} (ID: {product_id}) ناموجود شد.")
            else:
                logger.warning(f"❌ خطا در ناموجود کردن محصول {sku}: {res.text}")
        except Exception as e:
            logger.error(f"❌ خطا در ناموجود کردن محصول {sku}: {e}")

# ==============================================================================
# --- تابع اصلی ---
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

    SELECTED_IDS_STRING = "1582:14548-allz,1584-all-allz|16777:all-allz|4882:all-allz|16778:22570-all-allz"
    parsed_selection = parse_selected_ids_string(SELECTED_IDS_STRING)
    logger.info(f"✅ انتخاب‌های دلخواه: {parsed_selection}")

    filtered_categories = get_selected_categories_according_to_selection(parsed_selection, all_cats)
    logger.info(f"✅ دسته‌بندی‌های نهایی: {[cat['name'] for cat in filtered_categories]}")

    category_mapping = transfer_categories_to_wc(filtered_categories)
    if not category_mapping:
        logger.error("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد. برنامه خاتمه می‌یابد.")
        return
    logger.info(f"✅ مرحله 5: انتقال دسته‌بندی‌ها کامل شد. تعداد نگاشت‌شده: {len(category_mapping)}")

    cached_products = load_cache()

    # --- کنترل هوشمند سرعت و تاخیر در دریافت محصولات ---
    max_workers = 3
    delay = 0.5
    min_workers = 1
    max_max_workers = 6
    min_delay = 0.2
    max_delay = 2.0

    selected_ids = [cat['id'] for cat in filtered_categories]
    all_products = {}
    product_queue = Queue()
    logger.info(f"\n⏳ شروع فرآیند جمع‌آوری تمام محصولات از همه دسته‌بندی‌های انتخابی و زیرمجموعه‌ها...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_catid = {}
        for cat_id in selected_ids:
            future = executor.submit(get_products_from_category_page, session, cat_id, 10, delay)
            future_to_catid[future] = cat_id

        pbar = tqdm(total=len(selected_ids), desc="دریافت محصولات دسته‌ها")
        for future in as_completed(future_to_catid):
            cat_id = future_to_catid[future]
            try:
                products_in_cat = future.result()
                for product in products_in_cat:
                    key = f"{product['id']}|{cat_id}"
                    all_products[key] = product
                    product_queue.put(product)
                # اگر موفقیت‌آمیز بود، سرعت را کمی زیاد کن (تا سقف)
                if max_workers < max_max_workers:
                    max_workers += 1
                if delay > min_delay:
                    delay = max(min_delay, delay - 0.05)
            except Exception as e:
                logger.warning(f"⚠️ خطا در دریافت محصولات دسته {cat_id}: {e}")
                # اگر خطای بلاک شدن بود، سرعت را کم کن و تاخیر را زیاد کن
                if "Blocked" in str(e) or "rate limited" in str(e):
                    if max_workers > min_workers:
                        max_workers -= 1
                    if delay < max_delay:
                        delay = min(max_delay, delay + 0.2)
                    logger.warning(f"🚦 سرعت کم شد: max_workers={max_workers}, delay={delay:.2f}")
            pbar.update(1)
        pbar.close()

    logger.info(f"✅ مرحله 6: استخراج محصولات کامل شد. تعداد استخراج‌شده: {len(all_products)}")

    # پرینت محصولات به صورت شاخه‌ای و مرتب
    print_products_tree(all_products, filtered_categories)

    # --- ادغام کش و آمار محصولات جدید بر اساس دسته ---
    updated_products = {}
    changed_count = 0
    new_products_by_category = {}

    for key, p in all_products.items():
        if key in cached_products and cached_products[key]['price'] == p['price'] and cached_products[key]['stock'] == p['stock'] and cached_products[key]['specs'] == p['specs']:
            updated_products[key] = cached_products[key]
        else:
            updated_products[key] = p
            changed_count += 1
            cat_id = p['category_id']
            new_products_by_category[cat_id] = new_products_by_category.get(cat_id, 0) + 1

    logger.info(f"✅ مرحله 7: ادغام با کش کامل شد. تعداد محصولات تغییرشده/جدید برای ارسال: {changed_count}")

    # نمایش آمار محصولات جدید بر اساس دسته
    logger.info("📊 آمار محصولات جدید/تغییر یافته بر اساس دسته‌بندی:")
    cat_map = {cat['id']: cat['name'] for cat in filtered_categories}
    for cat_id, count in sorted(new_products_by_category.items(), key=lambda x: -x[1]):
        logger.info(f"  - {cat_map.get(cat_id, str(cat_id))}: {count} محصول جدید/تغییر یافته")

    save_cache(updated_products)

    stats = {'created': 0, 'updated': 0, 'failed': 0, 'no_category': 0, 'lock': Lock()}
    logger.info(f"\n🚀 شروع پردازش و ارسال {changed_count} محصول (تغییرشده/جدید) به ووکامرس...")

    # ارسال محصولات به ووکامرس با ترد جداگانه و صف مرکزی
    def worker():
        while True:
            try:
                product = product_queue.get_nowait()
            except Exception:
                break
            process_product_wrapper((product, stats, category_mapping, cat_map))
            product_queue.task_done()

    num_workers = 3
    threads = []
    for _ in range(num_workers):
        t = Thread(target=worker)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    logger.info("\n===============================")
    logger.info(f"📦 محصولات پردازش شده: {changed_count}")
    logger.info(f"🟢 ایجاد شده: {stats['created']}")
    logger.info(f"🔵 آپدیت شده: {stats['updated']}")
    logger.info(f"🔴 شکست‌خورده: {stats['failed']}")
    logger.info(f"🟡 بدون دسته: {stats.get('no_category', 0)}")
    logger.info("===============================")

    # --- ناموجود کردن محصولات حذف‌شده از eways در ووکامرس ---
    mark_deleted_products_outofstock(
        all_products,
        WC_API_URL,
        WC_CONSUMER_KEY,
        WC_CONSUMER_SECRET
    )

    logger.info("تمام!")

if __name__ == "__main__":
    main()
