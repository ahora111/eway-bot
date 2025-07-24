import requests
import os
import re
import time

# --- اطلاعات ووکامرس ---
WC_API_URL = os.environ.get("WC_API_URL", "https://your-woocommerce-site.com/wp-json/wc/v3/products")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY", "ck_xxx")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET", "cs_xxx")

# --- اطلاعات API سایت هدف ---
API_BASE_URL = "https://panel.naminet.co/api"
CATEGORY_ID = 13
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
    try:
        price_value = float(price_value)
    except:
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

    is_variable = False
    variations = []
    color_options = []
    # استخراج ویژگی رنگ و وارییشن‌ها
    if "attributes" in product and product["attributes"]:
        for attr in product["attributes"]:
            if attr.get("product_attribute_name") == "رنگ" and attr.get("attribute_values"):
                is_variable = True
                for v in attr["attribute_values"]:
                    color_options.append(v.get("name", ""))
                    variations.append({
                        "sku": f"NAMIN-{product_id}-{v.get('id', '')}",
                        "regular_price": process_price(v.get("price", 0)),
                        "stock_status": "instock" if v.get("in_stock", True) else "outofstock",
                        "attributes": [{"name": "Color", "option": v.get("name", "")}]
                    })

    # استخراج سایر ویژگی‌ها از توضیحات کوتاه
    other_attrs = extract_attributes(product.get('short_description', ''))

    if is_variable and variations:
        wc_data = {
            "name": product_name,
            "type": "variable",
            "sku": f"NAMIN-{product.get('sku', product_id)}",
            "description": product.get('short_description', ''),
            "categories": [{"id": cid} for cid in product.get("category_ids", []) if cid],
            "images": [{"src": img.get("src", "")} for img in product.get("images", []) if img.get("src")],
            "attributes": [
                {
                    "name": "Color",
                    "slug": "pa_color",
                    "visible": True,
                    "variation": True,
                    "options": color_options
                }
            ] + other_attrs
        }
        create_or_update_product(wc_data, variations)
    else:
        price = product.get('price') or product.get('final_price_value') or 0
        in_stock = product.get('in_stock', True)
        wc_data = {
            "name": product_name,
            "type": "simple",
            "sku": f"NAMIN-{product.get('sku', product_id)}",
            "regular_price": process_price(price),
            "description": product.get('short_description', ''),
            "categories": [{"id": cid} for cid in product.get("category_ids", []) if cid],
            "images": [{"src": img.get("src", "")} for img in product.get("images", []) if img.get("src")],
            "stock_status": "instock" if in_stock else "outofstock",
            "attributes": other_attrs
        }
        create_or_update_product(wc_data)

def get_all_products():
    all_products = []
    page = 1
    while True:
        url = f"{API_BASE_URL}/categories/{CATEGORY_ID}/products/?page={page}&pageSize=50"
        data = make_api_request(url)
        if not data:
            break
        products = data if isinstance(data, list) else data.get("products", [])
        if not products:
            break
        all_products.extend(products)
        print(f"صفحه {page}، تعداد محصولات این صفحه: {len(products)}")
        if len(products) < 50:
            break
        page += 1
    print(f"\nکل محصولات دریافت شده: {len(all_products)}")
    return all_products

def main():
    products = get_all_products()
    total = len(products)
    available = sum(1 for p in products if p.get('in_stock', True))
    unavailable = total - available
    print(f"\n🔎 تعداد کل محصولات شناسایی شده: {total}")
    print(f"✅ محصولات موجود: {available}")
    print(f"❌ محصولات ناموجود: {unavailable}\n")
    for product in products:
        try:
            process_product(product)
        except Exception as e:
            print(f"   ❌ خطا در پردازش محصول: {e}")
        time.sleep(1)
    print("\nتمام محصولات پردازش شدند. فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
