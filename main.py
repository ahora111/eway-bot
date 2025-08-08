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
import logging
from logging.handlers import RotatingFileHandler

# ==============================================================================
# --- تنظیمات ---
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=1024*1024, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# ==============================================================================
# --- لاگین eways ---
# ==============================================================================
def login_eways(username, password):
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0',
        'Referer': f"{BASE_URL}/",
        'X-Requested-With': 'XMLHttpRequest',
        'Accept-Language': 'en-US,en;q=0.9,fa;q=0.8'
    })
    session.verify = False
    login_url = f"{BASE_URL}/User/Login"
    payload = {"UserName": username, "Password": password, "RememberMe": "true"}
    logger.info("⏳ در حال لاگین به پنل eways ...")
    resp = session.post(login_url, data=payload, timeout=30)
    if resp.status_code != 200:
        logger.error(f"❌ لاگین ناموفق! کد وضعیت: {resp.status_code}")
        return None
    if 'Aut' in session.cookies:
        logger.info("✅ لاگین موفق! کوکی Aut دریافت شد.")
        return session
    logger.error("❌ کوکی Aut دریافت نشد.")
    return None

# ==============================================================================
# --- دریافت دسته‌بندی‌ها ---
# ==============================================================================
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
        logger.info(f"✅ تعداد {len(final_cats)} دسته‌بندی استخراج شد.")
        return final_cats
    except Exception as e:
        logger.error(f"❌ خطا در دریافت دسته‌بندی‌ها: {e}")
        return None

# ==============================================================================
# --- انتخاب دسته‌بندی‌ها ---
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
# --- انتقال دسته‌بندی‌ها به ووکامرس ---
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
    wc_cats_map = {}
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
    for cat in tqdm(sorted_cats, desc="انتقال دسته‌ها"):
        name = cat["name"].strip()
        parent_id = cat.get("parent_id") or 0
        wc_parent = source_to_wc_id_map.get(parent_id, 0)
        existing_id = check_existing_category(name, wc_parent)
        if existing_id:
            source_to_wc_id_map[cat["id"]] = existing_id
            continue
        data = {"name": name, "parent": wc_parent}
        try:
            res = requests.post(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
            if res.status_code in [200, 201]:
                new_id = res.json()["id"]
                source_to_wc_id_map[cat["id"]] = new_id
            else:
                error_data = res.json()
                if error_data.get("code") == "term_exists" and "data" in error_data and "resource_id" in error_data["data"]:
                    existing_id = error_data["data"]["resource_id"]
                    source_to_wc_id_map[cat["id"]] = existing_id
                else:
                    logger.error(f"❌ خطا در ساخت دسته‌بندی '{name}' (parent: {wc_parent}): {res.text}")
        except Exception as e:
            logger.error(f"❌ خطای شبکه در ساخت دسته‌بندی '{name}': {e}")
    logger.info(f"✅ انتقال دسته‌بندی‌ها کامل شد.")
    return source_to_wc_id_map

# ==============================================================================
# --- کش محصولات ووکامرس ---
# ==============================================================================
def get_wc_products_cache(prefix="EWAYS-"):
    products = {}
    page = 1
    while True:
        res = requests.get(f"{WC_API_URL}/products", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET),
                           params={"per_page": 100, "page": page}, verify=False)
        res.raise_for_status()
        data = res.json()
        if not data:
            break
        for p in data:
            sku = p.get('sku', '')
            if sku and sku.startswith(prefix):
                products[sku] = p
        if len(data) < 100:
            break
        page += 1
    logger.info(f"✅ کش محصولات ووکامرس بارگذاری شد: {len(products)} محصول")
    return products

# ==============================================================================
# --- دریافت مشخصات فنی موازی ---
# ==============================================================================
def get_product_details(session, cat_id, product_id):
    url = PRODUCT_DETAIL_URL_TEMPLATE.format(cat_id=cat_id, product_id=product_id)
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'lxml')
        specs_table = soup.select_one('#link1 .table-responsive table') or \
                      soup.select_one('.table-responsive table') or \
                      soup.find('table', class_='table')
        specs = {}
        if specs_table:
            rows = specs_table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) == 2:
                    key = cells[0].text.strip()
                    value = cells[1].text.strip()
                    if key and value:
                        specs[key] = value
        return specs
    except Exception as e:
        logger.warning(f"خطا در دریافت مشخصات محصول {product_id}: {e}")
        return {}

def fetch_specs_parallel(session, products, cat_id, max_workers=8, delay=0.1):
    def fetch_spec(product):
        try:
            product['specs'] = get_product_details(session, cat_id, product['id'])
            time.sleep(random.uniform(delay, delay + 0.1))
        except Exception as e:
            product['specs'] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_spec, p) for p in products]
        for future in as_completed(futures):
            pass
    return products

