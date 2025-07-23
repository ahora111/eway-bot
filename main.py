import requests
import os
import urllib3
import re
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- اطلاعات ووکامرس ---
WC_API_URL = os.environ.get("WC_API_URL")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET]):
    print("❌ متغیرهای محیطی ووکامرس به درستی تنظیم نشده‌اند.")
    exit(1)

# --- اطلاعات API سایت هدف ---
API_BASE_URL = "https://panel.naminet.co/api"
CATEGORY_API_URL = f"{API_BASE_URL}/categories/13/products/"
PRODUCT_DETAIL_API_URL = f"{API_BASE_URL}/products/{{product_id}}"
PRODUCT_ATTR_API_URL = f"{API_BASE_URL}/products/attr/{{product_id}}"
AUTH_TOKEN = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJuYmYiOiIxNzUyMjUyMTE2IiwiZXhwIjoiMTc2MDAzMTcxNiIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL2VtYWlsYWRkcmVzcyI6IjA5MzcxMTExNTU4QGhtdGVtYWlsLm5leHQiLCJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6ImE3OGRkZjViLTVhMjMtNDVkZC04MDBlLTczNTc3YjBkMzQzOSIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL25hbWUiOiIwOTM3MTExMTU1OCIsIkN1c3RvbWVySWQiOiIxMDA4NCJ9.kXoXA0atw0M64b6m084Gt4hH9MoC9IFFDFwuHOEdazA"
REFERER_URL = "https://naminet.co/"

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
            'User-Agent': 'Mozilla/5.0',
            'Authorization': AUTH_TOKEN,
            'Referer': REFERER_URL
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"   ❌ خطا در درخواست API به {url}: {e}")
        return None

def extract_attributes(short_description):
    attrs = []
    if short_description:
        desc_attrs = re.findall(r"(.+?)\s*:\s*(.+)", short_description)
        for name, value in desc_attrs:
            attrs.append({
                "name": name.strip(),
                "variation": False,
                "visible": True,
                "options": [value.strip()]
            })
    return attrs

def create_or_update_product(wc_data, variations=None):
    sku = wc_data['sku']
    check_url = f"{WC_API_URL}?sku={sku}"
    try:
        r = requests.get(check_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
        r.raise_for_status()
        existing = r.json()
        product_id = None
        if existing:
            product_id = existing[0]['id']
            print(f"   محصول موجود است (ID: {product_id}). آپدیت...")
            update_url = f"{WC_API_URL}/{product_id}"
            requests.put(update_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=wc_data, verify=False)
        else:
            print(f"   محصول جدید است. ایجاد '{wc_data['name']}' ...")
            res = requests.post(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=wc_data, verify=False)
            if res.status_code in [200, 201]:
                product_id = res.json()['id']
                print(f"   ✅ محصول ایجاد شد (ID: {product_id}).")
            else:
                print(f"   ❌ خطا در ایجاد محصول. Status: {res.status_code}, Response: {res.text}")
                return
        # اگر متغیر دارد، وارییشن‌ها را ثبت کن
        if product_id and variations:
            print(f"   ثبت {len(variations)} وارییشن ...")
            variations_url = f"{WC_API_URL}/{product_id}/variations/batch"
            batch_data = {"create": variations}
            res_vars = requests.post(variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=batch_data, verify=False)
            if res_vars.status_code in [200, 201]:
                print(f"   ✅ وارییشن‌ها ثبت شدند.")
            else:
                print(f"   ❌ خطا در ثبت وارییشن‌ها. Status: {res_vars.status_code}, Response: {res_vars.text}")
    except Exception as e:
        print(f"   ❌ خطا در ارتباط با ووکامرس: {e}")

def process_product(product_summary):
    print(f"\n" + "="*50)
    product_name = product_summary.get('name', 'بدون نام')
    product_id = product_summary.get('id')
    print(f"پردازش محصول: {product_name} (ID: {product_id})")

    if not product_id:
        print("   شناسه محصول یافت نشد. نادیده گرفته شد.")
        return

    # دریافت متغیرها (رنگ و ...)
    variations_raw = make_api_request(PRODUCT_ATTR_API_URL.format(product_id=product_id))
    has_variations = variations_raw and isinstance(variations_raw, list) and any(v.get('in_stock') and v.get('price', 0) > 0 for v in variations_raw)

    # اگر متغیر دارد (محصول متغیر)
    if has_variations:
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
            print("   هیچ وارییشن معتبر یافت نشد.")
            return
        wc_attributes = [
            {"name": "رنگ", "variation": True, "visible": True, "options": list(all_colors)}
        ] + extract_attributes(product_summary.get('short_description', ''))
        product_to_send = {
            "name": product_name,
            "type": "variable",
            "sku": f"NAMIN-{product_summary.get('sku', product_id)}",
            "description": product_summary.get('short_description', ''),
            "images": [{"src": img['src']} for img in product_summary.get('images', [])],
            "attributes": wc_attributes,
            "default_attributes": [{"name": "رنگ", "option": list(all_colors)[0]}] if all_colors else [],
        }
        create_or_update_product(product_to_send, variations_data)
    else:
        # محصول ساده
        price = product_summary.get('price') or product_summary.get('final_price_value') or 0
        in_stock = product_summary.get('in_stock', True)
        if not price or price <= 0:
            print("   محصول قیمت ندارد یا ناموجود است. رد شد.")
            return
        wc_attributes = extract_attributes(product_summary.get('short_description', ''))
        product_to_send = {
            "name": product_name,
            "type": "simple",
            "sku": f"NAMIN-{product_summary.get('sku', product_id)}",
            "regular_price": process_price(price),
            "description": product_summary.get('short_description', ''),
            "images": [{"src": img['src']} for img in product_summary.get('images', [])],
            "stock_status": "instock" if in_stock else "outofstock",
            "attributes": wc_attributes
        }
        create_or_update_product(product_to_send)

def main():
    print("شروع انتقال همه محصولات با جزییات کامل به ووکامرس ...")
    api_response = make_api_request(CATEGORY_API_URL)
    if not api_response:
        print("هیچ داده‌ای از API لیست محصولات دریافت نشد.")
        return
    products_list = api_response if isinstance(api_response, list) else api_response.get('products', [])
    if not products_list:
        print("هیچ محصولی در پاسخ API یافت نشد.")
        return
    for product_summary in products_list:
        process_product(product_summary)
        time.sleep(1)
    print("\nتمام محصولات پردازش شدند. فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
