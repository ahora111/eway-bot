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

# --- اطلاعات API سایت هدف ---
API_URL = "https://panel.naminet.co/api/categories/13/products/"
AUTH_TOKEN = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJuYmYiOiIxNzUyMjUyMTE2IiwiZXhwIjoiMTc2MDAzMTcxNiIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL2VtYWlsYWRkcmVzcyI6IjA5MzcxMTExNTU4QGhtdGVtYWlsLm5leHQiLCJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6ImE3OGRkZjViLTVhMjMtNDVkZC04MDBlLTczNTc3YjBkMzQzOSIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL25hbWUiOiIwOTM3MTExMTU1OCIsIkN1c3RvbWVySWQiOiIxMDA4NCJ9.kXoXA0atw0M64b6m084Gt4hH9MoC9IFFDFwuHOEdazA"
REFERER_URL = "https://naminet.co/"
# ---------------------------------------------

def make_api_request(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
            'Authorization': AUTH_TOKEN,
            'Referer': REFERER_URL
        }
        response = requests.get(url, headers=headers, timeout=30, verify=False)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"   ❌ خطا در درخواست API به {url}: {e}")
        return None
    except ValueError:
        print(f"   ❌ پاسخ JSON نامعتبر از {url}")
        return None

def process_price(price_value):
    try:
        price_value = float(price_value)
    except (ValueError, TypeError):
        return "0"
    if price_value <= 1: return "0"
    elif price_value <= 7000000: new_price = price_value + 260000
    elif price_value <= 10000000: new_price = price_value * 1.035
    elif price_value <= 20000000: new_price = price_value * 1.025
    elif price_value <= 30000000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price / 10000) * 10000))

def extract_attributes(short_description):
    attrs = []
    if short_description:
        lines = short_description.split('\r\n')
        for line in lines:
            if ':' in line:
                parts = line.split(':', 1)
                name, value = parts[0].strip(), parts[1].strip()
                if name and value:
                    attrs.append({
                        "name": name,
                        "variation": False,
                        "visible": True,
                        "options": [value]
                    })
    return attrs

