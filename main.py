import requests
import urllib3
import os
import re

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

# --- آدرس API سایت هدف ---
# !!! لطفاً این آدرس را با Request URL واقعی که پیدا کرده‌اید جایگزین کنید !!!
# بر اساس داده‌ها، به نظر می‌رسد این آدرس صحیح باشد.
SAMSUNG_CATEGORY_API_URL = "https://naminet.co/api/catalog/products?categoryId=13"
# ----------------------------

# --- توابع کمکی ---
def parse_attributes_from_description(description):
    """
    توضیحات کوتاه را به لیستی از ویژگی‌های ووکامرس تبدیل می‌کند.
    """
    attributes = []
    # استفاده از عبارت منظم برای پیدا کردن الگوهای "نام ویژگی : مقدار"
    matches = re.findall(r"(.+?)\s*:\s*(.+)", description)
    for match in matches:
        attr_name = match[0].strip()
        attr_value = match[1].strip()
        if attr_name and attr_value:
            attributes.append({
                "name": attr_name,
                "visible": True,
                "variation": False,
                "options": [attr_value]
            })
    return attributes

def process_price(price_value):
    """ تابع محاسبه قیمت نهایی شما """
    if price_value <= 1: return "0"
    elif price_value <= 7000000: new_price = price_value + 260000
    elif price_value <= 10000000: new_price = price_value * 1.035
    elif price_value <= 20000000: new_price = price_value * 1.025
    elif price_value <= 30000000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price / 10000) * 10000))

# --- توابع اصلی ---

def fetch_products_from_api(api_url):
    """
    محصولات را مستقیماً از API سایت هدف دریافت می‌کند.
    """
    print(f"در حال ارسال درخواست به API: {api_url}")
    try:
        # اضافه کردن هدر User-Agent برای شبیه‌سازی مرورگر
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36'
        }
        response = requests.get(api_url, headers=headers, timeout=30)
        response.raise_for_status() # اگر کد وضعیت خطا بود، استثنا ایجاد می‌کند
        data = response.json()
        
        # ساختار پاسخ ممکن است products['products'] یا فقط products باشد
        products = data.get('products', [])
        if not products and isinstance(data, list):
             products = data # اگر پاسخ مستقیماً یک لیست بود

        print(f"✅ تعداد {len(products)} محصول از API دریافت شد.")
        return products
    except requests.exceptions.RequestException as e:
        print(f"❌ خطا در ارتباط با API سایت هدف: {e}")
        return []
    except ValueError:
        print(f"❌ پاسخ دریافتی از API سایت هدف، JSON معتبر نیست. پاسخ: {response.text[:200]}")
        return []

def create_or_update_product(product_data):
    """
    محصول را در ووکامرس ایجاد یا به‌روزرسانی می‌کند.
    """
    # استفاده از SKU خود سایت به عنوان شناسه منحصر به فرد
    sku = f"NAMIN-{product_data.get('sku', product_data.get('id'))}"
    
    # اگر قیمت صفر یا ناموجود بود، از آن صرف نظر کن
    if product_data.get('price', 0) == 0 or not product_data.get('in_stock', True):
        print(f"محصول '{product_data['name']}' قیمت ندارد یا ناموجود است. نادیده گرفته شد.")
        return

    final_price = process_price(product_data['price'])

    data_to_send = {
        "name": product_data['name'],
        "sku": sku,
        "type": "simple", # چون رنگ‌ها در محصول اصلی مشخص نیستند، simple می‌سازیم
        "regular_price": final_price,
        "description": product_data.get('short_description', ''),
        "manage_stock": False,
        "stock_status": "instock" if product_data.get('in_stock', True) else "outofstock",
        "images": [{"src": img['src']} for img in product_data.get('images', [])],
        "attributes": parse_attributes_from_description(product_data.get('short_description', ''))
    }
    
    print(f"   در حال بررسی محصول با SKU: {sku} ...")
    check_url = f"{WC_API_URL}?sku={sku}"
    try:
        r = requests.get(check_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
        r.raise_for_status()
        existing_products = r.json()
        
        if existing_products and isinstance(existing_products, list) and len(existing_products) > 0:
            product_id = existing_products[0]['id']
            update_url = f"{WC_API_URL}/{product_id}"
            print(f"   محصول موجود است. آپدیت محصول با ID: {product_id} ...")
            res = requests.put(update_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data_to_send, verify=False)
            if res.status_code == 200: print(f"   ✅ محصول '{data_to_send['name']}' آپدیت شد.")
            else: print(f"   ❌ خطا در آپدیت. Status: {res.status_code}, Response: {res.text}")
        else:
            print(f"   محصول جدید است. ایجاد محصول '{data_to_send['name']}' ...")
            res = requests.post(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data_to_send, verify=False)
            if res.status_code == 201: print(f"   ✅ محصول '{data_to_send['name']}' ایجاد شد.")
            else: print(f"   ❌ خطا در ایجاد. Status: {res.status_code}, Response: {res.text}")
            
    except requests.exceptions.RequestException as e:
        print(f"   ❌ خطا در ارتباط با API ووکامرس: {e}")
    except ValueError:
        print(f"   ❌ پاسخ از ووکامرس معتبر نیست. Status: {r.status_code}, Response: {r.text[:200]}")

def main():
    print("شروع فرآیند با استراتژی API...")
    
    products = fetch_products_from_api(SAMSUNG_CATEGORY_API_URL)
    
    if not products:
        print("هیچ محصولی برای پردازش دریافت نشد. برنامه خاتمه می‌یابد.")
        return
        
    for product in products:
        print("\n" + "="*50)
        print(f"پردازش محصول: {product.get('name', 'بدون نام')}")
        create_or_update_product(product)
        time.sleep(1) # وقفه کوتاه بین درخواست‌ها به ووکامرس

    print("\nتمام محصولات پردازش شدند. فرآیند به پایان رسید.")


if __name__ == "__main__":
    main()
