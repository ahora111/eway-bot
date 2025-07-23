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

def process_product_group(product_summary):
    print(f"\n" + "="*50)
    product_name = product_summary.get('name', 'بدون نام')
    product_id = product_summary.get('id')
    print(f"پردازش گروه محصول: {product_name} (ID: {product_id})")

    if not product_id:
        print("   شناسه محصول یافت نشد. نادیده گرفته شد.")
        return

    # 1. دریافت جزئیات کامل محصول (شامل متغیرها)
    print("   در حال دریافت جزئیات کامل...")
    details = make_api_request(PRODUCT_DETAIL_API_URL_TEMPLATE.format(product_id=product_id))
    if not details:
        print("   دریافت جزئیات کامل ناموفق بود.")
        return

    variations_data = []
    all_attributes_map = {}
    
    # 2. استخراج متغیرها از بخش attributes
    if 'attributes' in details:
        for attr_group in details['attributes']:
            attr_name = attr_group.get('product_attribute_name')
            if attr_name and 'attribute_values' in attr_group:
                if attr_name not in all_attributes_map:
                    all_attributes_map[attr_name] = set()
                
                for var in attr_group['attribute_values']:
                    if var.get('in_stock') and var.get('price', 0) > 0:
                        option_name = var.get('name')
                        all_attributes_map[attr_name].add(option_name)
                        variations_data.append({
                            "sku": f"NAMIN-{product_id}-{var.get('id')}",
                            "regular_price": process_price(var['price']),
                            "stock_status": "instock",
                            "attributes": [{"name": attr_name, "option": option_name}]
                        })

    if not variations_data:
        # اگر محصول متغیر نبود، به عنوان محصول ساده پردازش کن
        print("   محصول متغیر نبود یا متغیر معتبری نداشت. در حال پردازش به عنوان محصول ساده...")
        if product_summary.get('price', 0) > 0 and product_summary.get('in_stock'):
            create_or_update_simple_product(product_summary)
        else:
            print("   محصول ساده قیمت ندارد یا ناموجود است.")
        return

    # 3. آماده‌سازی داده برای ارسال به ووکامرس
    wc_attributes = []
    for name, options in all_attributes_map.items():
        wc_attributes.append({
            "name": name,
            "variation": True,
            "visible": True,
            "options": list(options)
        })
    
    # اضافه کردن ویژگی‌های غیرمتغیر از توضیحات
    if details.get('short_description'):
        desc_attrs = re.findall(r"(.+?)\s*:\s*(.+)", details['short_description'])
        for name, value in desc_attrs:
            if name.strip() not in all_attributes_map:
                wc_attributes.append({
                    "name": name.strip(),
                    "variation": False,
                    "visible": True,
                    "options": [value.strip()]
                })

    product_to_send = {
        "name": product_name,
        "type": "variable",
        "sku": f"NAMIN-{details.get('sku', product_id)}",
        "description": details.get('short_description', ''),
        "images": [{"src": img['src']} for img in details.get('images', [])],
        "attributes": wc_attributes,
        "default_attributes": [{"name": list(all_attributes_map.keys())[0], "option": list(list(all_attributes_map.values())[0])[0]}] if all_attributes_map else [],
    }

    create_or_update_variable_product(product_to_send, variations_data)


def create_or_update_simple_product(product_data):
    """
    یک محصول ساده را در ووکامرس ایجاد یا آپدیت می‌کند.
    """
    sku = f"NAMIN-{product_data.get('sku', product_data.get('id'))}"
    final_price = process_price(product_data['price'])
    
    data_to_send = {
        "name": product_data['name'],
        "sku": sku,
        "type": "simple",
        "regular_price": final_price,
        "description": product_data.get('short_description', ''),
        "stock_status": "instock",
        "images": [{"src": img['src']} for img in product_data.get('images', [])]
    }
    
    # منطق ارسال به ووکامرس (مشابه قبل)
    print(f"   در حال بررسی محصول ساده با SKU: {sku} ...")
    # ... (کد ارسال به ووکامرس برای محصول ساده اینجا قرار می‌گیرد)


def create_or_update_variable_product(parent_product_data, variations_data):
    # این تابع تقریباً بدون تغییر است
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
            # ابتدا متغیرهای قدیمی را پاک می‌کنیم (برای سادگی)
            # در آینده می‌توان منطق آپدیت هوشمند را اضافه کرد
            existing_vars_url = f"{WC_API_URL}/{product_id}/variations"
            existing_vars = requests.get(existing_vars_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET)).json()
            if existing_vars:
                delete_ids = [v['id'] for v in existing_vars]
                if delete_ids:
                    print(f"   در حال پاک کردن {len(delete_ids)} متغیر قدیمی...")
                    requests.post(f"{existing_vars_url}/batch", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json={"delete": delete_ids})
            
            print(f"   در حال ایجاد {len(variations_data)} متغیر جدید...")
            variations_url = f"{WC_API_URL}/{product_id}/variations/batch"
            batch_data = {"create": variations_data}
            res_vars = requests.post(variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=batch_data, verify=False)
            if res_vars.status_code in [200, 201]:
                print(f"   ✅ متغیرها با موفقیت ایجاد شدند.")
            else:
                print(f"   ❌ خطا در ایجاد متغیرها. Status: {res_vars.status_code}, Response: {res_vars.text}")

    except Exception as e:
        print(f"   ❌ خطای کلی در ارتباط با ووکامرس: {e}")

def main():
    print("شروع فرآیند با استراتژی نهایی (API پیشرفته و بهینه)...")
    
    api_response = make_api_request(SAMSUNG_CATEGORY_API_URL)
    if not api_response:
        return

    products_list = api_response if isinstance(api_response, list) else api_response.get('products', [])
    if not products_list:
        print("هیچ محصولی در پاسخ API یافت نشد.")
        return
        
    for product_summary in products_list:
        process_product_group(product_summary)
        time.sleep(2)

    print("\nتمام محصولات پردازش شدند. فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
