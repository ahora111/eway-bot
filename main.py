import requests
import urllib3
import os
import re
import time
import json
import sys
import random
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from threading import Lock
import logging
from logging.handlers import RotatingFileHandler
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

# ==============================================================================
# --- تنظیمات لاگینگ ---
# ==============================================================================
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')  # سطح DEBUG برای جزئیات بیشتر
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=1024*1024, backupCount=5)  # 1MB per file, 5 backups
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# ==============================================================================
# --- تنظیمات و متغیرهای سراسری ---
# ==============================================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- اطلاعات ووکامرس ---
WC_API_URL = os.environ.get("WC_API_URL")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

# --- اطلاعات سایت Eways ---
BASE_URL = "https://panel.eways.co"
AUT_COOKIE_VALUE = os.environ.get("EWAYS_AUTH_TOKEN")
SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"
PRODUCT_LIST_URL_TEMPLATE = f"{BASE_URL}/Store/List/{{category_id}}/2/2/0/0/0/10000000000?page={{page}}"
PRODUCT_DETAIL_URL_TEMPLATE = f"{BASE_URL}/Store/Detail/{{cat_id}}/{{product_id}}"

# ==============================================================================
# --- توابع مربوط به سایت مبدا (Eways.co) ---
# ==============================================================================

def get_session():
    """یک Session با کوکی احراز هویت و retry mechanism ایجاد می‌کند."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f"{BASE_URL}/",
        'X-Requested-With': 'XMLHttpRequest'
    })
    if AUT_COOKIE_VALUE:
        session.cookies.set('Aut', AUT_COOKIE_VALUE, domain='panel.eways.co')
    session.verify = False
    
    # اضافه کردن retry برای درخواست‌ها
    retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session

def get_and_parse_categories(session):
    """دسته‌بندی‌ها را از API دریافت و به صورت یک لیست مسطح برمی‌گرداند."""
    logger.info(f"⏳ دریافت دسته‌بندی‌ها از: {SOURCE_CATS_API_URL}")
    try:
        response = session.get(SOURCE_CATS_API_URL, timeout=30)
        response.raise_for_status()
        logger.info("✅ پاسخ با موفقیت دریافت شد.")
        
        # اول سعی در پارس JSON (اگر API JSON برگرداند)
        try:
            data = response.json()
            logger.info("✅ پاسخ JSON است. در حال پردازش...")
            final_cats = []
            for c in data:  # فرض ساختار: [{'id': int, 'name': str, 'parent_id': int or None, 'url': str}]
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
        
        # اگر JSON نبود، پارس HTML
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
    """اجازه می‌دهد کاربر دسته‌بندی‌های مورد نظر را انتخاب کند. در محیط غیرتعاملی، پیش‌فرض را انتخاب می‌کند."""
    if not source_categories:
        logger.warning("⚠️ هیچ دسته‌بندی برای انتخاب موجود نیست.")
        return []
    
    logger.info("📋 لیست دسته‌بندی‌ها:")
    for i, cat in enumerate(source_categories):
        logger.info(f"{i+1}: {cat['name']} (ID: {cat['id']})")
    
    try:
        selected_input = input("شماره‌های مورد نظر را با کاما وارد کنید (مثل 1,3) یا 'all' برای همه: ").strip().lower()
    except EOFError:
        logger.warning("⚠️ ورودی کاربر در دسترس نیست (EOF). استفاده از دسته‌بندی‌های پیش‌فرض (IDهای 1582 و 2541).")
        default_ids = [1582, 2541]  # پیش‌فرض: جانبی موبایل و جانبی رایانه
        selected = [c for c in source_categories if c['id'] in default_ids]
        logger.info(f"✅ دسته‌بندی‌های پیش‌فرض انتخاب‌شده: {[c['name'] for c in selected]}")
        return selected
    
    if selected_input == 'all':
        return source_categories
    
    try:
        indices = [int(x.strip()) - 1 for x in selected_input.split(',')]
        selected = [source_categories[i] for i in indices if 0 <= i < len(source_categories)]
        logger.info(f"✅ دسته‌بندی‌های انتخاب‌شده: {[c['name'] for c in selected]}")
        return selected
    except ValueError:
        logger.error("❌ ورودی نامعتبر. هیچ دسته‌ای انتخاب نشد.")
        return []

def get_product_detail(session, cat_id, product_id):
    """جزئیات محصول را از صفحه جزئیات استخراج می‌کند."""
    url = PRODUCT_DETAIL_URL_TEMPLATE.format(cat_id=cat_id, product_id=product_id)
    logger.debug(f"      - دریافت جزئیات محصول از: {url}")
    try:
        response = session.get(url, timeout=30)
        if response.status_code != 200:
            logger.warning(f"      - خطا در دریافت جزئیات: status {response.status_code}")
            return None
        soup = BeautifulSoup(response.text, 'lxml')
        
        name = soup.select_one("h1.product-title").text.strip() if soup.select_one("h1.product-title") else None
        price_tag = soup.select_one(".product-price .price")
        price = re.sub(r'[^\d]', '', price_tag.text.strip()) if price_tag else "0"
        stock_tag = soup.select_one(".product-stock span")
        stock = int(re.sub(r'[^\d]', '', stock_tag.text.strip())) if stock_tag else 0
        image_url = soup.select_one(".product-image img")['src'] if soup.select_one(".product-image img") else ""
        if image_url and not image_url.startswith('http'):
            image_url = "https://staticcontent.eways.co" + image_url
        
        if name and int(price) > 0 and stock > 0:
            return {"id": product_id, "name": name, "price": price, "stock": stock, "image": image_url, "category_id": cat_id}
        else:
            logger.debug(f"      - محصول {product_id} موجود نیست یا قیمت/نام نامعتبر (قیمت: {price}, موجودی: {stock}).")
            return None
    except Exception as e:
        logger.warning(f"      - خطا در پارس جزئیات محصول {product_id}: {e}")
        return None

def get_products_from_category_page(session, category_id, max_pages=50):
    """محصولات را از صفحات HTML یک دسته‌بندی استخراج می‌کند و صفحه‌بندی را هوشمندانه مدیریت می‌کند."""
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
            logger.debug(f"    - بخشی از HTML صفحه: {str(soup.prettify()[:1000])}...")  # لاگ بخشی از HTML برای دیباگ
            
            product_blocks = soup.select(".goods_item.goods-record")
            logger.info(f"    - تعداد بلاک‌های محصول پیدا شده: {len(product_blocks)}")
            if not product_blocks:
                logger.info("    - هیچ محصولی در این صفحه یافت نشد. پایان صفحه‌بندی.")
                break

            current_page_product_ids = []
            for block in product_blocks:
                logger.debug(f"      - بلاک کامل: {str(block)}")  # لاگ کامل بلاک برای دیباگ
                try:
                    classes = block.get('class', [])
                    is_available = 'noCount' not in classes and 'soldOut' not in classes  # شرط برای موجود بودن
                    logger.debug(f"      - کلاس‌های بلاک: {classes}, موجود؟ {is_available}")
                    if not is_available: continue

                    id_tag = block.select_one("a[data-productid]")
                    product_id = id_tag.get('data-productid') if id_tag else None
                    logger.debug(f"      - product_id: {product_id}")
                    if not product_id or product_id in seen_product_ids: continue

                    # دریافت جزئیات از صفحه جزئیات
                    product = get_product_detail(session, category_id, product_id)
                    if product:
                        seen_product_ids.add(product_id)
                        current_page_product_ids.append(product_id)
                        all_products_in_category.append(product)
                        logger.info(f"      - محصول {product_id} ({product['name']}) اضافه شد با قیمت {product['price']} و موجودی {product['stock']}.")
                    else:
                        logger.debug(f"      - محصول {product_id} اضافه نشد (ناموجود یا خطا).")
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
            
    logger.info(f"    - تعداد کل محصولات استخراج‌شده از دسته {category_id}: {len(all_products_in_category)}")
    return all_products_in_category

def get_all_products(session, categories):
    """تمام محصولات را از تمام دسته‌بندی‌های انتخاب شده جمع‌آوری می‌کند."""
    all_products = {}
    logger.info("\n⏳ شروع فرآیند جمع‌آوری تمام محصولات از همه دسته‌بندی‌های انتخابی...")
    for category in tqdm(categories, desc="پردازش دسته‌بندی‌ها"):
        products_in_cat = get_products_from_category_page(session, category['id'])
        for product in products_in_cat:
            all_products[product['id']] = product
    logger.info(f"\n✅ فرآیند جمع‌آوری کامل شد. تعداد کل محصولات یکتا و موجود: {len(all_products)}")
    return list(all_products.values())

# ==============================================================================
# --- توابع مربوط به ووکامرس و انتقال داده ---
# ==============================================================================
def sort_cats_for_creation(flat_cats):
    """دسته‌بندی‌ها را برای ایجاد مرتب می‌کند (اول والد، بعد فرزند)."""
    sorted_cats = []
    id_to_cat = {cat["id"]: cat for cat in flat_cats}
    visited = set()
    def visit(cat):
        if cat["id"] in visited: return
        parent_id = cat.get("parent_id")
        if parent_id and parent_id in id_to_cat:
            visit(id_to_cat[parent_id])
        sorted_cats.append(cat)
        visited.add(cat["id"])
    for cat in flat_cats:
        visit(cat)
    return sorted_cats

def get_wc_categories():
    """تمام دسته‌بندی‌های موجود در ووکامرس را دریافت می‌کند."""
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
    return wc_cats

def transfer_categories_to_wc(source_categories):
    """دسته‌بندی‌های انتخاب شده را به ووکامرس منتقل کرده و نقشه IDها را برمی‌گرداند."""
    logger.info("\n⏳ انتقال دسته‌بندی‌ها به ووکامرس...")
    wc_cats = get_wc_categories()
    wc_cats_map = {cat["name"].strip(): cat["id"] for cat in wc_cats}
    source_to_wc_id_map = {}
    
    sorted_source_cats = sort_cats_for_creation(source_categories)
    
    for cat in tqdm(sorted_source_cats, desc="انتقال دسته‌بندی‌ها"):
        name = cat["name"].strip()
        if name in wc_cats_map:
            wc_id = wc_cats_map[name]
            source_to_wc_id_map[cat["id"]] = wc_id
        else:
            data = {"name": name}
            parent_id = cat.get("parent_id")
            if parent_id and parent_id in source_to_wc_id_map:
                data["parent"] = source_to_wc_id_map[parent_id]
            
            try:
                res = requests.post(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
                if res.status_code in [200, 201]:
                    new_id = res.json()["id"]
                    source_to_wc_id_map[cat["id"]] = new_id
                    wc_cats_map[name] = new_id  # اضافه کردن به نقشه برای استفاده فرزندان بعدی
                else:
                    logger.error(f"❌ خطا در ساخت دسته‌بندی '{name}': {res.text}")
            except Exception as e:
                logger.error(f"❌ خطای شبکه در ساخت دسته‌بندی '{name}': {e}")
                
    logger.info("✅ انتقال دسته‌بندی‌ها کامل شد.")
    return source_to_wc_id_map

def process_price(price_value):
    """فرمول محاسبه قیمت فروش بر اساس قیمت خرید."""
    try:
        price_value = float(re.sub(r'[^\d.]', '', str(price_value))) * 1000  # اضافه کردن سه صفر
    except (ValueError, TypeError): return "0"
    
    if price_value <= 1: return "0"
    elif price_value <= 7000000: new_price = price_value + 260000
    elif price_value <= 10000000: new_price = price_value * 1.035
    elif price_value <= 20000000: new_price = price_value * 1.025
    elif price_value <= 30000000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price, -4)))  # رند کردن به نزدیک‌ترین ده هزار

def _send_to_woocommerce(sku, data, stats):
    """محصول را در ووکامرس ایجاد یا آپدیت می‌کند."""
    try:
        auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
        check_url = f"{WC_API_URL}/products?sku={sku}"
        
        r_check = requests.get(check_url, auth=auth, verify=False, timeout=20)
        r_check.raise_for_status()
        existing = r_check.json()
        
        if existing:
            product_id = existing[0]['id']
            # فقط قیمت و موجودی را آپدیت می‌کنیم تا سریع‌تر باشد
            update_data = {
                "regular_price": data["regular_price"],
                "stock_quantity": data["stock_quantity"],
                "stock_status": data["stock_status"]
            }
            res = requests.put(f"{WC_API_URL}/products/{product_id}", auth=auth, json=update_data, verify=False, timeout=20)
            if res.status_code == 200: 
                with stats['lock']: stats['updated'] += 1
            else: 
                logger.error(f"   ❌ خطا در آپدیت '{data['name']}'. Status: {res.status_code}")
        else:
            res = requests.post(f"{WC_API_URL}/products", auth=auth, json=data, verify=False, timeout=20)
            if res.status_code == 201: 
                with stats['lock']: stats['created'] += 1
            else: 
                logger.error(f"   ❌ خطا در ایجاد '{data['name']}'. Status: {res.status_code}")
    except Exception as e:
        logger.error(f"   ❌ خطای کلی در ارتباط با ووکامرس برای SKU {sku}: {e}")

def process_product_wrapper(args):
    """Wrapper برای استفاده در ThreadPoolExecutor."""
    product, stats, category_mapping = args
    try:
        wc_cat_id = category_mapping.get(product.get('category_id'))
        if not wc_cat_id: return
            
        # اختیاری: اگر نمی‌خوای محصولات با stock=0 منتقل بشن، این خط رو uncomment کن
        # if product.get('stock', 0) <= 0: return
        
        wc_data = {
            "name": product.get('name', 'بدون نام'), "type": "simple", "sku": f"EWAYS-{product.get('id')}",
            "regular_price": process_price(product.get('price', 0)),
            "categories": [{"id": wc_cat_id}],
            "images": [{"src": product.get("image")}] if product.get("image") else [],
            "stock_quantity": product.get('stock', 0), "manage_stock": True,
            "stock_status": "instock" if product.get('stock', 0) > 0 else "outofstock"
        }
        _send_to_woocommerce(wc_data['sku'], wc_data, stats)
    except Exception as e:
        logger.error(f"   ❌ خطای جدی در پردازش محصول {product.get('id', '')}: {e}")
        with stats['lock']: stats['failed'] += 1

# ==============================================================================
# --- تابع اصلی برنامه ---
# ==============================================================================
def main():
    if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET, AUT_COOKIE_VALUE]):
        logger.error("❌ یکی از متغیرهای محیطی ضروری (WC_* یا EWAYS_AUTH_TOKEN) تنظیم نشده است.")
        return

    session = get_session()
    
    # 1. دریافت و انتخاب دسته‌بندی‌ها
    source_categories = get_and_parse_categories(session)
    if not source_categories: return

    filtered_categories = get_selected_categories_flexible(source_categories)
    if not filtered_categories:
        logger.info("✅ هیچ دسته‌بندی انتخاب نشد. برنامه خاتمه می‌یابد.")
        return

    # 2. انتقال دسته‌بندی‌ها به ووکامرس و ساخت نقشه
    category_mapping = transfer_categories_to_wc(filtered_categories)
    if not category_mapping:
        logger.error("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد. برنامه خاتمه می‌یابد.")
        return

    # 3. دریافت تمام محصولات
    products = get_all_products(session, filtered_categories)
    if not products:
        logger.info("✅ هیچ محصولی برای پردازش یافت نشد. برنامه با موفقیت خاتمه می‌یابد.")
        return

    # 4. پردازش و ارسال محصولات به ووکامرس
    stats = {'created': 0, 'updated': 0, 'failed': 0, 'lock': Lock()}
    logger.info(f"\n🚀 شروع پردازش و ارسال {len(products)} محصول به ووکامرس...")
    with ThreadPoolExecutor(max_workers=10) as executor:  # افزایش برای سرعت بیشتر
        args_list = [(p, stats, category_mapping) for p in products]
        list(tqdm(executor.map(process_product_wrapper, args_list), total=len(products), desc="ارسال محصولات"))

    logger.info("\n===============================")
    logger.info(f"📦 محصولات پردازش شده: {len(products)}")
    logger.info(f"🟢 ایجاد شده: {stats['created']}")
    logger.info(f"🔵 آپدیت شده: {stats['updated']}")
    logger.info(f"🔴 شکست‌خورده: {stats['failed']}")
    logger.info("===============================\nتمام!")

if __name__ == "__main__":
    main()
