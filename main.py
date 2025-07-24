import requests
import urllib3
import os
import re
import time
import json

# غیرفعال کردن هشدار SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- اطلاعات ووکامرس (خوانده شده از Secrets گیت‌هاب) ---
WC_API_URL_BASE = os.environ.get("WC_API_URL", "https://your-site.com/wp-json/wc/v3")
WC_API_URL = f"{WC_API_URL_BASE}/products"
WC_CAT_API_URL = f"{WC_API_URL_BASE}/products/categories"
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET]):
    print("❌ متغیرهای محیطی ووکامرس به درستی تنظیم نشده‌اند. برنامه متوقف می‌شود.")
    exit(1)
# ---------------------------------

# --- اطلاعات API سایت هدف ---
API_BASE_URL = "https://panel.naminet.co/api"
CATEGORIES_API_URL = f"{API_BASE_URL}/categories/"
PRODUCTS_LIST_URL_TEMPLATE = f"{API_BASE_URL}/categories/{{category_id}}/products/"
PRODUCT_ATTRIBUTES_API_URL_TEMPLATE = f"{API_BASE_URL}/products/attr/{{product_id}}"
AUTH_TOKEN = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJuYmYiOiIxNzUyMjUyMTE2IiwiZXhwIjoiMTc2MDAzMTcxNiIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL2VtYWlsYWRkcmVzcyI6IjA5MzcxMTExNTU4QGhtdGVtYWlsLm5leHQiLCJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6ImE3OGRkZjViLTVhMjMtNDVkZC04MDBlLTczNTc3YjBkMzQzOSIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL25hbWUiOiIwOTM3MTExMTU1OCIsIkN1c3RvbWVySWQiOiIxMDA4NCJ9.kXoXA0atw0M64b6m084Gt4hH9MoC9IFFDFwuHOEdazA"
REFERER_URL = "https://naminet.co/"
# ---------------------------------------------

def make_api_request(url, params=None):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
            'Authorization': AUTH_TOKEN,
            'Referer': REFERER_URL
        }
        response = requests.get(url, headers=headers, params=params, timeout=30, verify=False)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"   ❌ خطا در درخواست API به {url}: {e}")
        return None

def process_price(price_value):
    # ... (بدون تغییر)
    try:
        price_value = float(price_value)
    except (ValueError, TypeError): return "0"
    if price_value <= 1: return "0"
    elif price_value <= 7000000: new_price = price_value + 260000
    elif price_value <= 10000000: new_price = price_value * 1.035
    elif price_value <= 20000000: new_price = price_value * 1.025
    elif price_value <= 30000000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price / 10000) * 10000))

def parse_attributes_from_description(description):
    # ... (بدون تغییر)
    attrs = []
    if description:
        lines = description.split('\r\n')
        for line in lines:
            if ':' in line:
                parts = line.split(':', 1)
                name, value = parts[0].strip(), parts[1].strip()
                if name and value:
                    attrs.append({"name": name, "visible": True, "options": [value]})
    return attrs

