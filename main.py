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

# --- توابع محاسباتی و کمکی ---
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

# --- توابع اصلی اسکریپت ---

def scrape_details_from_driver(driver):
    print("در حال استخراج جزئیات از صفحه فعلی...")
    try:
        # --- اصلاح کلیدی: منتظر لود شدن نام محصول در صفحه جدید می‌مانیم ---
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, 'h1')))
        soup = BeautifulSoup(driver.page_source, "html.parser")
    except TimeoutException:
        print("❌ صفحه محصول در زمان مقرر لود نشد. از این محصول صرف نظر می‌شود.")
        driver.save_screenshot("product_page_timeout.png")
        return None
    except Exception as e:
        print(f"خطا در انتظار برای صفحه محصول: {e}")
        return None

    product_name_tag = soup.find("h1", class_="font-bold")
    product_name = product_name_tag.text.strip() if product_name_tag else "نامشخص"
    images = []
    gallery_div = soup.find("div", class_="flex flex-row-reverse gap-4")
    if gallery_div:
        for img in gallery_div.find_all("img"):
            img_url = img.get("src", "")
            if img_url and img_url not in images:
                img_url = img_url.replace("/128/", "/1024/")
                images.append(img_url)
    price = "0"
    price_tag = soup.find("span", class_="price actual-price")
    if price_tag and price_tag.find("p"): price = price_tag.find("p").text.strip()
    if not is_number(price):
        price_p = soup.find("p", class_="text-left text-bold")
        if price_p: price = price_p.text.strip()
    price = price.replace("تومان", "").replace("از", "").strip()
    if not is_number(price):
        print(f"⚠️ قیمت برای محصول '{product_name}' یافت نشد.")
        return None
    color = "نامشخص"
    for div in soup.find_all("div", class_="flex flex-row gap-2 font-semibold"):
        if "رنگ" in div.text:
            color_tags = div.find_all("p")
            if len(color_tags) > 1:
                color = color_tags[1].text.strip()
                break
    attributes = []
    attr_container = soup.find("div", class_="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2")
    if attr_container:
        for attr_box in attr_container.find_all("div", class_="rounded-lg"):
            attr_ps = attr_box.find_all("p")
            if len(attr_ps) >= 2:
                attr_name, attr_value = attr_ps[0].text.strip(), attr_ps[1].text.strip()
                attributes.append({"name": attr_name, "visible": True, "variation": False, "options": [attr_value]})
    attributes.append({"name": "رنگ", "visible": True, "variation": False, "options": [color]})
    return {"name": product_name, "price": price, "color": color, "images": [{"src": img} for img in images], "attributes": attributes}


