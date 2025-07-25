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

# ==============================================================================
# --- تابع لاگین اتوماتیک به eways ---
# ==============================================================================
def login_eways(username, password):
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f"{BASE_URL}/",
        'X-Requested-With': 'XMLHttpRequest'
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

def get_selected_categories_flexible(source_categories):
    if not source_categories:
        logger.warning("⚠️ هیچ دسته‌بندی برای انتخاب موجود نیست.")
        return []
    logger.info("📋 لیست دسته‌بندی‌ها:")
    for i, cat in enumerate(source_categories):
        logger.info(f"{i+1}: {cat['name']} (ID: {cat['id']})")
    try:
        selected_input = input("شماره‌های مورد نظر را با کاما وارد کنید (مثل 1,3) یا 'all' برای همه: ").strip().lower()
    except EOFError:
        logger.warning("⚠️ ورودی کاربر در دسترس نیست (EOF). استفاده از دسته‌بندی‌های پیش‌فرض (IDهای 1582 و 16777).")
        default_ids = [16777]
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

def get_all_category_ids(categories, all_cats, selected_ids):
    all_ids = set(selected_ids)
    to_process = list(selected_ids)
    while to_process:
        current_id = to_process.pop()
        for cat in all_cats:
            if cat['parent_id'] == current_id:
                all_ids.add(cat['id'])
                to_process.append(cat['id'])
    return list(all_ids)

def get_products_from_category_page(session, category_id, max_pages=10):
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
            logger.info(f"    - تعداد بلاک‌های محصول پیدا شده: {len(product_blocks)}")
            if not product_blocks:
                logger.info("    - هیچ محصولی در این صفحه یافت نشد. پایان صفحه‌بندی.")
                break
            current_page_product_ids = []
            for block in product_blocks:
                try:
                    # چک کردن اگر محصول ناموجود است
                    unavailable = block.select_one(".goods-record-unavailable")
                    if unavailable:
                        continue  # ناموجود، رد کردن

                    # استخراج لینک و ID محصول
                    a_tag = block.select_one("a")
                    href = a_tag['href'] if a_tag else None
                    product_id = None
                    if href:
                        match = re.search(r'/Store/Detail/\d+/(\d+)', href)
                        product_id = match.group(1) if match else None
                    if not product_id or product_id in seen_product_ids or product_id.startswith('##'):
                        continue

                    # استخراج نام
                    name_tag = block.select_one("span.goods-record-title")
                    name = name_tag.text.strip() if name_tag else None

                    # استخراج قیمت
                    price_tag = block.select_one("span.goods-record-price")
                    price = re.sub(r'[^\d]', '', price_tag.text.strip()) if price_tag else None

                    # استخراج تصویر
                    image_tag = block.select_one("img.goods-record-image")
                    image_url = image_tag.get('data-src', '') if image_tag else ''

                    # اگر نام یا قیمت نامعتبر، رد کردن
                    if not name or not price or int(price) <= 0:
                        logger.debug(f"      - محصول {product_id} نامعتبر (نام: {name}, قیمت: {price})")
                        continue

                    # موجودی: اگر قیمت وجود دارد، فرض موجود
                    stock = 1

                    product = {
                        "id": product_id,
                        "name": name,
                        "price": price,
                        "stock": stock,
                        "image": image_url,
                        "category_id": category_id
                    }

                    seen_product_ids.add(product_id)
                    current_page_product_ids.append(product_id)
                    all_products_in_category.append(product)
                    logger.info(f"      - محصول {product_id} ({product['name']}) اضافه شد با قیمت {product['price']}.")
                except Exception as e:
                    logger.warning(f"      - خطا در پردازش یک بلاک محصول: {e}. رد شدن...")
            if not current_page_product_ids:
                logger.info("    - محصول جدیدی در این صفحه یافت نشد، توقف صفحه‌بندی.")
                break
            page_num += 1
            time.sleep(random.uniform(2, 3))
        except requests.RequestException as e:
            logger.error(f"    - خطای شبکه در پردازش صفحه محصولات: {e}")
            break
        except Exception as e:
            logger.error(f"    - خطای کلی در پردازش صفحه محصولات: {e}")
            break
    logger.info(f"    - تعداد کل محصولات استخراج‌شده از دسته {category_id}: {len(all_products_in_category)}")
    return all_products_in_category

