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
# این بخش جایگزین کامل منطق انتخاب قبلی شما شده است.
# ==============================================================================

def get_all_descendants(parent_id, all_cats_map):
    """تمام نوادگان (زیرمجموعه‌های تمام سطوح) یک دسته را به صورت بازگشتی پیدا می‌کند."""
    descendants = set()
    # پیدا کردن فرزندان مستقیم
    children = [cat['id'] for cat in all_cats_map.values() if cat.get('parent_id') == parent_id]
    for child_id in children:
        descendants.add(child_id)
        # پیدا کردن نوادگان هر فرزند
        descendants.update(get_all_descendants(child_id, all_cats_map))
    return descendants

def process_selection_rules(rule_string, all_cats):
    """
    رشته قوانین را پردازش کرده و دو لیست ID مجزا برمی‌گرداند:
    1. structure_ids: تمام IDهایی که برای حفظ ساختار درختی در ووکامرس لازمند.
    2. product_ids: تمام IDهایی که باید محصولاتشان از سایت مبدا استخراج شوند.
    """
    structure_ids = set()
    product_ids = set()

    # ساخت یک دیکشنری از دسته‌بندی‌ها برای جستجوی سریع با ID
    all_cats_map = {cat['id']: cat for cat in all_cats}

    for rule in rule_string.split('|'):
        rule = rule.strip()
        if not rule or ':' not in rule:
            continue

        try:
            parent_id_str, selections_str = rule.split(':', 1)
            parent_id = int(parent_id_str.strip())

            if parent_id not in all_cats_map:
                logger.warning(f"⚠️ شناسه والد {parent_id} در قانون '{rule}' یافت نشد. رد شدن...")
                continue
            
            # دسته والد همیشه برای حفظ ساختار لازم است
            structure_ids.add(parent_id)

            for sel in selections_str.split(','):
                sel = sel.strip()
                if not sel: continue

                # حالت ۱: قوانین روی خود دسته والد اعمال می‌شوند (all, allz, all-allz)
                if sel == 'all': # تمام زیرمجموعه‌های مستقیم (ساختار) و محصولاتشان (محصول)
                    direct_children = [cat['id'] for cat in all_cats if cat.get('parent_id') == parent_id]
                    structure_ids.update(direct_children)
                    product_ids.update(direct_children)

                elif sel == 'allz': # فقط محصولات خود دسته والد
                    product_ids.add(parent_id)

                elif sel == 'all-allz': # محصولات والد + تمام نوادگان و محصولاتشان
                    product_ids.add(parent_id)
                    descendants = get_all_descendants(parent_id, all_cats_map)
                    structure_ids.update(descendants)
                    product_ids.update(descendants)
                
                # حالت ۲: قوانین روی یک زیرمجموعه خاص اعمال می‌شوند (مثلا: 14548-allz)
                else:
                    match = re.match(r'^(\d+)-(.+)$', sel)
                    if not match:
                        logger.warning(f"⚠️ فرمت انتخاب '{sel}' در قانون '{rule}' نامعتبر است.")
                        continue
                    
                    child_id, command = int(match.group(1)), match.group(2)

                    if child_id not in all_cats_map:
                        logger.warning(f"⚠️ شناسه فرزند {child_id} در قانون '{rule}' یافت نشد.")
                        continue

                    # فرزند همیشه برای حفظ ساختار لازم است
                    structure_ids.add(child_id)

                    if command == 'allz': # فقط محصولات این فرزند
                        product_ids.add(child_id)
                    
                    elif command == 'all-allz': # محصولات این فرزند + تمام نوادگان و محصولاتشان
                        product_ids.add(child_id)
                        descendants = get_all_descendants(child_id, all_cats_map)
                        structure_ids.update(descendants)
                        product_ids.update(descendants)
                    else:
                        logger.warning(f"⚠️ دستور '{command}' برای فرزند {child_id} نامعتبر است.")
        except Exception as e:
            logger.error(f"❌ خطای جدی در پردازش قانون '{rule}': {e}")


    return list(structure_ids), list(product_ids)