def create_or_update_product(product_data):
    # این تابع بدون تغییر باقی می‌ماند
    sku = f"NAMIN-{product_data['name'].replace(' ', '-')}-{product_data['color'].replace(' ', '-')}"
    final_price = process_price(product_data['price'])
    if final_price == "0":
        print(f"قیمت نهایی برای محصول '{product_data['name']}' صفر است. ارسال نمی‌شود.")
        return
    print(f"در حال بررسی محصول با SKU: {sku} در ووکامرس...")
    check_url = f"{WC_API_URL}?sku={sku}"
    try:
        r = requests.get(check_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
        r.raise_for_status()
        existing_products = r.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ خطا در ارتباط با API ووکامرس: {e}")
        return
    except ValueError:
        print(f"❌ پاسخ از ووکامرس معتبر نیست. Status: {r.status_code}, Response: {r.text[:200]}")
        return
    data = {
        "name": f"{product_data['name']} - {product_data['color']}",
        "sku": sku, "type": "simple", "regular_price": final_price,
        "description": f"رنگ: {product_data['color']}", "manage_stock": False,
        "stock_status": "instock", "images": product_data['images'],
        "attributes": product_data['attributes']
    }
    if existing_products and isinstance(existing_products, list) and len(existing_products) > 0:
        product_id = existing_products[0]['id']
        update_url = f"{WC_API_URL}/{product_id}"
        print(f"محصول موجود است. آپدیت محصول با ID: {product_id} ...")
        r = requests.put(update_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
        if r.status_code == 200: print(f"✅ محصول '{data['name']}' آپدیت شد.")
        else: print(f"❌ خطا در آپدیت. Status: {r.status_code}, Response: {r.text}")
    else:
        print(f"محصول جدید است. ایجاد محصول '{data['name']}' ...")
        r = requests.post(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
        if r.status_code == 201: print(f"✅ محصول '{data['name']}' ایجاد شد.")
        else: print(f"❌ خطا در ایجاد. Status: {r.status_code}, Response: {r.text}")


def process_single_product(product_index, category_url):
    print("\n" + "="*50)
    print(f"پردازش محصول شماره {product_index + 1}")
    
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    if shutil.which("google-chrome"):
        options.binary_location = shutil.which("google-chrome")

    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", fix_hairline=True)
        
        print("باز کردن صفحه دسته‌بندی...")
        driver.get(category_url)
        
        wait = WebDriverWait(driver, 20)
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'div[id^="NAMI-"]')))
        time.sleep(3)
        
        all_products = driver.find_elements(By.CSS_SELECTOR, 'div[id^="NAMI-"]')
        if product_index >= len(all_products):
            print("ایندکس محصول خارج از محدوده است.")
            return

        product_to_click = all_products[product_index]
        
        print("اسکرول و کلیک روی محصول...")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", product_to_click)
        time.sleep(1)
        driver.execute_script("arguments[0].click();", product_to_click)
        
        product_details = scrape_details_from_driver(driver)
        if product_details:
            create_or_update_product(product_details)

    except Exception as e:
        print(f"❌ خطایی در پردازش محصول {product_index + 1} رخ داد: {e}")
        if driver:
            driver.save_screenshot(f"error_product_{product_index + 1}.png")
    finally:
        if driver:
            driver.quit()
            print(f"درایور برای محصول {product_index + 1} بسته شد.")


def main():
    category_url = "https://naminet.co/list/llp-13/%DA%AF%D9%88%D8%B4%DB%8C-%D8%B3%D8%A7%D9%85%D8%B3%D9%88%D9%86%DA%AF"
    product_count = 0
    
    print("شروع فاز ۱: شمارش محصولات...")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    if shutil.which("google-chrome"):
        options.binary_location = shutil.which("google-chrome")
    
    temp_driver = None
    try:
        temp_driver = webdriver.Chrome(options=options)
        stealth(temp_driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", fix_hairline=True)
        temp_driver.get(category_url)
        WebDriverWait(temp_driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[id^="NAMI-"]')))
        time.sleep(5)
        last_height = temp_driver.execute_script("return document.body.scrollHeight")
        for _ in range(7):
            temp_driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3)
            new_height = temp_driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height: break
            last_height = new_height
        
        product_count = len(temp_driver.find_elements(By.CSS_SELECTOR, 'div[id^="NAMI-"]'))
    except Exception as e:
        print(f"خطا در فاز شمارش محصولات: {e}")
    finally:
        if temp_driver:
            temp_driver.quit()
    
    if product_count == 0:
        print("هیچ محصولی برای پردازش پیدا نشد. برنامه خاتمه می‌یابد.")
        return
        
    print(f"فاز شمارش کامل شد. تعداد {product_count} محصول پیدا شد.")
    
    print("\nشروع فاز ۲: پردازش محصولات...")
    for i in range(product_count):
        process_single_product(i, category_url)
        time.sleep(2) # وقفه ۲ ثانیه‌ای بین هر درخواست کامل برای جلوگیری از مسدود شدن IP

    print("\nتمام محصولات پردازش شدند. فرآیند به پایان رسید.")


if __name__ == "__main__":
    main()
