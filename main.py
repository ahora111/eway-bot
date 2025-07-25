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

def build_category_tree(categories):
    """ساختار درختی دسته‌بندی‌ها را بر اساس parent_id می‌سازد."""
    tree = {}
    for cat in categories:
        tree[cat['id']] = cat.copy()
        tree[cat['id']]['children'] = []
    
    for cat in categories:
        parent_id = cat.get('parent_id')
        if parent_id and parent_id in tree:
            tree[parent_id]['children'].append(cat['id'])
    
    return tree

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
    """جزئیات محصول را از صفحه جزئیات استخراج می‌کند (فقط برای چک موجود بودن)."""
    if not product_id or product_id.startswith('##'):  # Skip placeholder IDs
        logger.debug(f"      - رد کردن product_id نامعتبر: {product_id}")
        return None
    
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
        
        if name and int(price) > 0 and stock > 0:
            return True  # فقط چک می‌کنیم که موجود باشه
        else:
            return False
    except Exception as e:
        logger.warning(f"      - خطا در پارس جزئیات محصول {product_id}: {e}")
        return False

def count_products_in_category(session, category_id, max_pages=5):
    """تعداد محصولات موجود در یک دسته را شمارش می‌کند (بدون ذخیره لیست)."""
    count = 0
    seen_product_ids = set()
    page_num = 1
    
    while page_num <= max_pages:
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page_num)
        logger.info(f"  - در حال شمارش محصولات از: {url}")
        
        try:
            response = session.get(url, timeout=30)
            if response.status_code != 200: break
                
            soup = BeautifulSoup(response.text, 'lxml')
            product_blocks = soup.select(".goods_item.goods-record")
            if not product_blocks: break

            for block in product_blocks:
                try:
                    classes = block.get('class', [])
                    if 'soldOut' in classes: continue

                    a_tag = block.select_one("a")
                    href = a_tag['href'] if a_tag else None
                    product_id = None
                    if href:
                        match = re.search(r'/Store/Detail/\d+/(\d+)', href)
                        product_id = match.group(1) if match else None
                    if not product_id or product_id in seen_product_ids or product_id.startswith('##'):
                        continue

                    # چک جزئیات برای موجود بودن
                    if get_product_detail(session, category_id, product_id):
                        seen_product_ids.add(product_id)
                        count += 1
                except Exception as e:
                    logger.warning(f"      - خطا در پردازش یک بلاک محصول: {e}")

            if len(product_blocks) == 0:
                break

            page_num += 1
            time.sleep(random.uniform(2, 3))
        except Exception as e:
            logger.error(f"    - خطا در شمارش صفحه محصولات: {e}")
            break
            
    return count

def count_products_recursive(session, cat_id, category_tree):
    """تعداد محصولات را به صورت بازگشتی برای دسته و تمام زیر دسته‌ها محاسبه می‌کند."""
    cat = category_tree.get(cat_id)
    if not cat:
        return 0
    
    # چاپ عنوان دسته در خروجی اصلی
    print(f"عنوان دسته: {cat['name']} (ID: {cat_id})")
    
    # شمارش محصولات مستقیم این دسته
    direct_count = count_products_in_category(session, cat_id)
    
    # شمارش بازگشتی زیر دسته‌ها
    total_count = direct_count
    for child_id in cat.get('children', []):
        total_count += count_products_recursive(session, child_id, category_tree)
    
    # فقط تعداد کل را در لاگ چاپ کن
    logger.info(f"تعداد محصولات در دسته '{cat['name']}' (ID: {cat_id}) و تمام زیر دسته‌ها: {total_count}")
    
    return total_count

# ==============================================================================
# --- تابع اصلی برنامه ---
# ==============================================================================
def main():
    if not AUT_COOKIE_VALUE:
        logger.error("❌ متغیر محیطی EWAYS_AUTH_TOKEN تنظیم نشده است.")
        return

    session = get_session()
    
    # 1. دریافت و انتخاب دسته‌بندی‌ها
    source_categories = get_and_parse_categories(session)
    if not source_categories: return

    filtered_categories = get_selected_categories_flexible(source_categories)
    if not filtered_categories:
        logger.info("✅ هیچ دسته‌بندی انتخاب نشد. برنامه خاتمه می‌یابد.")
        return

    # 2. ساخت ساختار درختی دسته‌ها
    category_tree = build_category_tree(source_categories)

    # 3. بررسی بازگشتی دسته‌ها و شمارش محصولات
    logger.info("\n⏳ شروع بررسی بازگشتی دسته‌ها و شمارش محصولات...")
    total_products = 0
    for category in tqdm(filtered_categories, desc="پردازش دسته‌بندی‌های سطح بالا"):
        total_products += count_products_recursive(session, category['id'], category_tree)
    
    logger.info(f"\n✅ فرآیند کامل شد. تعداد کل محصولات در تمام دسته‌ها و زیر دسته‌ها: {total_products}")

if __name__ == "__main__":
    main()
