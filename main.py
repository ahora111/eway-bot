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
import warnings

# Suppress InsecureRequestWarning
warnings.filterwarnings("ignore", category=requests.packages.urllib3.exceptions.InsecureRequestWarning)

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
    return [cat['id'] for cat in all_cats if cat.get('parent_id') == parent_id]

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
        selected_ids.add(parent_id) # همیشه دسته اصلی انتخاب می‌شود
        for sel in block['selections']:
            sel_id = sel['id']
            sel_type = sel['type']
            
            if sel_type == 'all_subcats': # فقط زیرمجموعه‌های مستقیم
                selected_ids.update(get_direct_subcategories(sel_id, all_cats))
            elif sel_type == 'only_products': # فقط خود دسته (که قبلا اضافه شده)
                selected_ids.add(sel_id)
            elif sel_type == 'all_subcats_and_products': # خود دسته و تمام زیرمجموعه‌ها به صورت بازگشتی
                selected_ids.add(sel_id)
                selected_ids.update(get_all_subcategories(sel_id, all_cats))

    return [cat for cat in all_cats if cat['id'] in selected_ids]

# ==============================================================================
# --- تنظیمات لاگینگ ---
# ==============================================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# ==============================================================================
# --- اطلاعات ووکامرس و سایت مبدا ---
# ==============================================================================
BASE_URL = "https://panel.eways.co"
SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"
PRODUCT_LIST_URL_TEMPLATE = f"{BASE_URL}/Store/List/{{category_id}}/2/2/0/0/0/10000000000?page={{page}}"
PRODUCT_DETAIL_URL_TEMPLATE = f"{BASE_URL}/Store/Detail/{{cat_id}}/{{product_id}}"

WC_API_URL = os.environ.get("WC_API_URL", "https://your-woocommerce-site.com/wp-json/wc/v3")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY", "ck_xxx")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET", "cs_xxx")

EWAYS_USERNAME = os.environ.get("EWAYS_USERNAME", "شماره موبایل یا یوزرنیم")
EWAYS_PASSWORD = os.environ.get("EWAYS_PASSWORD", "پسورد")

CACHE_FILE = 'products_cache_v2.json'  # ### تغییر کلیدی: نام فایل کش جدید برای جلوگیری از تداخل

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
    try:
        resp = session.post(login_url, data=payload, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"❌ خطای شبکه هنگام لاگین: {e}")
        return None

    if 'Aut' in session.cookies:
        logger.info("✅ لاگین موفق! کوکی Aut دریافت شد.")
        return session
    else:
        response_text = resp.text.lower()
        if "کپچا" in response_text or "captcha" in response_text:
            logger.error("❌ کوکی Aut دریافت نشد. کپچا فعال است.")
        elif "نام کاربری" in response_text or "رمز عبور" in response_text:
            logger.error("❌ کوکی Aut دریافت نشد. نام کاربری یا رمز عبور اشتباه است.")
        else:
            logger.error(f"❌ کوکی Aut دریافت نشد. لاگین ناموفق. پاسخ سرور: {resp.text[:200]}")
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
        specs_table = soup.select_one('#link1 .table-responsive table, .table-responsive table, table.table')
        if not specs_table:
            logger.debug(f"      - هیچ جدول مشخصاتی برای محصول {product_id} پیدا نشد.")
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
        logger.debug(f"      - {len(specs)} مشخصه فنی برای محصول {product_id} استخراج شد.")
        return specs
    except requests.exceptions.RequestException as e:
        logger.warning(f"      - خطا در دریافت جزئیات محصول {product_id}: {e}. تلاش مجدد...")
        raise
    except Exception as e:
        logger.error(f"      - خطای غیرمنتظره در استخراج مشخصات محصول {product_id}: {e}")
        return {}

def get_and_parse_categories(session):
    logger.info(f"⏳ دریافت دسته‌بندی‌ها از: {SOURCE_CATS_API_URL}")
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
        logger.info(f"✅ تعداد {len(final_cats)} دسته‌بندی از JSON استخراج شد.")
        return final_cats
    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.error(f"❌ خطا در دریافت یا پردازش دسته‌بندی‌ها: {e}")
        return None

