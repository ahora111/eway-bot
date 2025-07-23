import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium_stealth import stealth
import time
import urllib3
import shutil
import os

# غیرفعال کردن هشدار SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- اطلاعات ووکامرس (خوانده شده از Secrets گیت‌هاب) ---
WC_API_URL = os.environ.get("WC_API_URL")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET]):
    print("❌ متغیرهای محیطی ووکامرس به درستی تنظیم نشده‌اند. برنامه متوقف می‌شود.")
    exit(1)
# ---------------------------------

# --- توابع محاسباتی و کمکی (بدون تغییر) ---
def is_number(s):
    try:
        float(s.replace(",", "").replace("٬", ""))
        return True
    except ValueError:
        return False

def process_price(price_str):
    price_str = price_str.replace("٬", "").replace(",", "").strip()
    if is_number(price_str):
        price_value = float(price_str)
        if price_value <= 1: return "0"
        elif price_value <= 7000000: new_price = price_value + 260000
        elif price_value <= 10000000: new_price = price_value * 1.035
        elif price_value <= 20000000: new_price = price_value * 1.025
        elif price_value <= 30000000: new_price = price_value * 1.02
        else: new_price = price_value * 1.015
        return str(int(round(new_price / 10000) * 10000))
    return "0"

# --- توابع اصلی اسکریپت (با استراتژی جدید) ---

def get_all_product_urls(category_url):
    """
    فاز ۱: تمام URL های محصولات را به صورت یکجا و سریع جمع‌آوری می‌کند.
    """
    print("="*20 + " فاز ۱: شروع جمع‌آوری URL ها " + "="*20)
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    if shutil.which("google-chrome"):
        options.binary_location = shutil.which("google-chrome")

    driver = None
    urls = []
    try:
        driver = webdriver.Chrome(options=options)
        stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", fix_hairline=True)
        
        driver.get(category_url)
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[id^="NAMI-"]')))
        time.sleep(5)
        
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(7):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height: break
            last_height = new_height

        # ترفند جاوا اسکریپت برای خواندن لینک از دل تگ‌های a بدون href
        product_links = driver.execute_script("""
            var links = [];
            var elements = document.querySelectorAll('div[id^="NAMI-"] a');
            elements.forEach(function(el) {
                // با شبیه‌سازی یک کلیک، می‌توانیم به لینک مقصد برسیم
                // اما راه ساده‌تر، خواندن مستقیم از داده‌های خود المنت است اگر موجود باشد
                // در این حالت، چون لینک‌ها با JS ساخته می‌شوند، بهترین راه پیدا کردن تگ a و گرفتن href آن است
                // از آنجایی که href وجود ندارد، باید به دنبال شناسه محصول باشیم
                var parentDiv = el.closest('div[id^="NAMI-"]');
                if (parentDiv) {
                    var id = parentDiv.id.replace('NAMI-', '');
                    var url = 'https://naminet.co/p/' + id;
                    if (!links.includes(url)) {
                        links.push(url);
                    }
                }
            });
            return links;
        """)
        
        urls = product_links

    except Exception as e:
        print(f"❌ خطای اصلی در فاز جمع‌آوری URL رخ داد: {e}")
    finally:
        if driver:
            driver.quit()
    
    print(f"✅ فاز ۱ کامل شد. تعداد {len(urls)} URL منحصر به فرد جمع‌آوری شد.")
    return urls


def scrape_and_process_product(product_url):
    """
    فاز ۲: اطلاعات یک محصول را از URL داده شده استخراج و به ووکامرس ارسال می‌کند.
    """
    print("\n" + "="*20 + f" فاز ۲: استخراج از {product_url} 
