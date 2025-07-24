import requests
import os
import json
import re

# --- اطلاعات ووکامرس ---
WC_API_URL = os.environ.get("WC_API_URL", "https://your-woocommerce-site.com/wp-json/wc/v3/products")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY", "ck_xxx")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET", "cs_xxx")

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
            res = requests.put(update_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=wc_data)
            if res.status_code in [200, 201]:
                print(f"   ✅ محصول آپدیت شد (ID: {product_id}).")
            else:
                print(f"   ❌ خطا در آپدیت محصول. Status: {res.status_code}, Response: {res.text}")
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

def main():
    # --- خواندن داده محصول از فایل products.json ---
    with open("products.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    products = data["products"] if "products" in data else data

    for product in products:
        print(f"\n" + "="*50)
        product_name = product.get('name', 'بدون نام')
        product_id = product.get('id', product.get('sku', ''))
        print(f"پردازش محصول: {product_name} (ID: {product_id})")

        # آماده‌سازی ویژگی رنگ و وارییشن‌ها
        attribute = None
        for attr in product.get("attributes", []):
            if attr.get("product_attribute_name") == "رنگ":
                attribute = attr
                break

        color_options = []
        variations = []
        if attribute:
            for v in attribute.get("attribute_values", []):
                color_options.append(v["name"])
                variations.append({
                    "sku": f"NAMIN-{product_id}-{v['id']}",
                    "regular_price": str(int(v["price"])),
                    "stock_status": "instock" if v["in_stock"] else "outofstock",
                    "attributes": [{"name": "Color", "option": v["name"]}]
                })

        # استخراج سایر ویژگی‌ها از توضیحات کوتاه
        other_attrs = extract_attributes(product.get("short_description", ""))

        # آماده‌سازی محصول مادر (variable)
        wc_data = {
            "name": product["name"],
            "type": "variable",
            "sku": f"NAMIN-{product['sku']}",
            "description": product.get("short_description", ""),
            "categories": [{"id": cid} for cid in product.get("category_ids", [])],
            "images": [{"src": img["src"]} for img in product.get("images", [])],
            "attributes": [
                {
                    "id": 1,  # آیدی ویژگی global رنگ در ووکامرس (در پنل ویژگی‌ها ببین)
                    "name": "Color",
                    "slug": "pa_color",
                    "visible": True,
                    "variation": True,
                    "options": color_options
                }
            ] + other_attrs
        }

        create_or_update_product(wc_data, variations)

if __name__ == "__main__":
    main()
