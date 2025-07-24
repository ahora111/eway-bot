import requests
import os
import json
import re

# --- اطلاعات ووکامرس ---
WC_API_URL = os.environ.get("WC_API_URL", "https://your-woocommerce-site.com/wp-json/wc/v3/products")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY", "ck_xxx")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET", "cs_xxx")

# --- خواندن داده محصول از فایل products.json ---
with open("products.json", "r", encoding="utf-8") as f:
    data = json.load(f)
product = data["products"][0]

# --- آماده‌سازی ویژگی رنگ و وارییشن‌ها ---
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
            "sku": f"NAMIN-{product['id']}-{v['id']}",
            "regular_price": str(int(v["price"])),
            "stock_status": "instock" if v["in_stock"] else "outofstock",
            "attributes": [{"name": "Color", "option": v["name"]}]
        })

# --- استخراج سایر ویژگی‌ها از توضیحات کوتاه ---
other_attrs = []
short_description = product.get("short_description", "")
if short_description:
    desc_attrs = re.findall(r"(.+?)\s*:\s*(.+)", short_description)
    for name, value in desc_attrs:
        other_attrs.append({
            "name": name.strip(),
            "variation": False,
            "visible": True,
            "options": [value.strip()]
        })

# --- آماده‌سازی محصول مادر (variable) ---
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

# --- ارسال محصول مادر به ووکامرس ---
r = requests.post(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=wc_data)
if r.status_code not in [200, 201]:
    print("خطا در ایجاد محصول مادر:", r.text)
    exit(1)
parent_id = r.json()["id"]
print(f"✅ محصول مادر ایجاد شد: {parent_id}")

# --- ارسال وارییشن‌ها به ووکامرس ---
if variations:
    variations_url = f"{WC_API_URL}/{parent_id}/variations/batch"
    batch_data = {"create": variations}
    r2 = requests.post(variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=batch_data)
    if r2.status_code in [200, 201]:
        print("✅ وارییشن‌ها ثبت شدند.")
    else:
        print("❌ خطا در ثبت وارییشن‌ها:", r2.text)
else:
    print("هیچ وارییشنی برای این محصول پیدا نشد.")
