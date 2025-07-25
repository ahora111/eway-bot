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
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ==============================================================================
# --- تنظیمات لاگینگ ---
# ==============================================================================
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=1024*1024, backupCount=5)
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
LOGIN_URL = f"{BASE_URL}/Account/Login"
USERNAME = "09371111558"
PASSWORD = "4310811991"
SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"
PRODUCT_LIST_URL_TEMPLATE = f"{BASE_URL}/Store/List/{{category_id}}/2/2/0/0/0/10000000000?page={{page}}"
PRODUCT_DETAIL_URL_TEMPLATE = f"{BASE_URL}/Store/Detail/{{cat_id}}/{{product_id}}"

# ==============================================================================
# --- توابع مربوط به سایت مبدا (Eways.co) ---
# ==============================================================================

def login_and_get_session():
    """با Selenium لاگین می‌کند و session با کوکی‌ها برمی‌گرداند."""
    options = webdriver.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--headless')  # برای دیباگ، این را کامنت کنید

    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
    
    logger.info("⏳ لاگین به سایت...")
    driver.get(LOGIN_URL)
    
    try:
        username_field = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "UserName")))
        username_field.send_keys(USERNAME)
        
        password_field = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "Password")))
        password_field.send_keys(PASSWORD)
        
        login_button = WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
        login_button.click()
        
        WebDriverWait(driver, 30).until(EC.url_contains("/Dashboard"))
        logger.info("✅ لاگین موفق.")
        
        cookies = driver.get_cookies()
        session = requests.Session()
        for cookie in cookies:
            session.cookies.set(cookie['name'], cookie['value'])
        
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': f"{BASE_URL}/",
            'X-Requested-With': 'XMLHttpRequest'
        })
        session.verify = False
        
        retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        driver.quit()
        return session
    except TimeoutException:
        logger.error("❌ زمان انتظار برای عنصر تمام شد. صفحه ممکن است بارگذاری نشود.")
        driver.quit()
        sys.exit(1)
    except NoSuchElementException as e:
        logger.error(f"❌ عنصر پیدا نشد: {e}")
        driver.quit()
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ خطا در لاگین: {e}")
        driver.quit()
        sys.exit(1)

def get_and_parse_categories(session):
    """دریافت و پارس دسته‌بندی‌ها از API منبع."""
    try:
        response = session.get(SOURCE_CATS_API_URL)
        response.raise_for_status()
        categories = response.json()  # فرض بر این است که JSON برگردانده می‌شود با فیلدهایی مثل [{'id': 1, 'name': 'Cat1', ...}]
        logger.info(f"✅ دریافت {len(categories)} دسته‌بندی از منبع.")
        return categories
    except Exception as e:
        logger.error(f"❌ خطا در دریافت دسته‌بندی‌ها: {e}")
        return None

def get_selected_categories_flexible(source_categories):
    """انتخاب دسته‌بندی‌ها توسط کاربر (انعطاف‌پذیر)."""
    if not source_categories:
        return []
    
    print("\nدسته‌بندی‌های موجود:")
    for idx, cat in enumerate(source_categories, 1):
        print(f"{idx}. {cat.get('name', 'Unknown')} (ID: {cat.get('id', 'Unknown')})")
    
    selected_input = input("شماره دسته‌بندی‌های مورد نظر را با کاما جدا کنید (خالی برای همه، 'none' برای هیچ‌کدام): ").strip()
    
    if selected_input.lower() == 'none':
        return []
    elif not selected_input:
        return source_categories
    
    try:
        indices = [int(i.strip()) for i in selected_input.split(',')]
        selected = [source_categories[i-1] for i in indices if 1 <= i <= len(source_categories)]
        logger.info(f"✅ {len(selected)} دسته‌بندی انتخاب شد.")
        return selected
    except ValueError:
        logger.error("❌ ورودی نامعتبر. هیچ دسته‌بندی انتخاب نشد.")
        return []