# ==============================================================================
# --- دریافت محصولات هر دسته (با موازی‌سازی مشخصات فنی) ---
# ==============================================================================
def get_products_from_category_page(session, category_id, max_pages=10, delay=0.5):
    all_products_in_category = []
    seen_product_ids = set()
    page = 1
    while page <= max_pages:
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page)
        logger.info(f"⏳ دریافت محصولات از: {url}")
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code != 200:
                logger.error(f"❌ خطا در دریافت HTML صفحه {page} - status: {resp.status_code}")
                break
            soup = BeautifulSoup(resp.text, 'lxml')
            product_blocks = soup.select(".goods-record")
            html_products = []
            for block in product_blocks:
                a_tag = block.select_one("a")
                name_tag = block.select_one("span.goods-record-title")
                unavailable = block.select_one(".goods-record-unavailable")
                is_available = unavailable is None
                if a_tag and name_tag:
                    product_id = None
                    href = a_tag['href']
                    match = re.search(r'/Store/Detail/\d+/(\d+)', href)
                    if match:
                        product_id = match.group(1)
                    name = name_tag.text.strip()
                    price_tag = block.select_one("span.goods-record-price")
                    price_text = price_tag.text.strip() if price_tag else ""
                    price = re.sub(r'[^\d]', '', price_text) if price_text else "0"
                    image_tag = block.select_one("img.goods-record-image")
                    image_url = image_tag.get('data-src', '') if image_tag else ''
                    if is_available and product_id and product_id not in seen_product_ids:
                        html_products.append({
                            'id': product_id,
                            'name': name,
                            'category_id': category_id,
                            'price': price,
                            'stock': 1,
                            'image': image_url,
                        })
                        seen_product_ids.add(product_id)
            # موازی‌سازی دریافت مشخصات فنی
            html_products = fetch_specs_parallel(session, html_products, category_id)
            if not html_products:
                logger.info(f"⛔️ هیچ محصول موجودی در صفحه {page} نبود. بررسی صفحات متوقف شد.")
                break
            all_products_in_category.extend(html_products)
            page += 1
        except Exception as e:
            logger.error(f"خطا در پردازش صفحه محصولات: {e}")
            break
    logger.info(f"تعداد کل محصولات استخراج‌شده از دسته {category_id}: {len(all_products_in_category)}")
    return all_products_in_category

# ==============================================================================
# --- کش محصولات ---
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
# --- ارسال گروهی (batch) محصولات به ووکامرس ---
# ==============================================================================
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

def smart_tags_for_product(product, cat_map):
    tags = set()
    name = product.get('name', '')
    specs = product.get('specs', {})
    cat_id = product.get('category_id')
    cat_name = cat_map.get(cat_id, '').strip()
    price = int(product.get('price', 0))
    name_parts = [w for w in re.split(r'\s+', name) if w and len(w) > 2]
    common_words = {'گوشی', 'موبایل', 'تبلت', 'لپتاپ', 'لپ‌تاپ', 'مدل', 'محصول', 'کالا', 'جدید'}
    for part in name_parts[:2]:
        if part not in common_words:
            tags.add(part)
    if cat_name and cat_name not in common_words:
        tags.add(cat_name)
    important_keys = ['رنگ', 'Color', 'حافظه', 'ظرفیت', 'اندازه', 'سایز', 'Size', 'مدل', 'برند']
    for key, value in specs.items():
        if any(imp in key for imp in important_keys):
            val = value.strip()
            if 2 < len(val) < 30 and val not in common_words:
                tags.add(val)
    if price > 0:
        if price < 5000000:
            tags.add('اقتصادی')
        elif price > 20000000:
            tags.add('لوکس')
    tags.add('خرید آنلاین')
    tags.add('گارانتی دار')
    tags = {t for t in tags if t and len(t) <= 30 and t.lower() not in ['test', 'spam', 'محصول', 'کالا']}
    return [{"name": t} for t in sorted(tags)]

def send_products_batch_to_woocommerce(products, wc_cache, category_mapping, cat_map, stats, batch_size=10):
    url = f"{WC_API_URL}/products/batch"
    auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
    create_data = []
    update_data = []
    for p in products:
        wc_cat_id = category_mapping.get(p.get('category_id'))
        if not wc_cat_id:
            continue
        attributes = []
        for idx, (key, value) in enumerate(p.get('specs', {}).items()):
            attributes.append({
                "name": key,
                "options": [value],
                "position": idx,
                "visible": True,
                "variation": False
            })
        tags = smart_tags_for_product(p, cat_map)
        wc_data = {
            "name": p.get('name', 'بدون نام'),
            "type": "simple",
            "sku": f"EWAYS-{p.get('id')}",
            "regular_price": process_price(p.get('price', 0)),
            "categories": [{"id": wc_cat_id}],
            "images": [{"src": p.get("image")}] if p.get("image") else [],
            "stock_quantity": p.get('stock', 0),
            "manage_stock": True,
            "stock_status": "instock" if p.get('stock', 0) > 0 else "outofstock",
            "attributes": attributes,
            "tags": tags
        }
        sku = wc_data['sku']
        if sku in wc_cache:
            wc_data['id'] = wc_cache[sku]['id']
            update_data.append(wc_data)
        else:
            create_data.append(wc_data)
    # ارسال گروهی
    for i in range(0, max(len(create_data), len(update_data)), batch_size):
        data = {
            "create": create_data[i:i+batch_size],
            "update": update_data[i:i+batch_size]
        }
        try:
            res = requests.post(url, auth=auth, json=data, verify=False, timeout=60)
            res.raise_for_status()
            result = res.json()
            with stats['lock']:
                stats['created'] += len(result.get('create', []))
                stats['updated'] += len(result.get('update', []))
            logger.info(f"Batch ارسال شد: ایجاد {len(result.get('create', []))}، آپدیت {len(result.get('update', []))}")
        except Exception as e:
            logger.error(f"❌ خطا در ارسال batch: {e}")
            with stats['lock']:
                stats['failed'] += batch_size