# ==============================================================================
# --- تنظیمات لاگینگ ---
# ==============================================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s') # سطح لاگ به INFO تغییر کرد برای خروجی تمیزتر
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=1024*1024, backupCount=5, encoding='utf-8')
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
                    logger.debug(f"      - هیچ جدولی برای محصول {product_id} پیدا نشد.")
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
        
        logger.debug(f"      - مشخصات استخراج‌شده برای {product_id}: {specs}")
        return specs
    except requests.exceptions.RequestException as e:
        logger.warning(f"      - خطا در دریافت جزئیات محصول {product_id}: {e}. تلاش مجدد...")
        raise
    except Exception as e:
        logger.warning(f"      - خطا در استخراج مشخصات محصول {product_id}: {e}")
        return {}

def get_products_from_category_page(session, category_id, max_pages=100):
    all_products_in_category = []
    seen_product_ids = set()
    page_num = 1
    while page_num <= max_pages:
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page_num)
        logger.info(f"  - در حال دریافت محصولات از: {url}")
        try:
            response = session.get(url, timeout=30)
            if response.status_code != 200: break
            soup = BeautifulSoup(response.text, 'lxml')
            product_blocks = soup.select(".goods_item.goods-record")
            if not product_blocks:
                logger.info("    - هیچ محصولی در این صفحه یافت نشد. پایان صفحه‌بندی.")
                break
            
            current_page_product_ids = []
            for block in product_blocks:
                try:
                    if block.select_one(".goods-record-unavailable"):
                        continue

                    a_tag = block.select_one("a")
                    href = a_tag['href'] if a_tag else None
                    match = re.search(r'/Store/Detail/\d+/(\d+)', href) if href else None
                    product_id = match.group(1) if match else None
                    if not product_id or product_id in seen_product_ids or product_id.startswith('##'):
                        continue

                    name = block.select_one("span.goods-record-title").text.strip()
                    price_text = block.select_one("span.goods-record-price").text.strip()
                    price = re.sub(r'[^\d]', '', price_text)
                    image_url = block.select_one("img.goods-record-image").get('data-src', '')

                    if not all([name, price, int(price) > 0]):
                        continue

                    specs = get_product_details(session, category_id, product_id)
                    time.sleep(random.uniform(0.3, 0.8))

                    product = {
                        "id": product_id, "name": name, "price": price, "stock": 1,
                        "image": image_url, "category_id": category_id, "specs": specs
                    }
                    seen_product_ids.add(product_id)
                    current_page_product_ids.append(product_id)
                    all_products_in_category.append(product)
                    logger.info(f"      - محصول {product_id} ({name}) اضافه شد.")
                except Exception as e:
                    logger.warning(f"      - خطا در پردازش یک بلاک محصول: {e}. رد شدن...")
            
            if not current_page_product_ids:
                logger.info("    - محصول جدیدی در این صفحه یافت نشد، توقف صفحه‌بندی.")
                break
            page_num += 1
            time.sleep(random.uniform(0.5, 1.5))
        except requests.RequestException as e:
            logger.error(f"    - خطای شبکه در پردازش صفحه محصولات: {e}")
            break
        except Exception as e:
            logger.error(f"    - خطای کلی در پردازش صفحه محصولات: {e}")
            break
    return all_products_in_category

# ==============================================================================
# --- کش برای محصولات ---
# ==============================================================================
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
                logger.info(f"✅ کش بارگذاری شد. تعداد محصولات در کش: {len(cache)}")
                return cache
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"❌ خطا در خواندن یا پارس فایل کش: {e}. یک کش جدید ساخته خواهد شد.")
            return {}
    logger.info("⚠️ کش پیدا نشد. استخراج کامل انجام می‌شود.")
    return {}

def save_cache(products):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(products, f, ensure_ascii=False, indent=4)
        logger.info(f"✅ کش ذخیره شد. تعداد محصولات: {len(products)}")
    except IOError as e:
        logger.error(f"❌ خطا در ذخیره فایل کش: {e}")