# ### تغییر کلیدی ###
# افزایش max_pages برای اطمینان از خواندن تمام محصولات
@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)),
    stop=stop_after_attempt(4),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True
)
def get_products_from_category_page(session, category_id, max_pages=100, delay=0.5):
    all_products_in_category = []
    seen_product_ids_this_run = set()
    page_num = 1
    
    while page_num <= max_pages:
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page_num)
        logger.debug(f"  - در حال دریافت محصولات از: {url}")
        try:
            response = session.get(url, timeout=30)
            if response.status_code in [429, 503, 403]:
                raise requests.exceptions.HTTPError(f"Blocked or rate limited: {response.status_code}", response=response)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'lxml')
            product_blocks = soup.select(".goods_item.goods-record")
            
            if not product_blocks:
                logger.info(f"    - هیچ محصولی در صفحه {page_num} از دسته {category_id} یافت نشد. پایان صفحه‌بندی.")
                break

            found_new_product = False
            for block in product_blocks:
                if block.select_one(".goods-record-unavailable"):
                    continue
                
                a_tag = block.select_one("a[href*='/Store/Detail/']")
                if not a_tag:
                    continue

                href = a_tag['href']
                match = re.search(r'/Store/Detail/\d+/(\d+)', href)
                product_id = match.group(1) if match else None

                if not product_id or product_id in seen_product_ids_this_run:
                    continue

                name = block.select_one("span.goods-record-title").text.strip()
                price_text = block.select_one("span.goods-record-price").text.strip()
                price = re.sub(r'[^\d]', '', price_text)
                image_url = block.select_one("img.goods-record-image").get('data-src', '')

                if not name or not price or int(price) <= 0:
                    continue

                # دریافت جزئیات فقط یکبار برای هر محصول انجام می‌شود، اینجا فقط اطلاعات پایه را جمع‌آوری می‌کنیم
                product = {
                    "id": product_id,
                    "name": name,
                    "price": price,
                    "stock": 1, # موجود فرض می‌شود
                    "image": image_url,
                    "category_id": category_id,
                }
                all_products_in_category.append(product)
                seen_product_ids_this_run.add(product_id)
                found_new_product = True

            if not found_new_product:
                logger.info(f"    - محصول جدیدی در این صفحه یافت نشد، توقف صفحه‌بندی برای دسته {category_id}.")
                break

            page_num += 1
            time.sleep(random.uniform(delay, delay + 0.2))

        except requests.exceptions.RequestException as e:
            logger.error(f"    - خطا در پردازش صفحه محصولات دسته {category_id}: {e}")
            raise # اجازه می‌دهیم tenacity دوباره تلاش کند

    logger.info(f"    - تعداد کل محصولات استخراج‌شده از دسته {category_id}: {len(all_products_in_category)}")
    return all_products_in_category

# ==============================================================================
# --- کش برای محصولات (کلید: product_id) ---
# ==============================================================================
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
                logger.info(f"✅ کش بارگذاری شد. تعداد محصولات در کش: {len(cache)}")
                return cache
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"❌ خطا در بارگذاری فایل کش: {e}. یک کش جدید ساخته خواهد شد.")
            return {}
    logger.info("⚠️ کش پیدا نشد. استخراج کامل انجام می‌شود.")
    return {}

def save_cache(products_data):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(products_data, f, ensure_ascii=False, indent=4)
        logger.info(f"✅ کش ذخیره شد. تعداد محصولات: {len(products_data)}")
    except IOError as e:
        logger.error(f"❌ خطا در ذخیره فایل کش: {e}")

# ==============================================================================
# --- توابع ووکامرس ---
# ==============================================================================
def get_wc_categories():
    wc_cats, page = [], 1
    while True:
        try:
            res = requests.get(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), params={"per_page": 100, "page": page}, verify=False, timeout=30)
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
    return {cat["id"]: cat for cat in wc_cats}

def transfer_categories_to_wc(source_categories):
    logger.info("\n⏳ شروع انتقال دسته‌بندی‌ها به ووکامرس...")
    # ... (کد این بخش بدون تغییر باقی می‌ماند) ...
    return source_to_wc_id_map # فرض بر این است که این تابع همانطور که بود کار می‌کند

def process_price(price_value):
    try:
        price_value = float(re.sub(r'[^\d.]', '', str(price_value)))
        price_value /= 10 # تبدیل ریال به تومان
    except (ValueError, TypeError): return "0"
    if price_value <= 1: return "0"
    elif price_value <= 7000000: new_price = price_value + 260000
    elif price_value <= 10000000: new_price = price_value * 1.035
    elif price_value <= 20000000: new_price = price_value * 1.025
    elif price_value <= 30000000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price, -4))) # رند کردن به نزدیک‌ترین ۱۰ هزار تومان

