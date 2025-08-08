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
# --- تنظیمات لاگینگ ---
# ==============================================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=1024*1024, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# ==============================================================================
# --- اطلاعات ووکامرس و سایت مبدا ---
# ==============================================================================
BASE_URL = "https://panel.eways.co"
PRODUCT_DETAIL_URL_TEMPLATE = f"{BASE_URL}/Store/Detail/{{cat_id}}/{{product_id}}"

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
        logger.error("❌ کوکی Aut دریافت نشد. لاگین ناموفق یا دلیل نامشخص.")
        return None

# ==============================================================================
# --- گرفتن ویژگی‌های محصول (specs) ---
# ==============================================================================
@retry(
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    stop=stop_after_attempt(3),
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
        return specs
    except Exception as e:
        logger.warning(f"      - خطا در استخراج مشخصات محصول {product_id}: {e}")
        return {}

# ==============================================================================
# --- گرفتن محصولات هر دسته (فقط موجودها و توقف هوشمند) ---
# ==============================================================================
@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)),
    stop=stop_after_attempt(4),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True
)
def get_products_from_category_page(session, category_id, max_pages=10, delay=0.5):
    all_products_in_category = []
    seen_product_ids = set()
    page = 1
    error_count = 0
    while page <= max_pages:
        # --- محصولات اولیه HTML ---
        if page == 1:
            url = f"{BASE_URL}/Store/List/{category_id}/2/2/0/0/0/10000000000?text=%DA%AF%D9%88%D8%B4%DB%8C-%D9%85%D9%88%D8%A8%D8%A7%DB%8C%D9%84"
        else:
            url = f"{BASE_URL}/Store/List/{category_id}/2/2/{page-1}/0/0/10000000000?brands=&isMobile=false"
        logger.info(f"⏳ دریافت محصولات اولیه از HTML صفحه {page} ...")
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code != 200:
                logger.error(f"❌ خطا در دریافت HTML صفحه {page} - status: {resp.status_code} - url: {url}")
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
                        specs = get_product_details(session, category_id, product_id)
                        html_products.append({
                            'id': product_id,
                            'name': name,
                            'category_id': category_id,
                            'price': price,
                            'stock': 1,
                            'image': image_url,
                            'specs': specs,
                        })
                        seen_product_ids.add(product_id)
                        time.sleep(random.uniform(delay, delay + 0.2))
            logger.info(f"🟢 محصولات موجود اولیه (HTML) صفحه {page}: {len(html_products)}")

            # --- محصولات Lazy ---
            lazy_products = []
            lazy_page = 1
            referer_url = url
            while True:
                data = {
                    "ListViewType": 0,
                    "CatId": category_id,
                    "Order": 2,
                    "Sort": 2,
                    "LazyPageIndex": lazy_page,
                    "PageIndex": page - 1,
                    "PageSize": 24,
                    "Available": 0,
                    "MinPrice": 0,
                    "MaxPrice": 10000000000,
                    "IsLazyLoading": "true"
                }
                headers = {
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": referer_url
                }
                logger.info(f"⏳ در حال دریافت LazyPageIndex={lazy_page} صفحه {page} ...")
                resp = session.post(f"{BASE_URL}/Store/ListLazy", data=data, headers=headers, timeout=30)
                if resp.status_code != 200:
                    logger.error(f"❌ خطا در دریافت محصولات (کد: {resp.status_code})")
                    break
                try:
                    result = resp.json()
                except Exception as e:
                    logger.error(f"❌ خطا در تبدیل پاسخ به json: {e}")
                    logger.error(f"متن پاسخ سرور:\n{resp.text[:500]}")
                    break
                if not result or "Goods" not in result or not result["Goods"]:
                    logger.info(f"🚩 به انتهای محصولات Lazy صفحه {page} رسیدیم یا لیست خالی است.")
                    break
                goods = result["Goods"]
                for g in goods:
                    if g.get("Availability", True):
                        product_id = str(g["Id"])
                        if product_id not in seen_product_ids:
                            specs = get_product_details(session, category_id, product_id)
                            lazy_products.append({
                                "id": product_id,
                                "name": g["Name"],
                                "category_id": category_id,
                                "price": g.get("Price", "0"),
                                "stock": 1,
                                "image": g.get("ImageUrl", ""),
                                "specs": specs,
                            })
                            seen_product_ids.add(product_id)
                            time.sleep(random.uniform(delay, delay + 0.2))
                logger.info(f"🟢 محصولات موجود این صفحه Lazy: {len([g for g in goods if g.get('Availability', True)])}")
                lazy_page += 1
            # جمع محصولات موجود این صفحه
            available_in_page = html_products + lazy_products
            if not available_in_page:
                logger.info(f"⛔️ هیچ محصول موجودی در صفحه {page} نبود. بررسی صفحات متوقف شد.")
                break
            all_products_in_category.extend(available_in_page)
            page += 1
            error_count = 0
        except Exception as e:
            error_count += 1
            logger.error(f"    - خطا در پردازش صفحه محصولات: {e} (تعداد خطا: {error_count})")
            if error_count >= 3:
                logger.critical(f"🚨 تعداد خطاهای متوالی در دسته {category_id} به {error_count} رسید! توقف پردازش این دسته.")
                break
            time.sleep(2)
    logger.info(f"    - تعداد کل محصولات موجود استخراج‌شده از دسته {category_id}: {len(all_products_in_category)}")
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
# --- تابع اصلی ---
# ==============================================================================
def main():
    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session:
        logger.error("❌ لاگین به پنل eways انجام نشد. برنامه خاتمه می‌یابد.")
        return

    category_id = 4286
    products = get_products_from_category_page(session, category_id, max_pages=10)
    logger.info(f"\n✅ تعداد کل محصولات موجود این دسته: {len(products)}\n")
    for i, p in enumerate(products, 1):
        print(f"{i:03d}. {p['name']} (ID: {p['id']}) | قیمت: {p['price']} | عکس: {p['image']} | ویژگی‌ها: {json.dumps(p['specs'], ensure_ascii=False)}")

    # کش قبلی را بارگذاری کن
    cached_products = load_cache()
    updated_products = {}
    changed_count = 0

    for p in products:
        key = f"{p['id']}|{p['category_id']}"
        if key in cached_products and cached_products[key]['price'] == p['price'] and cached_products[key]['stock'] == p['stock']:
            updated_products[key] = cached_products[key]
        else:
            updated_products[key] = p
            changed_count += 1
            # اینجا می‌توانی ارسال به ووکامرس یا هر جای دیگر را انجام دهی

    save_cache(updated_products)

if __name__ == "__main__":
    main()
