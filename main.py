import requests
import os
import re
import time
import json
import random
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from threading import Lock
from logging.handlers import RotatingFileHandler
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type
from collections import defaultdict
import logging
import warnings

# نادیده گرفتن هشدارهای مربوط به درخواست‌های ناامن (SSL)
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
    queue = [parent_id]
    while queue:
        current_id = queue.pop(0)
        direct_subs = get_direct_subcategories(current_id, all_cats)
        result.extend(direct_subs)
        queue.extend(direct_subs)
    return result

def get_selected_categories_according_to_selection(parsed_selection, all_cats):
    selected_ids = set()
    for block in parsed_selection:
        parent_id = block['parent_id']
        selected_ids.add(parent_id)
        for sel in block['selections']:
            sel_id = sel['id']
            sel_type = sel['type']
            if sel_type == 'all_subcats':
                selected_ids.update(get_direct_subcategories(sel_id, all_cats))
            elif sel_type == 'only_products':
                selected_ids.add(sel_id)
            elif sel_type == 'all_subcats_and_products':
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

CACHE_FILE = 'products_cache_v2.json'

# ==============================================================================
# --- تابع لاگین اتوماتیک به eways ---
# ==============================================================================
def login_eways(username, password):
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f"{BASE_URL}/", 'X-Requested-With': 'XMLHttpRequest', 'Accept-Language': 'en-US,en;q=0.9,fa;q=0.8'
    })
    session.verify = False

    login_url = f"{BASE_URL}/User/Login"
    payload = {"UserName": username, "Password": password, "RememberMe": "true"}
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
### تابع اصلاح شده و کامل برای دریافت دسته‌بندی‌ها
def get_and_parse_categories(session):
    logger.info(f"⏳ دریافت دسته‌بندی‌ها از: {SOURCE_CATS_API_URL}")
    try:
        response = session.get(SOURCE_CATS_API_URL, timeout=30)
        response.raise_for_status()

        # تلاش اول: پردازش به عنوان JSON
        try:
            data = response.json()
            logger.info("✅ پاسخ از نوع JSON است. در حال پردازش...")
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
            # تلاش دوم: اگر JSON نبود، به عنوان HTML پردازش کن
            logger.warning("⚠️ پاسخ JSON نیست. تلاش برای پارس کردن HTML...")
            soup = BeautifulSoup(response.text, 'lxml')
            all_menu_items = soup.select("li[id^='menu-item-']")
            
            if not all_menu_items:
                logger.error("❌ هیچ آیتم دسته‌بندی در HTML پیدا نشد.")
                return None

            logger.info(f"🔎 تعداد {len(all_menu_items)} آیتم منو در HTML پیدا شد. در حال پردازش...")
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
            logger.info(f"✅ تعداد {len(final_cats)} دسته‌بندی معتبر از HTML استخراج شد.")
            return final_cats

    except requests.RequestException as e:
        logger.error(f"❌ خطا در دریافت دسته‌بندی‌ها از سرور: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ خطای ناشناخته در پردازش دسته‌بندی‌ها: {e}", exc_info=True)
        return None

@retry(retry=retry_if_exception_type(requests.exceptions.RequestException), stop=stop_after_attempt(5), wait=wait_random_exponential(multiplier=1, max=10), reraise=True)
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
        
        specs = {cells[0].text.strip(): cells[1].text.strip() for row in specs_table.find_all("tr") if len(cells := row.find_all("td")) == 2 and cells[0].text.strip() and cells[1].text.strip()}
        logger.debug(f"      - {len(specs)} مشخصه فنی برای محصول {product_id} استخراج شد.")
        return specs
    except requests.exceptions.RequestException as e:
        logger.warning(f"      - خطا در دریافت جزئیات محصول {product_id}: {e}. تلاش مجدد...")
        raise
    except Exception as e:
        logger.error(f"      - خطای غیرمنتظره در استخراج مشخصات {product_id}: {e}")
        return {}

