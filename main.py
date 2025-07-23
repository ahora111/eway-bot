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

def parse_attributes_from_description(description):
    attributes = []
    if not description:
        return attributes
    lines = description.split('\r\n')
    for line in lines:
        if ':' in line:
            parts = line.split(':', 1)
            attr_name = parts[0].strip()
            attr_value = parts[1].strip()
            if attr_name and attr_value:
                attributes.append({
                    "name": attr_name,
                    "visible": True,
                    "variation": False,
                    "options": [attr_value]
                })
    return attributes

# --- توابع اصلی ---

def process_product_group(product_summary):
    print(f"\n" + "="*50)
    product_name = product_summary.get('name', 'بدون نام')
    product_id = product_summary.get('id')
    print(f"پردازش گروه محصول: {product_name} (ID: {product_id})")

    if not product_id:
        print("   شناسه محصول یافت نشد. نادیده گرفته شد.")
        return

    print("   در حال دریافت جزئیات کامل...")
    details = make_api_request(PRODUCT_DETAIL_API_URL_TEMPLATE.format(product_id=product_id))
    if not details:
        print("   دریافت جزئیات کامل ناموفق بود.")
        return

    variations_data = []
    all_attributes_map = {}
    
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
        print("   محصول متغیر نبود یا متغیر معتبری نداشت. در حال پردازش به عنوان محصول ساده...")
        if details.get('price', 0) > 0 and details.get('in_stock'):
            create_or_update_simple_product(details)
        else:
            print("   محصول ساده قیمت ندارد یا ناموجود است.")
        return

    wc_attributes = []
    for name, options in all_attributes_map.items():
        wc_attributes.append({
            "name": name,
            "variation": True,
            "visible": True,
            "options": list(options)
        })
    
    if details.get('short_description'):
        desc_attrs = parse_attributes_from_description(details['short_description'])
        for desc_attr in desc_attrs:
            if desc_attr['name'] not in all_attributes_map:
                wc_attributes.append(desc_attr)

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
    sku = f"NAMIN-{product_data.get('sku', product_data.get('id'))}"
    final_price = process_price(product_data['price'])
    
    data_to_send = {
        "name": product_data['name'],
        "sku": sku,
        "type": "simple",
        "regular_price": final_price,
        "description": product_data.get('short_description', ''),
        "stock_status": "instock",
        "images": [{"src": img['src']} for img in product_data.get('images', [])],
        "attributes": parse_attributes_from_description(product_data.get('short_description', ''))
    }
    
    print(f"   در حال بررسی محصول ساده با SKU: {sku} ...")
    _send_to_woocommerce(sku, data_to_send)


def create_or_update_variable_product(parent_product_data, variations_data):
    sku = parent_product_data['sku']
    print(f"   در حال بررسی محصول متغیر با SKU: {sku} ...")
    
    product_id = _send_to_woocommerce(sku, parent_product_data)

    if product_id:
        print(f"   در حال ایجاد/آپدیت {len(variations_data)} متغیر برای محصول ID: {product_id}...")
        variations_url = f"{WC_API_URL}/{product_id}/variations/batch"
        # برای آپدیت، ابتدا متغیرهای موجود را پاک می‌کنیم
        existing_vars_url = f"{WC_API_URL}/{product_id}/variations"
        existing_vars_resp = requests.get(existing_vars_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
        if existing_vars_resp.status_code == 200:
            delete_ids = [v['id'] for v in existing_vars_resp.json()]
            if delete_ids:
                print(f"   در حال پاک کردن {len(delete_ids)} متغیر قدیمی...")
                requests.post(f"{variations_url}", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json={"delete": delete_ids}, verify=False)
        
        # سپس متغیرهای جدید را ایجاد می‌کنیم
        batch_data = {"create": variations_data}
        res_vars = requests.post(variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=batch_data, verify=False)
        if res_vars.status_code in [200, 201]:
            print(f"   ✅ متغیرها با موفقیت ایجاد/آپدیت شدند.")
        else:
            print(f"   ❌ خطا در ایجاد/آپدیت متغیرها. Status: {res_vars.status_code}, Response: {res_vars.text}")


def _send_to_woocommerce(sku, data):
    """ تابع داخلی برای ارسال داده به ووکامرس و مدیریت ایجاد/آپدیت """
    check_url = f"{WC_API_URL}?sku={sku}"
    try:
        r = requests.get(check_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
        r.raise_for_status()
        existing_products = r.json()
        
        if existing_products:
            product_id = existing_products[0]['id']
            print(f"   محصول موجود است (ID: {product_id}). در حال آپدیت...")
            update_url = f"{WC_API_URL}/{product_id}"
            res = requests.put(update_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
            if res.status_code == 200:
                print(f"   ✅ محصول '{data['name']}' آپدیت شد.")
                return product_id
            else:
                print(f"   ❌ خطا در آپدیت. Status: {res.status_code}, Response: {res.text}")
                return None
        else:
            print(f"   محصول جدید است. در حال ایجاد '{data['name']}' ...")
            res = requests.post(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
            if res.status_code == 201:
                product_id = res.json()['id']
                print(f"   ✅ محصول اصلی ایجاد شد (ID: {product_id}).")
                return product_id
            else:
                print(f"   ❌ خطا در ایجاد محصول اصلی. Status: {res.status_code}, Response: {res.text}")
                return None
    except Exception as e:
        print(f"   ❌ خطای کلی در ارتباط با ووکامرس: {e}")
        return None


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
        process_product_group(product_summary)
        time.sleep(2)

    print("\nتمام محصولات پردازش شدند. فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