# ==============================================================================
# --- آپدیت محصولات ناموجود ---
# ==============================================================================
def update_to_outofstock_batch(product_ids, stats):
    url = f"{WC_API_URL}/products/batch"
    auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
    update_data = []
    for pid in product_ids:
        update_data.append({
            "id": pid,
            "stock_quantity": 0,
            "stock_status": "outofstock",
            "manage_stock": True
        })
    try:
        res = requests.post(url, auth=auth, json={"update": update_data}, verify=False, timeout=60)
        res.raise_for_status()
        with stats['lock']:
            stats['outofstock_updated'] += len(update_data)
        logger.info(f"Batch آپدیت به ناموجود: {len(update_data)} محصول")
    except Exception as e:
        logger.error(f"❌ خطا در batch آپدیت به ناموجود: {e}")
        with stats['lock']:
            stats['failed'] += len(update_data)

# ==============================================================================
# --- تابع اصلی ---
# ==============================================================================
def main():
    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session:
        logger.error("❌ لاگین به پنل eways انجام نشد.")
        return

    all_cats = get_and_parse_categories(session)
    if not all_cats:
        logger.error("❌ دسته‌بندی‌ها بارگذاری نشد.")
        return

    SELECTED_IDS_STRING = "16777:all-allz"
    parsed_selection = parse_selected_ids_string(SELECTED_IDS_STRING)
    filtered_categories = get_selected_categories_according_to_selection(parsed_selection, all_cats)
    category_mapping = transfer_categories_to_wc(filtered_categories)

    cached_products = load_cache()
    wc_cache = get_wc_products_cache()

    selected_ids = [cat['id'] for cat in filtered_categories]
    all_products = {}
    logger.info(f"\n⏳ شروع جمع‌آوری محصولات ...")

    # استخراج محصولات هر دسته (موازی)
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_catid = {executor.submit(get_products_from_category_page, session, cat_id, 5, 0.3): cat_id for cat_id in selected_ids}
        for future in as_completed(future_to_catid):
            cat_id = future_to_catid[future]
            try:
                products_in_cat = future.result()
                for product in products_in_cat:
                    key = f"{product['id']}|{cat_id}"
                    all_products[key] = product
            except Exception as e:
                logger.warning(f"⚠️ خطا در دریافت محصولات دسته {cat_id}: {e}")

    logger.info(f"✅ استخراج محصولات کامل شد. تعداد: {len(all_products)}")

    # ادغام با کش
    updated_products = {}
    changed_products = []
    cat_map = {cat['id']: cat['name'] for cat in filtered_categories}
    for key, p in all_products.items():
        if key in cached_products and cached_products[key]['price'] == p['price'] and cached_products[key]['stock'] == p['stock'] and cached_products[key]['specs'] == p['specs']:
            updated_products[key] = cached_products[key]
        else:
            updated_products[key] = p
            changed_products.append(p)
    save_cache(updated_products)

    # مدیریت محصولات ناموجود
    extracted_skus = {f"EWAYS-{p['id']}" for p in all_products.values()}
    outofstock_ids = []
    for sku, wc_p in wc_cache.items():
        if sku not in extracted_skus and wc_p['stock_status'] != "outofstock":
            outofstock_ids.append(wc_p['id'])

    stats = {'created': 0, 'updated': 0, 'failed': 0, 'outofstock_updated': 0, 'lock': Lock()}

    # ارسال محصولات جدید/تغییر یافته به ووکامرس (batch)
    logger.info(f"\n🚀 شروع ارسال محصولات جدید/تغییر یافته به ووکامرس (batch)...")
    for i in range(0, len(changed_products), 10):
        send_products_batch_to_woocommerce(
            changed_products[i:i+10], wc_cache, category_mapping, cat_map, stats, batch_size=10
        )
        time.sleep(1)  # تاخیر برای سلامت سرور

    # آپدیت محصولات ناموجود (batch)
    logger.info(f"\n🚧 شروع آپدیت محصولات ناموجود در ووکامرس (batch)...")
    for i in range(0, len(outofstock_ids), 10):
        update_to_outofstock_batch(outofstock_ids[i:i+10], stats)
        time.sleep(1)

    logger.info("\n===============================")
    logger.info(f"🟢 ایجاد شده: {stats['created']}")
    logger.info(f"🔵 آپدیت شده: {stats['updated']}")
    logger.info(f"🟠 آپدیت به ناموجود: {stats['outofstock_updated']}")
    logger.info(f"🔴 شکست‌خورده: {stats['failed']}")
    logger.info("===============================\nتمام!")

if __name__ == "__main__":
    main()