@retry(retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)), stop=stop_after_attempt(4), wait=wait_random_exponential(multiplier=1, max=10), reraise=True)
def get_products_from_category_page(session, category_id, max_pages=100, delay=0.5):
    all_products_in_category = []
    seen_product_ids_this_run = set()
    page_num = 1
    
    while page_num <= max_pages:
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page_num)
        logger.debug(f"  - دریافت از: {url}")
        try:
            response = session.get(url, timeout=30)
            if response.status_code in [429, 503, 403]:
                raise requests.exceptions.HTTPError(f"Blocked: {response.status_code}", response=response)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'lxml')
            product_blocks = soup.select(".goods_item.goods-record")
            
            if not product_blocks:
                logger.info(f"    - پایان محصولات در صفحه {page_num} از دسته {category_id}.")
                break

            found_new_product = False
            for block in product_blocks:
                if block.select_one(".goods-record-unavailable"): continue
                a_tag = block.select_one("a[href*='/Store/Detail/']")
                if not a_tag: continue

                match = re.search(r'/Store/Detail/\d+/(\d+)', a_tag['href'])
                product_id = match.group(1) if match else None

                if not product_id or product_id in seen_product_ids_this_run: continue
                
                name = block.select_one("span.goods-record-title").text.strip()
                price = re.sub(r'[^\d]', '', block.select_one("span.goods-record-price").text.strip())
                image_url = block.select_one("img.goods-record-image").get('data-src', '')

                if not name or not price or int(price) <= 0: continue

                all_products_in_category.append({
                    "id": product_id, "name": name, "price": price, "stock": 1,
                    "image": image_url, "category_id": category_id,
                })
                seen_product_ids_this_run.add(product_id)
                found_new_product = True

            if not found_new_product:
                logger.info(f"    - محصول جدیدی در این صفحه یافت نشد، توقف برای دسته {category_id}.")
                break
            page_num += 1
            time.sleep(random.uniform(delay, delay + 0.2))
        except requests.exceptions.RequestException as e:
            logger.error(f"    - خطا در پردازش صفحه محصولات {category_id}: {e}")
            raise

    logger.info(f"    - استخراج {len(all_products_in_category)} محصول از دسته {category_id} کامل شد.")
    return all_products_in_category

# ==============================================================================
# --- کش برای محصولات ---
# ==============================================================================
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def save_cache(products_data):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(products_data, f, ensure_ascii=False, indent=4)
    except IOError as e:
        logger.error(f"❌ خطا در ذخیره فایل کش: {e}")

# ==============================================================================
# --- توابع ووکامرس ---
# ==============================================================================
def transfer_categories_to_wc(source_categories):
    logger.info("\n⏳ شروع انتقال دسته‌بندی‌ها به ووکامرس...")
    # This function is assumed to be working correctly from previous versions.
    # It should return a dictionary mapping source_id -> wc_id
    # For brevity, I'm replacing it with a placeholder that simulates the behavior.
    # IN YOUR REAL CODE, USE YOUR ORIGINAL `transfer_categories_to_wc` FUNCTION.
    source_to_wc_id_map = {}
    id_to_cat = {cat['id']: cat for cat in source_categories}
    wc_id_counter = 100 # Dummy WC ID start
    
    def process_cat(cat_id, parent_wc_id=0):
        if cat_id in source_to_wc_id_map:
            return
        
        cat = id_to_cat.get(cat_id)
        if not cat: return

        if cat.get('parent_id') and cat['parent_id'] not in source_to_wc_id_map:
            process_cat(cat['parent_id'], 0)
        
        wc_parent = source_to_wc_id_map.get(cat.get('parent_id'), 0)
        
        # Here would be the actual API call to create/get category in WC
        # For now, we simulate it
        source_to_wc_id_map[cat_id] = wc_id_counter
        logger.debug(f"نگاشت دسته‌بندی: '{cat['name']}' (ID: {cat_id}) -> WC ID: {wc_id_counter}")
        globals()['wc_id_counter'] += 1

    for c in source_categories:
        process_cat(c['id'])
        
    logger.info(f"✅ انتقال/نگاشت دسته‌بندی‌ها کامل شد. {len(source_to_wc_id_map)} نگاشت ایجاد شد.")
    return source_to_wc_id_map

