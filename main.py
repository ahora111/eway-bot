import time
import requests
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from collections import defaultdict

# غیرفعال کردن هشدار SSL
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ==============================================================================
# بخش ۱: توابع استخراج داده از کد اول (منبع: API Naminet)
# ==============================================================================

def fetch_from_naminet_api():
    """داده‌ها را از API نامی‌نت دریافت می‌کند."""
    logging.info("در حال دریافت اطلاعات از منبع اول (API Naminet)...")
    url = "https://panel.naminet.co/api/catalog/productGroupsAttrNew?term="
    headers = {
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJuYmYiOiIxNzUyMjUyMTE2IiwiZXhwIjoiMTc2MDAzMTcxNiIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL2VtYWlsYWRkcmVzcyI6IjA5MzcxMTExNTU4QGhtdGVtYWlsLm5leHQiLCJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6ImE3OGRkZjViLTVhMjMtNDVkZC04MDBlLTczNTc3YjBkMzQzOSIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL25hbWUiOiIwOTM3MTExMTU1OCIsIkN1c3RvbWVySWQiOiIxMDA4NCJ9.kXoXA0atw0M64b6m084Gt4hH9MoC9IFFDFwuHOEdazA"
    }
    try:
        response = requests.get(url, headers=headers, verify=False, timeout=20)
        response.raise_for_status()
        data = response.json()
        products = []
        for parent in data.get("ParentCategories", []):
            for category in parent.get("Data", []):
                for item in category.get("Data", []):
                    # ترکیب نام محصول و رنگ برای ایجاد یک نام منحصر به فرد
                    full_name = f"{item.get('ProductName', '')} {item.get('Name', '')}".strip()
                    price = item.get("final_price_value", 0)
                    if full_name and price > 0:
                        products.append({"name": full_name, "price": int(price)})
        logging.info(f"✅ از منبع اول {len(products)} محصول دریافت شد.")
        return products
    except Exception as e:
        logging.error(f"❌ خطا در دریافت اطلاعات از منبع اول: {e}")
        return []

# ==============================================================================
# بخش ۲: توابع استخراج داده از کد دوم (منبع: وب‌سایت Hamrahtel)
# ==============================================================================

def get_driver():
    """یک درایور Selenium برای مرورگر کروم ایجاد می‌کند."""
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36")
    service = Service()
    return webdriver.Chrome(service=service, options=options)

def scroll_page(driver, scroll_pause_time=1):
    """صفحه را تا انتها اسکرول می‌کند تا همه محصولات بارگذاری شوند."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause_time)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

def fetch_from_hamrahtel_site():
    """داده‌ها را با اسکرپینگ از سایت همراه‌تل دریافت می‌کند."""
    logging.info("در حال دریافت اطلاعات از منبع دوم (سایت Hamrahtel)...")
    driver = get_driver()
    products = []
    try:
        urls = {
            "mobile": "https://hamrahtel.com/quick-checkout?category=mobile",
            "tablet": "https://hamrahtel.com/quick-checkout?category=tablet",
        }
        for category, url in urls.items():
            driver.get(url)
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[class^="mantine-"] > .mantine-Text-root')))
            scroll_page(driver)
            
            # استخراج داده‌ها به صورت جفتی (نام و قیمت)
            elements = driver.find_elements(By.CSS_SELECTOR, 'div[class^="mantine-"] > .mantine-Text-root')
            # حذف المان‌های اضافی ابتدای لیست
            # این عدد ممکن است نیاز به تنظیم داشته باشد
            cleaned_elements = [el.text.strip() for el in elements if el.text.strip()][25:]
            
            i = 0
            while i < len(cleaned_elements) - 1:
                name = cleaned_elements[i]
                price_str = cleaned_elements[i+1].replace("تومان", "").replace(",", "").replace("٬", "").strip()
                
                # بررسی می‌کنیم که آیا آیتم بعدی یک عدد (قیمت) است یا خیر
                if price_str.isdigit():
                    products.append({"name": name, "price": int(price_str)})
                    i += 2
                else:
                    i += 1 # اگر قیمت نبود، فقط از نام عبور کن
        
        logging.info(f"✅ از منبع دوم {len(products)} محصول دریافت شد.")
        return products
    except Exception as e:
        logging.error(f"❌ خطا در دریافت اطلاعات از منبع دوم: {e}")
        return []
    finally:
        driver.quit()

# ==============================================================================
# بخش ۳: منطق پردازش قیمت، مقایسه و نمایش خروجی
# ==============================================================================

def process_price(price):
    """منطق افزایش قیمت که در هر دو اسکریپت مشترک بود."""
    if price <= 1:
        return 0
    elif price <= 7000000:
        price_with_increase = price + 260000
    elif price <= 10000000:
        price_with_increase = price * 1.035
    elif price <= 20000000:
        price_with_increase = price * 1.025
    elif price <= 30000000:
        price_with_increase = price * 1.02
    else: # بالای ۳۰ میلیون
        price_with_increase = price * 1.015
    # گرد کردن به نزدیک‌ترین ۱۰۰ هزار تومان
    return round(price_with_increase, -5)

def normalize_name(name):
    """نام محصول را برای مقایسه بهتر، استاندارد می‌کند."""
    # تبدیل به حروف کوچک، حذف فاصله‌های اضافی و جایگزینی کاراکترهای فارسی
    return name.lower().strip().replace('ی', 'ي').replace('ک', 'ك')

def main():
    """تابع اصلی برای اجرای کل فرآیند."""
    
    # ۱. دریافت داده‌ها از هر دو منبع
    naminet_products = fetch_from_naminet_api()
    hamrahtel_products = fetch_from_hamrahtel_site()
    
    # ۲. تجمیع تمام محصولات در یک دیکشنری
    # کلید: نام استاندارد شده محصول
    # مقدار: لیستی از قیمت‌های نهایی (پس از اعمال درصد افزایش)
    all_products = defaultdict(list)

    logging.info("در حال پردازش و تجمیع داده‌ها...")
    
    for product in naminet_products:
        final_price = process_price(product['price'])
        if final_price > 0:
            norm_name = normalize_name(product['name'])
            all_products[norm_name].append(final_price)
            
    for product in hamrahtel_products:
        final_price = process_price(product['price'])
        if final_price > 0:
            norm_name = normalize_name(product['name'])
            all_products[norm_name].append(final_price)
            
    # ۳. پیدا کردن محصولات مشترک و کمترین قیمت آن‌ها
    common_products = {}
    for name, prices in all_products.items():
        if len(prices) > 1: # اگر بیش از یک قیمت داشت یعنی مشترک است
            common_products[name] = min(prices)
            
    # ۴. نمایش نتایج
    print("\n" + "="*50)
    if not common_products:
        print("هیچ محصول مشترکی بین دو منبع پیدا نشد.")
    else:
        print(f"نتایج مقایسه: {len(common_products)} محصول مشترک یافت شد.")
        print("لیست محصولات مشترک با کمترین قیمت موجود:")
        print("-"*50)
        
        # مرتب‌سازی بر اساس نام برای نمایش بهتر
        for name in sorted(common_products.keys()):
            price = common_products[name]
            # نمایش نام با حروف بزرگ و قیمت با جداکننده هزارگان
            print(f"- {name.title()}: {price:,.0f} تومان")
            
    print("="*50)

if __name__ == "__main__":
    main()