def transfer_categories_to_wc(filtered_categories):
    """انتقال دسته‌بندی‌ها به ووکامرس و ساخت نگاشت (mapping)."""
    category_mapping = {}
    for cat in filtered_categories:
        data = {
            'name': cat.get('name', 'Unknown'),
            'slug': re.sub(r'\s+', '-', cat.get('name', 'unknown').lower()),  # slug ساده
            # می‌توانید فیلدهای بیشتری مثل 'description' اضافه کنید
        }
        try:
            response = requests.post(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
            response.raise_for_status()
            wc_cat = response.json()
            category_mapping[cat['id']] = wc_cat['id']
            logger.info(f"✅ دسته‌بندی '{cat['name']}' در ووکامرس ایجاد شد (ID: {wc_cat['id']}).")
        except Exception as e:
            logger.error(f"❌ خطا در ایجاد دسته‌بندی '{cat.get('name')}': {e}")
    return category_mapping

def get_all_products(session, filtered_categories):
    """دریافت تمام محصولات از دسته‌بندی‌های انتخاب‌شده (با pagination و جزئیات)."""
    products = []
    for cat in tqdm(filtered_categories, desc="دریافت محصولات از دسته‌بندی‌ها"):
        cat_id = cat['id']
        page = 1
        while True:
            url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=cat_id, page=page)
            try:
                response = session.get(url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # پارس لیست محصولات (فرضی: تنظیم بر اساس HTML واقعی سایت)
                product_elements = soup.find_all('div', class_='product-item')  # تغییر دهید اگر کلاس متفاوت است
                if not product_elements:
                    break
                
                for elem in product_elements:
                    product_id = elem.get('data-id') or re.search(r'/(\d+)', elem.find('a')['href']).group(1)  # فرضی
                    products.append({'cat_id': cat_id, 'product_id': product_id})
                
                page += 1
                time.sleep(random.uniform(1, 3))  # جلوگیری از بلاک شدن
            except Exception as e:
                logger.error(f"❌ خطا در صفحه {page} دسته {cat_id}: {e}")
                break
    
    # دریافت جزئیات هر محصول
    detailed_products = []
    for p in tqdm(products, desc="دریافت جزئیات محصولات"):
        url = PRODUCT_DETAIL_URL_TEMPLATE.format(cat_id=p['cat_id'], product_id=p['product_id'])
        try:
            response = session.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # پارس جزئیات (فرضی: تنظیم بر اساس HTML واقعی)
            product_data = {
                'cat_id': p['cat_id'],
                'name': soup.find('h1', class_='product-title').text.strip() if soup.find('h1', class_='product-title') else 'Unknown',
                'price': soup.find('span', class_='price').text.strip() if soup.find('span', class_='price') else '0',
                'description': soup.find('div', class_='description').text.strip() if soup.find('div', class_='description') else '',
                'images': [img['src'] for img in soup.find_all('img', class_='product-image')]  # فرضی
                # اضافه کردن فیلدهای بیشتر مثل attributes, stock و غیره
            }
            detailed_products.append(product_data)
            time.sleep(random.uniform(1, 3))
        except Exception as e:
            logger.error(f"❌ خطا در جزئیات محصول {p['product_id']}: {e}")
    
    logger.info(f"✅ {len(detailed_products)} محصول یافت شد.")
    return detailed_products

def process_product(product, stats, category_mapping):
    """پردازش و ارسال/آپدیت یک محصول به ووکامرس."""
    wc_cat_id = category_mapping.get(product['cat_id'])
    if not wc_cat_id:
        logger.error(f"❌ دسته‌بندی برای محصول '{product['name']}' یافت نشد.")
        with stats['lock']:
            stats['failed'] += 1
        return
    
    data = {
        'name': product['name'],
        'type': 'simple',
        'regular_price': re.sub(r'[^\d.]', '', product['price']),  # پاک کردن کاراکترهای غیرعددی
        'description': product['description'],
        'categories': [{'id': wc_cat_id}],
        'images': [{'src': img} for img in product.get('images', [])],
        # اضافه کردن فیلدهای بیشتر مثل 'stock_quantity' اگر دارید
    }
    
    try:
        # فرض: همیشه ایجاد جدید. برای آپدیت، ابتدا با GET چک کنید اگر وجود دارد.
        response = requests.post(f"{WC_API_URL}/products", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
        if response.status_code == 201:
            with stats['lock']:
                stats['created'] += 1
            logger.info(f"🟢 محصول '{product['name']}' ایجاد شد.")
        else:
            # سعی آپدیت (فرضی: نیاز به ID محصول موجود در ووکامرس دارید)
            logger.warning(f"⚠️ سعی آپدیت برای '{product['name']}'.")
            update_response = requests.put(f"{WC_API_URL}/products/{some_existing_id}", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)  # some_existing_id را جایگزین کنید
            if update_response.status_code == 200:
                with stats['lock']:
                    stats['updated'] += 1
            else:
                raise Exception(update_response.text)
    except Exception as e:
        logger.error(f"❌ خطا در پردازش محصول '{product['name']}': {e}")
        with stats['lock']:
            stats['failed'] += 1

def process_product_wrapper(args):
    """Wrapper برای ThreadPoolExecutor."""
    process_product(*args)

def main():
    if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET]):
        logger.error("❌ یکی از متغیرهای محیطی ضروری (WC_*) تنظیم نشده است.")
        return

    session = login_and_get_session()  # لاگین اتوماتیک
    
    source_categories = get_and_parse_categories(session)
    if not source_categories: return

    filtered_categories = get_selected_categories_flexible(source_categories)
    if not filtered_categories:
        logger.info("✅ هیچ دسته‌بندی انتخاب نشد. برنامه خاتمه می‌یابد.")
        return

    category_mapping = transfer_categories_to_wc(filtered_categories)
    if not category_mapping:
        logger.error("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد. برنامه خاتمه می‌یابد.")
        return

    products = get_all_products(session, filtered_categories)
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
