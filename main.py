import requests
import urllib3
import os
import re
import time

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

# --- اطلاعات API سایت هدف (شاه‌کلید) ---
API_BASE_URL = "https://panel.naminet.co/api"
SAMSUNG_CATEGORY_API_URL = f"{API_BASE_URL}/categories/13/products/"
PRODUCT_DETAIL_API_URL_TEMPLATE = f"{API_BASE_URL}/products/{{product_id}}"
PRODUCT_ATTRIBUTES_API_URL_TEMPLATE = f"{API_BASE_URL}/products/attr/{{product_id}}"
AUTH_TOKEN = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJuYmYiOiIxNzUyMjUyMTE2IiwiZXhwIjoiMTc2MDAzMTcxNiIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL2VtYWlsYWRkcmVzcyI6IjA5MzcxMTExNTU4QGhtdGVtYWlsLm5leHQiLCJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6ImE3OGRkZjViLTVhMjMtNDVkZC04MDBlLTczNTc3YjBkMzQzOSIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL25hbWUiOiIwOTM3MTExMTU1OCIsIkN1c3RvbWVySWQiOiIxMDA4NCJ9.kXoXA0atw0M64b6m084Gt4hH9MoC9IFFDFwuHOEdazA"
REFERER_URL = "https://naminet.co/"
# ---------------------------------------------

# --- توابع کمکی ---
def process_price(price_value):
    if price_value <= 1: return "0"
    elif price_value <= 7000000: new_price = price_value + 260000
    elif price_value <= 10000000: new_price = price_value * 1.035
    elif price_value <= 20000000: new_price = price_value * 1.025
    elif price_value <= 30000000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price / 10000) * 10000))

def make_api_request(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36',
            'Authorization': AUTH_TOKEN,
            'Referer': REFERER_URL
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"   ❌ خطا در درخواست API به {url}: {e}")
        return None
    except ValueError:
        print(f"   ❌ پاسخ JSON نامعتبر از {url}")
        return None

# --- توابع اصلی ---

def process_product(product_summary):
    print(f"\n" + "="*50)
    product_name = product_summary.get('name', 'بدون نام')
    product_id = product_summary.get('id')
    print(f"پردازش محصول: {product_name} (ID: {product_id})")

    if not product_id:
        print("   شناسه محصول یافت نشد. نادیده گرفته شد.")
        return

    # 1. دریافت جزئیات اصلی محصول (برای توضیحات و تصاویر)
    print("   در حال دریافت جزئیات اصلی...")
    # main_details = make_api_request(PRODUCT_DETAIL_API_URL_TEMPLATE.format(product_id=product_id))
    # if not main_details:
    #     print("   دریافت جزئیات اصلی ناموفق بود.")
    #     return
    
    # 2. دریافت متغیرها (رنگ‌ها و قیمت‌ها)
    print("   در حال دریافت متغیرها...")
    variations_raw = make_api_request(PRODUCT_ATTRIBUTES_API_URL_TEMPLATE.format(product_id=product_id))
    if not variations_raw or not isinstance(variations_raw, list):
        print("   هیچ متغیری برای این محصول یافت نشد.")
        return

    variations_data = []
    all_colors = set()
    for var in variations_raw:
        if var.get('in_stock') and var.get('price', 0) > 0:
            color_name = var.get('name')
            all_colors.add(color_name)
            variations_data.append({
                "sku": f"NAMIN-{product_id}-{var.get('id')}",
                "regular_price": process_price(var['price']),
                "stock_status": "instock",
                "attributes": [{"name": "رنگ", "option": color_name}]
            })
    
    if not variations_data:
        print("   هیچ متغیر معتبر (موجود و با قیمت) یافت نشد.")
        return

    # 3. آماده‌سازی داده برای ارسال به ووکامرس
    wc_attributes = [
        {"name": "رنگ", "variation": True, "visible": True, "options": list(all_colors)}
    ]
    
    if product_summary.get('short_description'):
        desc_attrs = re.findall(r"(.+?)\s*:\s*(.+)", product_summary['short_description'])
        for name, value in desc_attrs:
            wc_attributes.append({
                "name": name.strip(),
                "variation": False,
                "visible": True,
                "options": [value.strip()]
            })

    product_to_send = {
        "name": product_name,
        "type": "variable",
        "sku": f"NAMIN-{product_summary.get('sku', product_id)}",
        "description": product_summary.get('short_description', ''),
        "images": [{"src": img['src']} for img in product_summary.get('images', [])],
        "attributes": wc_attributes,
        "default_attributes": [{"name": "رنگ", "option": list(all_colors)[0]}] if all_colors else [],
    }

    create_or_update_variable_product(product_to_send, variations_data)


def create_or_update_variable_product(parent_product_data, variations_data):
    sku = parent_product_data['sku']
    print(f"   در حال بررسی محصول متغیر با SKU: {sku} ...")
    
    check_url = f"{WC_API_URL}?sku={sku}"
    try:
        r = requests.get(check_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
        r.raise_for_status()
        existing_products = r.json()
        
        product_id = None
        if existing_products:
            product_id = existing_products[0]['id']
            print(f"   محصول موجود است (ID: {product_id}). در حال آپدیت...")
            update_url = f"{WC_API_URL}/{product_id}"
            requests.put(update_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=parent_product_data, verify=False)
        else:
            print(f"   محصول جدید است. در حال ایجاد محصول متغیر '{parent_product_data['name']}' ...")
            res_main = requests.post(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=parent_product_data, verify=False)
            if res_main.status_code == 201:
                product_id = res_main.json()['id']
                print(f"   ✅ محصول اصلی ایجاد شد (ID: {product_id}).")
            else:
                print(f"   ❌ خطا در ایجاد محصول اصلی. Status: {res_main.status_code}, Response: {res_main.text}")
                return

        if product_id:
            print(f"   در حال ایجاد/آپدیت {len(variations_data)} متغیر...")
            variations_url = f"{WC_API_URL}/{product_id}/variations/batch"
            batch_data = {"create": variations_data} # برای سادگی، فعلاً همیشه متغیرها را ایجاد می‌کنیم
            res_vars = requests.post(variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=batch_data, verify=False)
            if res_vars.status_code in [200, 201]:
                print(f"   ✅ متغیرها با موفقیت ایجاد/آپدیت شدند.")
            else:
                print(f"   ❌ خطا در ایجاد/آپدیت متغیرها. Status: {res_vars.status_code}, Response: {res_vars.text}")

    except Exception as e:
        print(f"   ❌ خطای کلی در ارتباط با ووکامرس: {e}")

def main():
    print("شروع فرآیند با استراتژی نهایی (API پیشرفته و بهینه)...")
    
    api_response = make_api_request(SAMSUNG_CATEGORY_API_URL)
    if not api_response:
        print("هیچ داده‌ای از API لیست محصولات دریافت نشد.")
        return

    products_list = api_response if isinstance(api_response, list) else api_response.get('products', [])
    if not products_list:
        print("هیچ محصولی در پاسخ API یافت نشد.")
        return
        
    for product_summary in products_list:
        process_product(product_summary)
        time.sleep(2)

    print("\nتمام محصولات پردازش شدند. فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
    