@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True
)
def _send_to_woocommerce(sku, data, stats):
    try:
        auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
        logger.debug(f"   - چک کردن SKU {sku} در ووکامرس...")
        check_url = f"{WC_API_URL}/products?sku={sku}"
        r_check = requests.get(check_url, auth=auth, verify=False, timeout=20)
        r_check.raise_for_status()
        existing = r_check.json()

        if existing:
            product_id = existing[0]['id']
            # ### تغییر کلیدی ###
            # هنگام آپدیت، دسته‌بندی‌ها هم ارسال می‌شوند
            update_data = {
                "regular_price": data["regular_price"],
                "stock_quantity": data["stock_quantity"],
                "stock_status": data["stock_status"],
                "attributes": data["attributes"],
                "tags": data.get("tags", []),
                "categories": data["categories"] # اطمینان از اینکه محصول در همه دسته‌های صحیح قرار دارد
            }
            logger.debug(f"   - آپدیت محصول {product_id} با {len(update_data['attributes'])} مشخصه و {len(update_data['categories'])} دسته...")
            res = requests.put(f"{WC_API_URL}/products/{product_id}", auth=auth, json=update_data, verify=False, timeout=30)
            res.raise_for_status()
            with stats['lock']: stats['updated'] += 1
            logger.info(f"   ✅ محصول {sku} با موفقیت آپدیت شد.")
        else:
            logger.debug(f"   - ایجاد محصول جدید با SKU {sku} و {len(data['attributes'])} مشخصه فنی...")
            res = requests.post(f"{WC_API_URL}/products", auth=auth, json=data, verify=False, timeout=30)
            res.raise_for_status()
            with stats['lock']: stats['created'] += 1
            logger.info(f"   ✅ محصول {sku} با موفقیت ایجاد شد.")
    except requests.exceptions.HTTPError as e:
        error_text = e.response.text if e.response else "No response body"
        logger.error(f"   ❌ خطای HTTP برای SKU {sku}: {e.response.status_code} - پاسخ: {error_text}")
        raise
    except Exception as e:
        logger.error(f"   ❌ خطای کلی در ارتباط با ووکامرس برای SKU {sku}: {e}")
        raise

# ==============================================================================
# --- برچسب‌گذاری هوشمند سئو محور (بدون تغییر) ---
# ==============================================================================
def smart_tags_for_product(product, cat_map):
    # ... (کد این بخش بدون تغییر باقی می‌ماند) ...
    return [{"name": t} for t in sorted(tags)]

