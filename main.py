import requests
import os
import re
import time

# --- اطلاعات ووکامرس ---
WC_API_URL = os.environ.get("WC_API_URL")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET]):
    print("❌ متغیرهای محیطی ووکامرس به درستی تنظیم نشده‌اند.")
    exit(1)

# --- اطلاعات API سایت هدف ---
API_BASE_URL = "https://panel.naminet.co/api"
SAMSUNG_CATEGORY_API_URL = f"{API_BASE_URL}/categories/13/products/"
AUTH_TOKEN = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJuYmYiOiIxNzUyMjUyMTE2IiwiZXhwIjoiMTc2MDAzMTcxNiIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL2VtYWlsYWRkcmVzcyI6IjA5MzcxMTExNTU4QGhtdGVtYWlsLm5leHQiLCJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6ImE3OGRkZjViLTVhMjMtNDVkZC04MDBlLTczNTc3YjBkMzQzOSIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL25hbWUiOiIwOTM3MTExMTU1OCIsIkN1c3RvbWVySWQiOiIxMDA4NCJ9.kXoXA0atw0M64b6m084Gt4hH9MoC9IFFDFwuHOEdazA"
REFERER_URL = "https://naminet.co/"

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

def process_price(price_value):
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
        # ویژگی‌های مهم را جدا کن و به صورت attribute بفرست
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
        r = requests.get(check_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET))
        r.raise_for_status()
        existing = r.json()
        product_id = None
        if existing:
            product_id = existing[0]['id']
            print(f"   محصول موجود است (ID: {product_id}). آپدیت...")
            update_url = f"{WC_API_URL}/{product_id}"
            requests.put(update_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=wc_data)
        else:
            print(f"   محصول جدید است. ایجاد '{wc_data['name']}' ...")
            res = requests.post(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=wc_data)
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
            res_vars = requests.post(variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=batch_data)
            if res_vars.status_code in [200, 201]:
                print(f"   ✅ وارییشن‌ها ثبت شدند.")
            else:
                print(f"   ❌ خطا در ثبت وارییشن‌ها. Status: {res_vars.status_code}, Response: {res_vars.text}")
    except Exception as e:
        print(f"   ❌ خطا در ارتباط با ووکامرس: {e}")

def process_product(product):
    print(f"\n" + "="*50)
    product_name = product.get('name', 'بدون نام')
    product_id = product.get('id', product.get('sku', ''))
    print(f"پردازش محصول: {product_name} (ID: {product_id})")

    # فقط محصولات موجود را منتقل کن (پیشنهاد 2)
    if not product.get('in_stock', True):
        print("   محصول ناموجود است. رد شد.")
        return

    # اگر ویژگی رنگ و attribute_values دارد، محصول متغیر است
    is_variable = False
    variations = []
    color_options = []
    if "attributes" in product and product["attributes"]:
        for attr in product["attributes"]:
            if attr.get("product_attribute_name") == "رنگ" and attr.get("attribute_values"):
                is_variable = True
                for v in attr["attribute_values"]:
                    color_options.append(v["name"])
                    variations.append({
                        "sku": f"NAMIN-{product_id}-{v['id']}",
                        "regular_price": process_price(v["price"]),
                        "stock_status": "instock" if v["in_stock"] else "outofstock",
                        "attributes": [{"name": "رنگ", "option": v["name"]}]
                    })

    # محصول متغیر
    if is_variable and variations:
        wc_data = {
            "name": product_name,
            "type": "variable",
            "sku": f"NAMIN-{product.get('sku', product_id)}",
            "description": product.get('short_description', ''),
            "categories": [{"id": cid} for cid in product.get("category_ids", [])],
            "images": [{"src": img["src"]} for img in product.get("images", [])],
            "attributes": [{
                "name": "رنگ",
                "slug": "color",
                "visible": True,
                "variation": True,
                "options": color_options
            }] + extract_attributes(product.get('short_description', ''))
        }
        create_or_update_product(wc_data, variations)
    else:
        # محصول ساده
        price = product.get('price') or product.get('final_price_value') or 0
        in_stock = product.get('in_stock', True)
        if not price or price <= 0:
            print("   محصول قیمت ندارد یا ناموجود است. رد شد.")
            return
        wc_data = {
            "name": product_name,
            "type": "simple",
            "sku": f"NAMIN-{product.get('sku', product_id)}",
            "regular_price": process_price(price),
            "description": product.get('short_description', ''),
            "categories": [{"id": cid} for cid in product.get("category_ids", [])],
            "images": [{"src": img["src"]} for img in product.get("images", [])],
            "stock_status": "instock" if in_stock else "outofstock",
            "attributes": extract_attributes(product.get('short_description', ''))
        }
        create_or_update_product(wc_data)

def main():
    # خواندن محصولات از فایل جیسون
    import json
    with open("products.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    products = data["products"] if "products" in data else data

    for product in products:
        process_product(product)
        time.sleep(1)
    print("\nتمام محصولات پردازش شدند. فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
