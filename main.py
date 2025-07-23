import json
import requests
import os

# --- اطلاعات ووکامرس (از محیط یا مستقیم) ---
WC_API_URL = os.environ.get("WC_API_URL", "https://your-woocommerce-site.com/wp-json/wc/v3/products")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY", "ck_xxx")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET", "cs_xxx")

# --- خواندن فایل JSON محصولات ---
with open("products.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# اگر داده فقط لیست محصولات است:
products = data["products"] if "products" in data else data

def product_to_woocommerce(p):
    # آماده‌سازی داده برای ووکامرس
    images = [{"src": img["src"]} for img in p.get("images", [])]
    categories = [{"id": cid} for cid in p.get("category_ids", [])]
    data = {
        "name": p.get("name", ""),
        "sku": p.get("sku", ""),
        "type": "simple",
        "regular_price": str(p.get("final_price_value", "")),
        "description": p.get("short_description", ""),
        "manage_stock": True,
        "stock_quantity": 10 if p.get("in_stock", False) else 0,
        "stock_status": "instock" if p.get("in_stock", False) else "outofstock",
        "images": images,
        "categories": categories,
    }
    return data

def create_or_update_product(wc_data):
    # چک کردن وجود محصول با SKU
    check_url = f"{WC_API_URL}?sku={wc_data['sku']}"
    r = requests.get(check_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET))
    if r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) > 0:
        # محصول وجود دارد، آپدیت کن
        product_id = r.json()[0]['id']
        update_url = f"{WC_API_URL}/{product_id}"
        r2 = requests.put(update_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=wc_data)
        if r2.status_code == 200:
            print(f"✅ محصول '{wc_data['name']}' آپدیت شد.")
        else:
            print(f"❌ خطا در آپدیت '{wc_data['name']}':", r2.text)
    else:
        # محصول جدید است، ایجاد کن
        r2 = requests.post(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=wc_data)
        if r2.status_code in [200, 201]:
            print(f"✅ محصول '{wc_data['name']}' ایجاد شد.")
        else:
            print(f"❌ خطا در ایجاد '{wc_data['name']}':", r2.text)

# --- پردازش و ارسال محصولات ---
for p in products:
    if not p.get("in_stock", False):
        print(f"⏩ محصول '{p.get('name', '')}' ناموجود است، رد شد.")
        continue
    wc_data = product_to_woocommerce(p)
    create_or_update_product(wc_data)
