import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
import urllib3

# غیرفعال کردن هشدار SSL (برای ارتباط با ووکامرس)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- اطلاعات ووکامرس سایت شما ---
WC_API_URL = "https://pakhshemobile.ir/wp-json/wc/v3/products"
WC_CONSUMER_KEY = "ck_251fa0545dc395c3d01788a5d9be814aab7575c8"
WC_CONSUMER_SECRET = "cs_b2b0dca5807d49e8e10ef2a9edcc00bd08c82af3"
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
        if price_value <= 1:
            return "0" # قیمت نامعتبر
        elif price_value <= 7000000:
            new_price = price_value + 260000
        elif price_value <= 10000000:
            new_price = price_value * 1.035
        elif price_value <= 20000000:
            new_price = price_value * 1.025
        elif price_value <= 30000000:
            new_price = price_value * 1.02
        else: # بالای 30 میلیون
            new_price = price_value * 1.015
        
        # رند کردن به نزدیکترین ۱۰ هزار تومان
        return str(int(round(new_price / 10000) * 10000))
    return "0" # اگر ورودی عدد نبود

# --- توابع اصلی اسکریپت ---

def get_product_links(category_url):
    """
    این تابع به صفحه دسته‌بندی محصولات میره و لینک تمام محصولات رو استخراج می‌کنه.
    """
    print("در حال دریافت لینک محصولات از صفحه دسته‌بندی...")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # استفاده از webdriver-manager برای مدیریت خودکار درایور کروم
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    driver.get(category_url)
    time.sleep(5)  # صبر می‌کنیم تا همه محصولات با جاوااسکریپت لود شوند

    soup = BeautifulSoup(driver.page_source, "html.parser")
    driver.quit()

    links = []
    # پیدا کردن تگ a داخل هر باکس محصول
    for product_box in soup.find_all("div", id=lambda x: x and x.startswith("NAMI-")):
        link_tag = product_box.find("a", href=True)
        if link_tag:
            # ساخت لینک کامل
            full_link = "https://naminet.co" + link_tag['href']
            if full_link not in links:
                links.append(full_link)
    
    print(f"✅ تعداد {len(links)} لینک محصول پیدا شد.")
    return links

def scrape_product_details(product_url):
    """
    این تابع وارد صفحه یک محصول خاص میشه و تمام جزئیاتش رو استخراج می‌کنه.
    """
    print(f"در حال استخراج اطلاعات از: {product_url}")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    try:
        driver.get(product_url)
        time.sleep(3) # صبر برای لود کامل صفحه محصول
        soup = BeautifulSoup(driver.page_source, "html.parser")
    finally:
        driver.quit()

    # --- استخراج اطلاعات با استفاده از کدهای خودت ---
    
    # الف) نام محصول
    product_name_tag = soup.find("h1", class_="font-bold")
    product_name = product_name_tag.text.strip() if product_name_tag else "نامشخص"

    # ب) تصاویر
    images = []
    # این کلاس برای تصاویر اصلی محصول در گالری استفاده میشه
    gallery_div = soup.find("div", class_="flex flex-row-reverse gap-4")
    if gallery_div:
        for img in gallery_div.find_all("img"):
            img_url = img.get("src", "")
            if img_url and img_url not in images:
                # تبدیل لینک تصویر به لینک با کیفیت بالاتر
                img_url = img_url.replace("/128/", "/1024/")
                images.append(img_url)

    # ج) قیمت
    price = "0"
    price_tag = soup.find("span", class_="price actual-price")
    if price_tag:
        price_p = price_tag.find("p")
        if price_p:
            price = price_p.text.strip()
    if not is_number(price): # اگر قیمت در حالت اول پیدا نشد یا عدد نبود
        price_p = soup.find("p", class_="text-left text-bold")
        if price_p:
            price = price_p.text.strip()
    
    # حذف کاراکترهای اضافی از قیمت
    price = price.replace("تومان", "").replace("از", "").strip()
    if not is_number(price):
        print(f"⚠️ قیمت برای محصول {product_name} یافت نشد. از این محصول صرف نظر می‌شود.")
        return None

    # د) رنگ
    color = "نامشخص"
    # دنبال دیوی می‌گردیم که کلمه "رنگ" در آن باشد
    for div in soup.find_all("div", class_="flex flex-row gap-2 font-semibold"):
        if "رنگ" in div.text:
            # دومین تگ p معمولا مقدار رنگ است
            color_tags = div.find_all("p")
            if len(color_tags) > 1:
                color = color_tags[1].text.strip()
                break # بعد از پیدا کردن رنگ، از حلقه خارج شو

    # ه) ویژگی‌های فنی (Attributes)
    attributes = []
    # این کلاس برای باکس‌های ویژگی‌ها صحیح است
    attr_container = soup.find("div", class_="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2")
    if attr_container:
        for attr_box in attr_container.find_all("div", class_="rounded-lg"):
            attr_ps = attr_box.find_all("p")
            if len(attr_ps) >= 2:
                attr_name = attr_ps[0].text.strip()
                attr_value = attr_ps[1].text.strip()
                attributes.append({"name": attr_name, "visible": True, "variation": False, "options": [attr_value]})

    # افزودن رنگ به لیست ویژگی‌ها برای نمایش در ووکامرس
    attributes.append({"name": "رنگ", "visible": True, "variation": False, "options": [color]})

    return {
        "name": product_name,
        "price": price,
        "color": color,
        "images": [{"src": img} for img in images],
        "attributes": attributes
    }


