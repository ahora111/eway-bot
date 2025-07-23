import requests
from bs4 import BeautifulSoup
from selenium import webdriver
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

def get_product_links(category_url):
    """
    نسخه تشخیصی برای دیدن لینک‌های خام پیدا شده توسط سلنیوم.
    """
    print("در حال دریافت لینک محصولات از صفحه دسته‌بندی...")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("start-maximized")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    if shutil.which("google-chrome"):
         options.binary_location = shutil.which("google-chrome")

    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", fix_hairline=True)
        
        print("در حال باز کردن صفحه با هویت مخفی...")
        driver.get(category_url)
        
        print("در انتظار بارگذاری کانتینر محصولات...")
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[id^="NAMI-"]')))
        print("کانتینر محصولات بارگذاری شد.")
        
        print("دادن وقفه 5 ثانیه‌ای و انجام اسکرول...")
        time.sleep(5)
        
        last_height = driver.execute_script("return document.body.scrollHeight")
        for i in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                print(f"اسکرول پس از {i+1} بار متوقف شد.")
                break
            last_height = new_height
        
        print("اسکرول تمام شد. استخراج لینک‌ها مستقیماً با سلنیوم...")
        
        product_elements = driver.find_elements(By.CSS_SELECTOR, 'div[id^="NAMI-"] a')
        print(f"سلنیوم تعداد {len(product_elements)} عنصر لینک پیدا کرد.")
        
        links = []
        # --- تغییر کلیدی برای دیباگ ---
        print("\n--- شروع چاپ لینک‌های خام ---")
        for element in product_elements:
            try:
                href = element.get_attribute('href')
                print(f"DEBUG_HREF: {href}") # <<<<<< این خط لینک خام را چاپ می‌کند
                if href and '/p/' in href: # فیلتر هنوز اینجاست
                    if href not in links:
                        links.append(href)
            except Exception as e:
                print(f"خطای جزئی در خواندن یک لینک: {e}")
                continue
        print("--- پایان چاپ لینک‌های خام ---\n")

    except Exception as e:
        print(f"❌ خطایی هنگام دریافت لینک‌ها رخ داد: {e}")
        if driver:
            with open("debug_page.html", "w", encoding="utf-8") as f: f.write(driver.page_source)
            driver.save_screenshot("debug_screenshot.png")
            print("سورس صفحه و اسکرین‌شات برای دیباگ ذخیره شدند.")
        links = []

    finally:
        if driver:
            driver.quit()

    if not links:
        print("❌ هیچ لینکی به لیست نهایی اضافه نشد (به دلیل عدم تطابق با فیلتر).")
    else:
        print(f"✅ تعداد {len(links)} لینک محصول پیدا شد.")
    
    return links

def scrape_product_details(product_url):
    print(f"در حال استخراج اطلاعات از: {product_url}")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    if shutil.which("google-chrome"):
         options.binary_location = shutil.which("google-chrome")
    driver = webdriver.Chrome(options=options)
    stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", fix_hairline=True)
    try:
        driver.get(product_url)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, 'h1')))
        soup = BeautifulSoup(driver.page_source, "html.parser")
    finally:
        driver.quit()
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
        print(f"⚠️ قیمت برای محصول {product_name} یافت نشد.")
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
    sku = f"NAMIN-{product_data['name'].replace(' ', '-')}-{product_data['color'].replace(' ', '-')}"
    final_price = process_price(product_data['price'])
    if final_price == "0":
        print(f"قیمت نهایی برای {product_data['name']} صفر است. ارسال نمی‌شود.")
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

def main():
    category_url = "https://naminet.co/list/llp-13/%DA%AF%D9%88%D8%B4%DB%8C-%D8%B3%D8%A7%D9%85%D8%B3%D9%88%D9%86%DA%AF"
    product_links = get_product_links(category_url)
    if not product_links:
        print("برنامه خاتمه یافت.")
        return
    print(f"\n--- شروع فرآیند برای {len(product_links)} محصول ---\n")
    for link in product_links:
        try:
            product_details = scrape_product_details(link)
            if product_details:
                create_or_update_product(product_details)
                print("-" * 40)
                time.sleep(1)
        except Exception as e:
            print(f"خطای کلی هنگام پردازش لینک {link}: {e}")
            continue

if __name__ == "__main__":
    main()