# ### تغییر کلیدی ###
# این تابع حالا یک محصول گروه‌بندی شده با لیستی از دسته‌ها را پردازش می‌کند
def process_product_wrapper(args):
    source_id, product_data, stats, category_mapping, cat_map, session = args
    try:
        product_details = product_data['details']
        source_cat_ids = list(product_data['categories'])

        # تبدیل شناسه‌های دسته‌بندی مبدا به شناسه‌های ووکامرس
        wc_cat_ids = [category_mapping.get(cat_id) for cat_id in source_cat_ids]
        wc_cat_ids = [cid for cid in wc_cat_ids if cid is not None]

        if not wc_cat_ids:
            logger.warning(f"   ⚠️ هیچ دسته‌بندی ووکامرسی برای محصول {source_id} یافت نشد. رد کردن...")
            with stats['lock']: stats['no_category'] += 1
            return

        # دریافت مشخصات فنی محصول (فقط یک بار)
        # فرض می‌کنیم مشخصات در همه دسته‌ها یکسان است، پس از اولین دسته استفاده می‌کنیم
        specs = get_product_details(session, source_cat_ids[0], source_id)
        
        attributes = []
        for i, (key, value) in enumerate(specs.items()):
            attributes.append({
                "name": key, "options": [value], "position": i,
                "visible": True, "variation": False
            })

        tags = smart_tags_for_product(product_details, cat_map)

        wc_data = {
            "name": product_details.get('name', 'بدون نام'),
            "type": "simple",
            "sku": f"EWAYS-{source_id}",
            "regular_price": process_price(product_details.get('price', 0)),
            "categories": [{"id": wc_id} for wc_id in wc_cat_ids],
            "images": [{"src": product_details.get("image")}] if product_details.get("image") else [],
            "stock_quantity": product_details.get('stock', 0),
            "manage_stock": True,
            "stock_status": "instock" if product_details.get('stock', 0) > 0 else "outofstock",
            "attributes": attributes,
            "tags": tags
        }

        _send_to_woocommerce(wc_data['sku'], wc_data, stats)
        time.sleep(random.uniform(0.2, 0.8)) # کاهش تاخیر چون جزئیات قبلا گرفته شده

    except Exception as e:
        logger.error(f"   ❌ خطای جدی در پردازش محصول {source_id}: {e}", exc_info=True)
        with stats['lock']: stats['failed'] += 1

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
    cat_map = {cat['id']: cat['name'] for cat in all_cats}
    logger.info(f"✅ مرحله ۱: بارگذاری دسته‌بندی‌ها کامل شد. تعداد: {len(all_cats)}")

    SELECTED_IDS_STRING = "16777:all-allz|4882:all-allz|16778:22570-all-allz"
    parsed_selection = parse_selected_ids_string(SELECTED_IDS_STRING)
    filtered_categories = get_selected_categories_according_to_selection(parsed_selection, all_cats)
    selected_cat_ids = {cat['id'] for cat in filtered_categories}
    logger.info(f"✅ دسته‌بندی‌های نهایی برای اسکرپ: {len(filtered_categories)} عدد")

    category_mapping = transfer_categories_to_wc(filtered_categories)
    if not category_mapping:
        logger.error("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد. برنامه خاتمه می‌یابد.")
        return
    logger.info(f"✅ مرحله ۲: انتقال دسته‌بندی‌ها کامل شد.")

    # ### تغییر کلیدی ###
    # مرحله استخراج تمام محصولات قبل از هر پردازش دیگری
    all_products_raw = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_catid = {executor.submit(get_products_from_category_page, session, cat_id): cat_id for cat_id in selected_cat_ids}
        
        pbar = tqdm(as_completed(future_to_catid), total=len(selected_cat_ids), desc="دریافت محصولات از دسته‌ها")
        for future in pbar:
            cat_id = future_to_catid[future]
            try:
                products_in_cat = future.result()
                all_products_raw.extend(products_in_cat)
            except Exception as e:
                logger.error(f"⚠️ خطا در دریافت محصولات دسته {cat_id}: {e}")
        
    logger.info(f"✅ مرحله ۳: استخراج خام کامل شد. تعداد کل رکوردهای محصول (با تکرار): {len(all_products_raw)}")

    # ### تغییر کلیدی ###
    # مرحله گروه‌بندی محصولات بر اساس شناسه یکتا
    products_by_source_id = defaultdict(lambda: {"details": None, "categories": set()})
    for p in all_products_raw:
        source_id = p['id']
        products_by_source_id[source_id]['details'] = p
        products_by_source_id[source_id]['categories'].add(p['category_id'])

    logger.info(f"✅ مرحله ۴: گروه‌بندی کامل شد. تعداد محصولات یکتا: {len(products_by_source_id)}")
    
    # لاگ کردن محصولات چند دسته‌ای
    for pid, data in products_by_source_id.items():
        if len(data['categories']) > 1:
            cat_names = [cat_map.get(cid, str(cid)) for cid in data['categories']]
            logger.debug(f"  - محصول چند دسته‌ای: {data['details']['name']} (ID: {pid}) در دسته‌های: {', '.join(cat_names)}")

    # ### تغییر کلیدی ###
    # مقایسه با کش و شناسایی محصولات جدید/تغییریافته
    cached_products = load_cache()
    products_to_send = {}
    
    for source_id, current_data in products_by_source_id.items():
        is_changed = True
        if source_id in cached_products:
            cached_data = cached_products[source_id]
            # مقایسه قیمت، موجودی و مجموعه دسته‌بندی‌ها
            if (cached_data.get('price') == current_data['details']['price'] and
                set(cached_data.get('categories', [])) == current_data['categories']):
                is_changed = False
        
        if is_changed:
            products_to_send[source_id] = current_data
        
    logger.info(f"✅ مرحله ۵: مقایسه با کش کامل شد. تعداد محصولات جدید/تغییریافته برای ارسال: {len(products_to_send)}")

    # ساخت کش جدید برای ذخیره‌سازی
    new_cache_data = {}
    for source_id, data in products_by_source_id.items():
        new_cache_data[source_id] = {
            "price": data['details']['price'],
            "categories": list(data['categories'])
        }
    save_cache(new_cache_data)

    if not products_to_send:
        logger.info("✅ هیچ محصول جدید یا تغییریافته‌ای برای ارسال به ووکامرس وجود ندارد. پایان کار.")
        return

    # ### تغییر کلیدی ###
    # ارسال محصولات شناسایی شده به ووکامرس
    stats = {'created': 0, 'updated': 0, 'failed': 0, 'no_category': 0, 'lock': Lock()}
    logger.info(f"\n🚀 شروع پردازش و ارسال {len(products_to_send)} محصول به ووکامرس...")
    
    tasks = [(source_id, data, stats, category_mapping, cat_map, session) for source_id, data in products_to_send.items()]

    with ThreadPoolExecutor(max_workers=3) as executor:
        list(tqdm(executor.map(process_product_wrapper, tasks), total=len(tasks), desc="ارسال به ووکامرس"))

    logger.info("\n===============================")
    logger.info(f"📦 محصولات پردازش شده: {len(products_to_send)}")
    logger.info(f"🟢 ایجاد شده: {stats['created']}")
    logger.info(f"🔵 آپدیت شده: {stats['updated']}")
    logger.info(f"🔴 شکست‌خورده: {stats['failed']}")
    logger.info(f"🟡 بدون دسته ووکامرس: {stats['no_category']}")
    logger.info("===============================\nتمام!")

if __name__ == "__main__":
    main()