# ==============================================================================
# --- توابع ووکامرس ---
# ==============================================================================
def transfer_categories_to_wc(source_categories, all_cats_from_source):
    logger.info("\n⏳ شروع انتقال دسته‌بندی‌ها به ووکامرس...")
    
    # ساخت یک نقشه از ID به آبجکت برای جستجوی سریع والدها
    source_cat_map = {cat['id']: cat for cat in all_cats_from_source}
    
    # مرتب‌سازی دسته‌ها برای اطمینان از اینکه والدها قبل از فرزندان ساخته می‌شوند
    sorted_cats = sorted(source_categories, key=lambda c: (source_cat_map.get(c.get('parent_id'), {}).get('name', ''), c['name']))

    # نقشه برای نگهداری ID های ساخته شده در ووکامرس (Source ID -> WC ID)
    source_to_wc_id_map = {}
    
    # tqdm برای نمایش پیشرفت
    for cat in tqdm(sorted_cats, desc="انتقال دسته‌ها به ووکامرس"):
        name = cat["name"].strip()
        source_parent_id = cat.get("parent_id")
        
        # پیدا کردن ID والد در ووکامرس از روی نقشه
        wc_parent_id = source_to_wc_id_map.get(source_parent_id, 0)
        
        # بررسی وجود دسته با نام و والد یکسان در ووکامرس
        try:
            res_check = requests.get(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), 
                                     params={"search": name, "parent": wc_parent_id}, verify=False)
            res_check.raise_for_status()
            existing_cats = res_check.json()
            
            exact_match = next((wc_cat for wc_cat in existing_cats if wc_cat['name'].strip() == name and wc_cat['parent'] == wc_parent_id), None)
            
            if exact_match:
                source_to_wc_id_map[cat["id"]] = exact_match["id"]
                continue # دسته از قبل وجود دارد، به بعدی برو
        except Exception as e:
            logger.warning(f"⚠️ خطا در بررسی وجود دسته '{name}': {e}")

        # ساخت دسته جدید
        data = {"name": name, "parent": wc_parent_id}
        try:
            res = requests.post(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
            if res.status_code in [200, 201]:
                new_id = res.json()["id"]
                source_to_wc_id_map[cat["id"]] = new_id
            else:
                error_data = res.json()
                if error_data.get("code") == "term_exists" and error_data.get("data", {}).get("resource_id"):
                    existing_id = error_data["data"]["resource_id"]
                    source_to_wc_id_map[cat["id"]] = existing_id
                else:
                    logger.error(f"❌ خطا در ساخت دسته '{name}': {res.text}")
        except Exception as e:
            logger.error(f"❌ خطای شبکه در ساخت دسته '{name}': {e}")

    logger.info(f"✅ انتقال دسته‌بندی‌ها کامل شد. تعداد نگاشت‌شده: {len(source_to_wc_id_map)}")
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
            logger.debug(f"   - آپدیت محصول {product_id}...")
            res = requests.put(f"{WC_API_URL}/products/{product_id}", auth=auth, json=update_data, verify=False, timeout=30)
            res.raise_for_status()
            with stats['lock']: stats['updated'] += 1
        else:
            logger.debug(f"   - ایجاد محصول جدید با SKU {sku}...")
            res = requests.post(f"{WC_API_URL}/products", auth=auth, json=data, verify=False, timeout=30)
            res.raise_for_status()
            with stats['lock']: stats['created'] += 1
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
            logger.warning(f"   ⚠️ دسته برای محصول {product.get('id')} در نقشه ووکامرس پیدا نشد. رد کردن...")
            return
            
        attributes = []
        for i, (key, value) in enumerate(product.get('specs', {}).items()):
            attributes.append({"name": key, "options": [value], "position": i, "visible": True, "variation": False})
            
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
        with stats['lock']: stats['failed'] += 1

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

    # --- تعریف و پردازش قوانین انتخاب ---
    # این رشته، قلب تپنده انتخاب‌های شماست. آن را با دقت ویرایش کنید.
    # فرمت: "ID_والد:ID_فرزند-دستور,ID_فرزند-دستور|ID_والد_دیگر:دستور_کلی"
    # دستورها: allz (فقط محصولات), all-allz (محصولات و تمام زیرمجموعه‌ها)
    SELECTED_IDS_STRING = "1582:14548-allz,1584-all-allz|16777:all-allz|2045:all-allz|16778:22570-all-allz"
    
    # پردازش قوانین برای گرفتن دو لیست مجزا
    structure_cat_ids, product_cat_ids = process_selection_rules(SELECTED_IDS_STRING, all_cats)
    
    logger.info(f"✅ IDهای ساختاری برای انتقال به ووکامرس: {structure_cat_ids}")
    logger.info(f"✅ IDهای محصول برای استخراج: {product_cat_ids}")

    # --- انتقال دسته‌های ساختاری به ووکامرس ---
    cats_for_wc_transfer = [cat for cat in all_cats if cat['id'] in structure_cat_ids]
    category_mapping = transfer_categories_to_wc(cats_for_wc_transfer, all_cats)
    if not category_mapping:
        logger.error("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد. برنامه خاتمه می‌یابد.")
        return
    logger.info(f"✅ مرحله ۲: انتقال دسته‌بندی‌های ساختاری کامل شد.")

    # --- استخراج محصولات از دسته‌های مشخص شده ---
    cached_products = load_cache()
    
    all_products = {}
    logger.info("\n⏳ شروع فرآیند جمع‌آوری تمام محصولات از دسته‌بندی‌های محاسبه‌شده...")
    for cat_id in tqdm(product_cat_ids, desc="دریافت محصولات"):
        products_in_cat = get_products_from_category_page(session, cat_id)
        for product in products_in_cat:
            all_products[product['id']] = product # استفاده از دیکشنری برای جلوگیری از محصول تکراری
    
    new_products_list = list(all_products.values())
    logger.info(f"\n✅ مرحله ۳: استخراج محصولات کامل شد. تعداد کل محصولات یکتا: {len(new_products_list)}")

    # --- مقایسه با کش و شناسایی تغییرات ---
    products_to_send = []
    updated_cache_data = {}
    for p in new_products_list:
        pid = p['id']
        cached_p = cached_products.get(pid)
        # اگر محصول جدید است یا قیمت، موجودی یا مشخصاتش تغییر کرده، آن را برای ارسال انتخاب کن
        if not cached_p or cached_p.get('price') != p.get('price') or cached_p.get('specs') != p.get('specs'):
            products_to_send.append(p)
        updated_cache_data[pid] = p # به‌روزرسانی کش با آخرین اطلاعات
        
    logger.info(f"✅ مرحله ۴: مقایسه با کش کامل شد. تعداد محصولات تغییرکرده/جدید برای ارسال: {len(products_to_send)}")

    # ذخیره کش جدید با اطلاعات به‌روز
    save_cache(updated_cache_data)

    if not products_to_send:
        logger.info("🎉 هیچ محصول جدید یا تغییرکرده‌ای برای ارسال وجود ندارد. کار تمام شد!")
        return

    # --- ارسال محصولات به ووکامرس ---
    stats = {'created': 0, 'updated': 0, 'failed': 0, 'lock': Lock()}
    logger.info(f"\n🚀 شروع پردازش و ارسال {len(products_to_send)} محصول به ووکامرس...")
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        args_list = [(p, stats, category_mapping) for p in products_to_send]
        list(tqdm(executor.map(process_product_wrapper, args_list), total=len(products_to_send), desc="ارسال محصولات"))

    logger.info("\n===============================")
    logger.info(f"📦 خلاصه عملیات:")
    logger.info(f"🟢 ایجاد شده: {stats['created']}")
    logger.info(f"🔵 آپدیت شده: {stats['updated']}")
    logger.info(f"🔴 شکست‌خورده: {stats['failed']}")
    logger.info("===============================\nتمام!")

if __name__ == "__main__":
    main()
