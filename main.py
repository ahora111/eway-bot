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
from selenium import selenium  # توجه: احتمالاً خطا است، باید از selenium.webdriver استفاده کنید
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
    
    logger.info("⏳ لاگین به سایت...")
    driver.get(LOGIN_URL)
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "Username")))
        driver.find_element(By.ID, "Username").send_keys(USERNAME)
        driver.find_element(By.ID, "Password").send_keys(PASSWORD)
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        WebDriverWait(driver, 10).until(EC.url_contains("/Dashboard"))
        logger.info("✅ لاگین موفق.")
        
        # استخراج کوکی‌ها
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
    except Exception as e:
        logger.error(f"❌ خطا در لاگین: {e}")
        driver.quit()
        sys.exit(1)

def main():
    if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET]):
        logger.error("❌ یکی از متغیرهای محیطی ضروری (WC_*) تنظیم نشده است.")
        return

    session = login_and_get_session()  # لاگین اتوماتیک و جایگزینی همان‌طور که درخواست کردید
    
    # بقیه main مثل قبل
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