def sync_categories():
    print("="*20 + " فاز ۰: شروع همگام‌سازی دسته‌بندی‌ها " + "="*20)
    stats = {'created': 0, 'existing': 0, 'failed': 0}
    
    # 1. گرفتن دسته‌بندی‌ها از سایت منبع
    source_cats_raw = make_api_request(CATEGORIES_API_URL)
    if not source_cats_raw:
        print("دریافت دسته‌بندی‌ها از سایت منبع ناموفق بود.")
        return None
    
    source_cats = {cat['id']: cat for cat in source_cats_raw}

    # 2. گرفتن دسته‌بندی‌های موجود در ووکامرس
    wc_cats_map = {}
    page = 1
    while True:
        resp = requests.get(f"{WC_CAT_API_URL}?per_page=100&page={page}", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
        if not resp.ok or not resp.json():
            break
        for cat in resp.json():
            wc_cats_map[cat['name']] = cat['id']
        page += 1
    
    # 3. ایجاد دسته‌بندی‌های جدید در صورت نیاز
    for cat_id, cat_data in source_cats.items():
        cat_name = cat_data.get('name')
        if not cat_name:
            continue
            
        if cat_name not in wc_cats_map:
            print(f"   ایجاد دسته‌بندی جدید: '{cat_name}'")
            new_cat_data = {
                "name": cat_name,
                "slug": cat_data.get('se_name', cat_name)
            }
            # اگر دسته‌بندی والد داشت، آن را هم تنظیم می‌کنیم
            parent_id = cat_data.get('parent_category_id', 0)
            if parent_id in source_cats:
                 parent_name = source_cats[parent_id].get('name')
                 if parent_name in wc_cats_map:
                     new_cat_data['parent'] = wc_cats_map[parent_name]

            res = requests.post(WC_CAT_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=new_cat_data, verify=False)
            if res.status_code == 201:
                stats['created'] += 1
                wc_cats_map[cat_name] = res.json()['id'] # به لیست اضافه می‌کنیم تا برای والدها استفاده شود
            else:
                stats['failed'] += 1
                print(f"   ❌ خطا در ایجاد دسته‌بندی '{cat_name}'. Status: {res.status_code}, Response: {res.text}")
        else:
            stats['existing'] += 1
    
    print("\n===============================")
    print("📊 آمار همگام‌سازی دسته‌بندی‌ها:")
    print(f"   - 🟢 دسته‌بندی‌های جدید ایجاد شده: {stats['created']}")
    print(f"   - 🔵 دسته‌بندی‌های موجود: {stats['existing']}")
    print(f"   - 🔴 خطا در ایجاد: {stats['failed']}")
    print("===============================\n")

    # یک نقشه از ID منبع به ID ووکامرس برمی‌گردانیم
    source_to_wc_id_map = {}
    for source_id, source_data in source_cats.items():
        if source_data['name'] in wc_cats_map:
            source_to_wc_id_map[source_id] = wc_cats_map[source_data['name']]
            
    return source_to_wc_id_map

def _send_to_woocommerce(sku, data, stats):
    # ... (این تابع بدون تغییر باقی می‌ماند، فقط stats را می‌پذیرد)
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
                stats['updated'] += 1
                print(f"   ✅ محصول '{data['name']}' آپدیت شد.")
            else:
                stats['failed'] += 1
                print(f"   ❌ خطا در آپدیت. Status: {res.status_code}, Response: {res.text}")
        else:
            print(f"   محصول جدید است. در حال ایجاد '{data['name']}' ...")
            res = requests.post(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
            if res.status_code == 201:
                product_id = res.json()['id']
                stats['created'] += 1
                print(f"   ✅ محصول ایجاد شد (ID: {product_id}).")
            else:
                stats['failed'] += 1
                print(f"   ❌ خطا در ایجاد محصول. Status: {res.status_code}, Response: {res.text}")
        
        return product_id
    except Exception as e:
        stats['failed'] += 1
        print(f"   ❌ خطای کلی در ارتباط با ووکامرس: {e}")
        return None

def create_or_update_variations(product_id, variations):
    # ... (بدون تغییر)
    if not product_id or not variations: return
        
    print(f"   در حال ثبت {len(variations)} متغیر برای محصول ID: {product_id}...")
    variations_url = f"{WC_API_URL}/{product_id}/variations/batch"
    
    existing_vars_resp = requests.get(f"{WC_API_URL}/{product_id}/variations?per_page=100", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
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

def process_product(product, cat_map, stats):
    print("\n" + "="*50)
    product_name = product.get('name', 'بدون نام')
    product_id = product.get('id', '')
    print(f"پردازش: {product_name} (ID: {product_id})")

    variations_raw = make_api_request(PRODUCT_ATTRIBUTES_API_URL_TEMPLATE.format(product_id=product_id))
    
    variations = []
    color_options = set()
    
    if variations_raw and isinstance(variations_raw, list):
        for var in variations_raw:
            if var.get("in_stock") and var.get("price", 0) > 0:
                color_name = var.get("name", "").strip()
                if not color_name: continue
                color_options.add(color_name)
                variations.append({
                    "sku": f"NAMIN-{product_id}-{var.get('id', '')}",
                    "regular_price": process_price(var.get("price", 0)),
                    "stock_status": "instock",
                    "attributes": [{"name": "رنگ", "option": color_name}]
                })

    other_attrs = parse_attributes_from_description(product.get('short_description', ''))
    
    # تبدیل ID دسته‌بندی منبع به ID ووکامرس
    wc_categories = [{"id": cat_map[cid]} for cid in product.get('category_ids', []) if cid in cat_map]
    if not wc_categories:
        print("   هشدار: هیچ دسته‌بندی معتبری برای این محصول یافت نشد.")

    if variations:
        wc_data = {
            "name": product_name,
            "type": "variable",
            "sku": f"NAMIN-{product.get('sku', product_id)}",
            "description": product.get('short_description', ''),
            "categories": wc_categories,
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
        product_wc_id = _send_to_woocommerce(wc_data['sku'], wc_data, stats)
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
                "categories": wc_categories,
                "images": [{"src": img.get("src", "")} for img in product.get("images", [])],
                "stock_status": "instock",
                "attributes": other_attrs
            }
            _send_to_woocommerce(wc_data['sku'], wc_data, stats)
        else:
            stats['skipped'] += 1
            print("   محصول ساده قیمت ندارد یا ناموجود است. نادیده گرفته شد.")

def get_all_products(category_id):
    all_products = []
    page = 1
    while True:
        print(f"در حال دریافت صفحه {page} از محصولات دسته‌بندی {category_id}...")
        url = PRODUCTS_LIST_URL_TEMPLATE.format(category_id=category_id)
        params = {'page': page, 'pageSize': 50}
        data = make_api_request(url, params=params)
        
        if data is None: break
        
        products_in_page = data.get("products", [])
        if not products_in_page:
            print("صفحه آخر دریافت شد.")
            break
        
        all_products.extend(products_in_page)
        print(f"تعداد {len(products_in_page)} محصول از صفحه {page} دریافت شد.")
        
        if len(products_in_page) < 50: break
        page += 1
        time.sleep(1)
        
    print(f"\nدریافت اطلاعات از API کامل شد. کل محصولات دریافت شده: {len(all_products)}")
    return all_products

def main():
    # فاز ۰: همگام‌سازی دسته‌بندی‌ها
    cat_map = sync_categories()
    if cat_map is None:
        print("ادامه فرآیند به دلیل خطای همگام‌سازی دسته‌بندی‌ها ممکن نیست.")
        return

    # فاز ۱: دریافت تمام محصولات از تمام دسته‌بندی‌ها
    # برای سادگی فعلا فقط دسته‌بندی اصلی (موبایل) را می‌گیریم
    products = get_all_products(1) # ID 1 برای دسته‌بندی اصلی موبایل است
    if not products:
        print("هیچ محصولی برای پردازش یافت نشد. برنامه خاتمه می‌یابد.")
        return
        
    stats = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0}
    
    # فاز ۲: پردازش محصولات
    for product in products:
        try:
            if product.get('in_stock', True) and product.get('price', 0) > 0:
                process_product(product, cat_map, stats)
            else:
                stats['skipped'] += 1
                print("\n" + "="*50)
                print(f"نادیده گرفتن گروه محصول ناموجود/بدون قیمت: {product.get('name')}")
        except Exception as e:
            stats['failed'] += 1
            print(f"   ❌ خطا در پردازش محصول {product.get('id', '')}: {e}")
        time.sleep(1.5)
        
    print("\n===============================")
    print("📊 آمار نهایی همگام‌سازی محصولات:")
    print(f"📦 تعداد کل گروه‌های محصول شناسایی شده: {len(products)}")
    print(f"✅ محصولات پردازش شده: {stats['created'] + stats['updated']}")
    print(f"❌ محصولات نادیده گرفته شده: {stats['skipped']}")
    print(f"🟢 محصولات جدید ایجاد شده: {stats['created']}")
    print(f"🔵 محصولات آپدیت شده: {stats['updated']}")
    print(f"🔴 خطا در پردازش: {stats['failed']}")
    print("===============================")
    print("تمام محصولات پردازش شدند. فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