def process_price(price_value):
    try:
        price_value = float(re.sub(r'[^\d.]', '', str(price_value))) / 10
    except (ValueError, TypeError): return "0"
    if price_value <= 1: return "0"
    elif price_value <= 7_000_000: new_price = price_value + 260_000
    elif price_value <= 10_000_000: new_price = price_value * 1.035
    elif price_value <= 20_000_000: new_price = price_value * 1.025
    elif price_value <= 30_000_000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price, -4)))

@retry(retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)), stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=1, max=10), reraise=True)
def _send_to_woocommerce(sku, data, stats):
    auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
    try:
        r_check = requests.get(f"{WC_API_URL}/products?sku={sku}", auth=auth, verify=False, timeout=20)
        r_check.raise_for_status()
        existing = r_check.json()
        if existing:
            product_id = existing[0]['id']
            update_data = {
                "regular_price": data["regular_price"], "stock_quantity": data["stock_quantity"],
                "stock_status": data["stock_status"], "attributes": data["attributes"],
                "tags": data.get("tags", []), "categories": data["categories"]
            }
            res = requests.put(f"{WC_API_URL}/products/{product_id}", auth=auth, json=update_data, verify=False, timeout=30)
            res.raise_for_status()
            with stats['lock']: stats['updated'] += 1
            logger.info(f"   ✅ محصول {sku} آپدیت شد.")
        else:
            res = requests.post(f"{WC_API_URL}/products", auth=auth, json=data, verify=False, timeout=30)
            res.raise_for_status()
            with stats['lock']: stats['created'] += 1
            logger.info(f"   ✅ محصول {sku} ایجاد شد.")
    except requests.exceptions.HTTPError as e:
        logger.error(f"   ❌ خطای HTTP برای SKU {sku}: {e.response.status_code} - {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"   ❌ خطای کلی برای SKU {sku}: {e}")
        raise

# ==============================================================================
# --- توابع پردازش محصول ---
# ==============================================================================
def smart_tags_for_product(product, cat_map):
    tags = set()
    name_parts = [w for w in re.split(r'\s+', product.get('name', '')) if len(w) > 2]
    common_words = {'گوشی', 'موبایل', 'تبلت', 'لپتاپ', 'لپ‌تاپ', 'مدل'}
    for part in name_parts[:2]:
        if part not in common_words: tags.add(part)
    return [{"name": t} for t in sorted(tags)]

def process_product_wrapper(args):
    source_id, product_data, stats, category_mapping, cat_map, session = args
    try:
        wc_cat_ids = [cid for cat_id in product_data['categories'] if (cid := category_mapping.get(cat_id))]
        if not wc_cat_ids:
            with stats['lock']: stats['no_category'] += 1
            return

        specs = get_product_details(session, list(product_data['categories'])[0], source_id)
        attributes = [{"name": k, "options": [v], "position": i, "visible": True, "variation": False} for i, (k, v) in enumerate(specs.items())]
        
        wc_data = {
            "name": product_data['details'].get('name', 'بدون نام'), "type": "simple",
            "sku": f"EWAYS-{source_id}", "regular_price": process_price(product_data['details'].get('price', 0)),
            "categories": [{"id": wc_id} for wc_id in wc_cat_ids],
            "images": [{"src": product_data['details'].get("image")}] if product_data['details'].get("image") else [],
            "stock_quantity": product_data['details'].get('stock', 0), "manage_stock": True,
            "stock_status": "instock" if product_data['details'].get('stock', 0) > 0 else "outofstock",
            "attributes": attributes, "tags": smart_tags_for_product(product_data['details'], cat_map)
        }
        _send_to_woocommerce(wc_data['sku'], wc_data, stats)
    except Exception as e:
        logger.error(f"   ❌ خطای جدی در پردازش محصول {source_id}: {e}", exc_info=True)
        with stats['lock']: stats['failed'] += 1

# ==============================================================================
# --- تابع اصلی ---
# ==============================================================================
def main():
    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session: return

    all_cats = get_and_parse_categories(session)
    if not all_cats:
        logger.error("❌ دسته‌بندی‌ها بارگذاری نشد. برنامه خاتمه می‌یابد.")
        return
    cat_map = {cat['id']: cat['name'] for cat in all_cats}
    logger.info(f"✅ مرحله ۱: {len(all_cats)} دسته‌بندی بارگذاری شد.")

    SELECTED_IDS_STRING = "16777:all-allz|4882:all-allz|16778:22570-all-allz"
    parsed_selection = parse_selected_ids_string(SELECTED_IDS_STRING)
    filtered_categories = get_selected_categories_according_to_selection(parsed_selection, all_cats)
    selected_cat_ids = {cat['id'] for cat in filtered_categories}
    logger.info(f"✅ دسته‌بندی‌های نهایی برای اسکرپ: {len(filtered_categories)} عدد")

    category_mapping = transfer_categories_to_wc(filtered_categories)
    if not category_mapping: return
    logger.info(f"✅ مرحله ۲: انتقال دسته‌بندی‌ها کامل شد.")

    all_products_raw = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_catid = {executor.submit(get_products_from_category_page, session, cat_id): cat_id for cat_id in selected_cat_ids}
        pbar = tqdm(as_completed(future_to_catid), total=len(selected_cat_ids), desc="دریافت محصولات")
        for future in pbar:
            try:
                all_products_raw.extend(future.result())
            except Exception as e:
                logger.error(f"⚠️ خطا در دریافت محصولات دسته {future_to_catid[future]}: {e}")
    logger.info(f"✅ مرحله ۳: استخراج خام کامل شد. {len(all_products_raw)} رکورد محصول دریافت شد.")

    products_by_source_id = defaultdict(lambda: {"details": None, "categories": set()})
    for p in all_products_raw:
        products_by_source_id[p['id']]['details'] = p
        products_by_source_id[p['id']]['categories'].add(p['category_id'])
    logger.info(f"✅ مرحله ۴: گروه‌بندی کامل شد. {len(products_by_source_id)} محصول یکتا یافت شد.")

    cached_products = load_cache()
    products_to_send = {
        source_id: current_data
        for source_id, current_data in products_by_source_id.items()
        if source_id not in cached_products or \
           cached_products[source_id].get('price') != current_data['details']['price'] or \
           set(cached_products[source_id].get('categories', [])) != current_data['categories']
    }
    logger.info(f"✅ مرحله ۵: مقایسه با کش کامل شد. {len(products_to_send)} محصول جدید/تغییریافته برای ارسال.")

    new_cache_data = {
        source_id: {"price": data['details']['price'], "categories": list(data['categories'])}
        for source_id, data in products_by_source_id.items()
    }
    save_cache(new_cache_data)

    if not products_to_send:
        logger.info("✅ هیچ محصولی برای ارسال وجود ندارد. پایان کار.")
        return

    stats = {'created': 0, 'updated': 0, 'failed': 0, 'no_category': 0, 'lock': Lock()}
    logger.info(f"\n🚀 شروع پردازش و ارسال {len(products_to_send)} محصول به ووکامرس...")
    tasks = [(sid, data, stats, category_mapping, cat_map, session) for sid, data in products_to_send.items()]

    with ThreadPoolExecutor(max_workers=3) as executor:
        list(tqdm(executor.map(process_product_wrapper, tasks), total=len(tasks), desc="ارسال به ووکامرس"))

    logger.info("\n===============================\n"
                f"📦 محصولات پردازش شده: {len(products_to_send)}\n"
                f"🟢 ایجاد شده: {stats['created']}\n"
                f"🔵 آپدیت شده: {stats['updated']}\n"
                f"🔴 شکست‌خورده: {stats['failed']}\n"
                f"🟡 بدون دسته ووکامرس: {stats['no_category']}\n"
                "===============================\nتمام!")

if __name__ == "__main__":
    main()