def create_or_update_product(product_data):
    """
    این تابع اطلاعات محصول را به ووکامرس ارسال می‌کند (ایجاد یا آپدیت).
    """
    # ساخت یک SKU منحصر به فرد بر اساس نام و رنگ محصول
    sku = f"NAMIN-{product_data['name'].replace(' ', '-')}-{product_data['color'].replace(' ', '-')}"
    
    # پردازش قیمت نهایی
    final_price = process_price(product_data['price'])
    if final_price == "0":
        print(f"قیمت نهایی برای {product_data['name']} صفر است. ارسال نمی‌شود.")
        return

    print(f"در حال بررسی محصول با SKU: {sku} در ووکامرس...")
    
    # بررسی وجود محصول با SKU
    check_url = f"{WC_API_URL}?sku={sku}"
    try:
        r = requests.get(check_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
        r.raise_for_status() # اگر خطایی مثل 401 یا 500 بود، استثنا ایجاد می‌کند
        existing_products = r.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ خطا در ارتباط با API ووکامرس: {e}")
        return
    except ValueError: # خطا در خواندن JSON
        print(f"❌ پاسخ دریافتی از ووکامرس معتبر نیست. Status: {r.status_code}, Response: {r.text[:200]}")
        return

    # آماده‌سازی دیتا برای ارسال
    data = {
        "name": f"{product_data['name']} - {product_data['color']}",
        "sku": sku,
        "type": "simple",
        "regular_price": final_price,
        "description": f"رنگ: {product_data['color']}",
        "short_description": "",
        "manage_stock": False,
        "stock_status": "instock", # موجود در انبار
        "images": product_data['images'],
        "attributes": product_data['attributes']
    }

    if existing_products and isinstance(existing_products, list) and len(existing_products) > 0:
        # --- آپدیت محصول ---
        product_id = existing_products[0]['id']
        update_url = f"{WC_API_URL}/{product_id}"
        print(f"محصول موجود است. در حال آپدیت محصول با ID: {product_id} ...")
        r = requests.put(update_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
        if r.status_code == 200:
            print(f"✅ محصول '{data['name']}' با موفقیت آپدیت شد.")
        else:
            print(f"❌ خطا در آپدیت محصول. Status: {r.status_code}, Response: {r.text}")
    else:
        # --- ایجاد محصول جدید ---
        print(f"محصول جدید است. در حال ایجاد محصول '{data['name']}' ...")
        r = requests.post(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
        if r.status_code == 201:
            print(f"✅ محصول '{data['name']}' با موفقیت ایجاد شد.")
        else:
            print(f"❌ خطا در ایجاد محصول. Status: {r.status_code}, Response: {r.text}")

def main():
    """
    تابع اصلی برای اجرای کل فرآیند.
    """
    category_url = "https://naminet.co/list/llp-13/%DA%AF%D9%88%D8%B4%DB%8C-%D8%B3%D8%A7%D9%85%D8%B3%D9%88%D9%86%DA%AF"
    
    # 1. گرفتن تمام لینک‌های محصول
    product_links = get_product_links(category_url)
    
    if not product_links:
        print("هیچ لینکی برای پردازش یافت نشد. برنامه خاتمه می‌یابد.")
        return

    # 2. حلقه روی هر لینک، استخراج اطلاعات و ارسال به ووکامرس
    for link in product_links:
        try:
            product_details = scrape_product_details(link)
            if product_details:
                create_or_update_product(product_details)
                print("-" * 30)
                time.sleep(1) # یک ثانیه وقفه بین هر درخواست برای جلوگیری از فشار به سرور
        except Exception as e:
            print(f"خطای غیرمنتظره هنگام پردازش لینک {link}: {e}")
            continue

if __name__ == "__main__":
    main()