def get_all_products(session, categories, all_cats):
    all_products = {}
    selected_ids = [cat['id'] for cat in categories]
    all_relevant_ids = get_all_category_ids(categories, all_cats, selected_ids)
    logger.info(f"📂 IDهای دسته و زیرمجموعه‌های استخراج‌شده: {all_relevant_ids}")
    logger.info("\n⏳ شروع فرآیند جمع‌آوری تمام محصولات از همه دسته‌بندی‌های انتخابی و زیرمجموعه‌ها...")
    for cat_id in tqdm(all_relevant_ids, desc="پردازش دسته‌بندی‌ها و زیرمجموعه‌ها"):
        products_in_cat = get_products_from_category_page(session, cat_id)
        for product in products_in_cat:
            all_products[product['id']] = product
    logger.info(f"\n✅ فرآیند جمع‌آوری کامل شد. تعداد کل محصولات یکتا و موجود: {len(all_products)}")
    return list(all_products.values())

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
    return wc_cats

def transfer_categories_to_wc(source_categories):
    logger.info("\n⏳ انتقال دسته‌بندی‌ها به ووکامرس...")
    wc_cats = get_wc_categories()
    wc_cats_map = {cat["name"].strip(): cat["id"] for cat in wc_cats}
    source_to_wc_id_map = {}
    for cat in source_categories:
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
                    wc_cats_map[name] = new_id
                else:
                    logger.error(f"❌ خطا در ساخت دسته‌بندی '{name}': {res.text}")
            except Exception as e:
                logger.error(f"❌ خطای شبکه در ساخت دسته‌بندی '{name}': {e}")
    logger.info("✅ انتقال دسته‌بندی‌ها کامل شد.")
    return source_to_wc_id_map

def process_price(price_value):
    try:
        # استخراج عدد خام و تبدیل به float
        price_value = float(re.sub(r'[^\d.]', '', str(price_value)))
        # تبدیل ریال به تومان (فعال‌شده بر اساس درخواست شما)
        price_value /= 10  # 893000000 ریال -> 89300000 تومان
    except (ValueError, TypeError): return "0"
    if price_value <= 1: return "0"
    elif price_value <= 7000000: new_price = price_value + 260000
    elif price_value <= 10000000: new_price = price_value * 1.035
    elif price_value <= 20000000: new_price = price_value * 1.025
    elif price_value <= 30000000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price, -4)))

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
    product, stats, category_mapping = args
    try:
        wc_cat_id = category_mapping.get(product.get('category_id'))
        if not wc_cat_id: return
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
# --- تابع اصلی ---
# ==============================================================================
def main():
    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session:
        logger.error("❌ لاگین به پنل eways انجام نشد. برنامه خاتمه می‌یابد.")
        return

    all_cats = get_and_parse_categories(session)
    if not all_cats: return

    filtered_categories = get_selected_categories_flexible(all_cats)
    if not filtered_categories:
        logger.info("✅ هیچ دسته‌بندی انتخاب نشد. برنامه خاتمه می‌یابد.")
        return

    category_mapping = transfer_categories_to_wc(all_cats)  # تمام دسته‌ها را انتقال می‌دهیم تا زیرمجموعه‌ها هم پشتیبانی شوند
    if not category_mapping:
        logger.error("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد. برنامه خاتمه می‌یابد.")
        return

    products = get_all_products(session, filtered_categories, all_cats)
    if not products:
        logger.info("✅ هیچ محصولی برای پردازش یافت نشد. برنامه با موفقیت خاتمه می‌یابد.")
        return

    stats = {'created': 0, 'updated': 0, 'failed': 0, 'lock': Lock()}
    logger.info(f"\n🚀 شروع پردازش و ارسال {len(products)} محصول به ووکامرس...")
    with ThreadPoolExecutor(max_workers=10) as executor:
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