def _send_to_woocommerce(sku, data):
    check_url = f"{WC_API_URL}?sku={sku}"
    try:
        r = requests.get(check_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
        r.raise_for_status()
        existing = r.json()
        
        product_id = None
        if existing:
            product_id = existing[0]['id']
            print(f"   محصول موجود است (ID: {product_id}). در حال آپدیت...")
            update_url = f"{WC_API_URL}/{product_id}"
            res = requests.put(update_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
            if res.status_code == 200:
                print(f"   ✅ محصول '{data['name']}' آپدیت شد.")
            else:
                print(f"   ❌ خطا در آپدیت. Status: {res.status_code}, Response: {res.text}")
        else:
            print(f"   محصول جدید است. در حال ایجاد '{data['name']}' ...")
            res = requests.post(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
            if res.status_code == 201:
                product_id = res.json()['id']
                print(f"   ✅ محصول ایجاد شد (ID: {product_id}).")
            else:
                print(f"   ❌ خطا در ایجاد محصول. Status: {res.status_code}, Response: {res.text}")
        
        return product_id
    except Exception as e:
        print(f"   ❌ خطای کلی در ارتباط با ووکامرس: {e}")
        return None

def create_or_update_variations(product_id, variations):
    if not product_id or not variations:
        return
        
    print(f"   در حال ثبت {len(variations)} متغیر برای محصول ID: {product_id}...")
    variations_url = f"{WC_API_URL}/{product_id}/variations/batch"
    
    existing_vars_resp = requests.get(f"{WC_API_URL}/{product_id}/variations", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
    if existing_vars_resp.status_code == 200 and existing_vars_resp.json():
        delete_ids = [v['id'] for v in existing_vars_resp.json()]
        if delete_ids:
            print(f"   در حال پاک کردن {len(delete_ids)} متغیر قدیمی...")
            requests.post(variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json={"delete": delete_ids}, verify=False)
    
    for i in range(0, len(variations), 10):
        batch = variations[i:i + 10]
        batch_data = {"create": batch}
        res_vars = requests.post(variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=batch_data, verify=False)
        if res_vars.status_code not in [200, 201]:
            print(f"   ❌ خطا در ثبت دسته متغیرها. Status: {res_vars.status_code}, Response: {res_vars.text}")
            break
    else:
        print(f"   ✅ متغیرها با موفقیت ثبت شدند.")

def process_product(product):
    print("\n" + "="*50)
    product_name = product.get('name', 'بدون نام')
    product_id = product.get('id', '')
    print(f"پردازش: {product_name} (ID: {product_id})")

    variations = []
    color_options = set()
    
    if "attributes" in product and product["attributes"]:
        for attr in product["attributes"]:
            if attr.get("product_attribute_name") == "رنگ" and attr.get("attribute_values"):
                for v in attr["attribute_values"]:
                    if v.get("in_stock") and v.get("price", 0) > 0:
                        color_name = v.get("name", "").strip()
                        if not color_name: continue
                        color_options.add(color_name)
                        variations.append({
                            "sku": f"NAMIN-{product_id}-{v.get('id', '')}",
                            "regular_price": process_price(v.get("price", 0)),
                            "stock_status": "instock",
                            "attributes": [{"name": "رنگ", "option": color_name}]
                        })

    other_attrs = extract_attributes(product.get('short_description', ''))

    if variations:
        wc_data = {
            "name": product_name,
            "type": "variable",
            "sku": f"NAMIN-{product.get('sku', product_id)}",
            "description": product.get('short_description', ''),
            "categories": [{"id": 13}],
            "images": [{"src": img.get("src", "")} for img in product.get("images", [])],
            "attributes": [
                {
                    "name": "رنگ",
                    "visible": True,
                    "variation": True,
                    "options": sorted(list(color_options))
                }
            ] + other_attrs,
            "default_attributes": [{"name": "رنگ", "option": sorted(list(color_options))[0]}] if color_options else []
        }
        product_wc_id = _send_to_woocommerce(wc_data['sku'], wc_data)
        create_or_update_variations(product_wc_id, variations)
    else:
        price = product.get('price') or product.get('final_price_value') or 0
        if price > 0 and product.get('in_stock', True):
            wc_data = {
                "name": product_name,
                "type": "simple",
                "sku": f"NAMIN-{product.get('sku', product_id)}",
                "regular_price": process_price(price),
                "description": product.get('short_description', ''),
                "categories": [{"id": 13}],
                "images": [{"src": img.get("src", "")} for img in product.get("images", [])],
                "stock_status": "instock",
                "attributes": other_attrs
            }
            _send_to_woocommerce(wc_data['sku'], wc_data)
        else:
            print("   محصول ساده قیمت ندارد یا ناموجود است. نادیده گرفته شد.")

def main():
    # --- اصلاح کلیدی: حذف کامل Paging ---
    print("شروع فرآیند با استراتژی نهایی (API مستقیم)...")
    api_response = make_api_request(API_URL)
    
    if not api_response:
        print("هیچ داده‌ای از API دریافت نشد. برنامه خاتمه می‌یابد.")
        return
        
    products = api_response.get("products", [])
    if not products:
        print("هیچ محصولی در پاسخ API یافت نشد. برنامه خاتمه می‌یابد.")
        return
        
    total = len(products)
    available = sum(1 for p in products if p.get('in_stock', True) and p.get('price', 0) > 0)
    
    print(f"\n🔎 تعداد کل گروه‌های محصول شناسایی شده: {total}")
    print(f"✅ گروه‌های محصول موجود و با قیمت: {available}\n")
    
    for product in products:
        try:
            process_product(product)
        except Exception as e:
            print(f"   ❌ خطا در پردازش محصول: {e}")
        time.sleep(1)
        
    print("\nتمام محصولات پردازش شدند. فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
