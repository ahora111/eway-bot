import requests
import urllib3
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# غیرفعال کردن هشدار SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- اطلاعات ووکامرس (خوانده شده از Secrets گیت‌هاب) ---
WC_API_URL_BASE = os.environ.get("WC_API_URL")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

if not all([WC_API_URL_BASE, WC_CONSUMER_KEY, WC_CONSUMER_SECRET]):
    print("❌ متغیرهای محیطی ووکامرس به درستی تنظیم نشده‌اند. برنامه متوقف می‌شود.")
    exit(1)

WC_PRODUCTS_API_URL = f"{WC_API_URL_BASE}/products"
WC_CAT_API_URL = f"{WC_API_URL_BASE}/products/categories"
# ---------------------------------

# --- اطلاعات API سایت هدف ---
API_BASE_URL = "https://panel.naminet.co/api"
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

def get_all_products(category_id):
    all_products = []
    page = 1
    while True:
        print(f"در حال دریافت صفحه {page} از محصولات دسته‌بندی {category_id}...")
        url = PRODUCTS_LIST_URL_TEMPLATE.format(category_id=category_id)
        params = {'page': page, 'pageSize': 100} # افزایش اندازه صفحه برای کاهش تعداد درخواست‌ها
        data = make_api_request(url, params=params)
        
        if data is None: break
        
        products_in_page = data.get("products", [])
        if not products_in_page:
            print("صفحه آخر دریافت شد.")
            break
        
        all_products.extend(products_in_page)
        
        if len(products_in_page) < 100: break
        page += 1
        time.sleep(0.5)
        
    return all_products

def fetch_variation_data_concurrently(products):
    """
    اطلاعات متغیرها را برای همه محصولات به صورت موازی دریافت می‌کند.
    """
    product_variations = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_product = {
            executor.submit(make_api_request, PRODUCT_ATTRIBUTES_API_URL_TEMPLATE.format(product_id=p['id'])): p['id']
            for p in products if p.get('id')
        }
        
        print(f"\nدر حال دریافت موازی اطلاعات متغیرها برای {len(products)} محصول...")
        for future in as_completed(future_to_product):
            product_id = future_to_product[future]
            try:
                product_variations[product_id] = future.result()
            except Exception as exc:
                print(f'محصول ID {product_id} در گرفتن متغیرها با خطا مواجه شد: {exc}')
    
    return product_variations

def send_batch_to_woocommerce(batch_data, stats):
    """
    محصولات را به صورت دسته‌ای به ووکامرس ارسال می‌کند.
    """
    if not any(batch_data.values()):
        print("هیچ محصولی برای ارسال دسته‌ای وجود ندارد.")
        return

    print(f"\nدر حال ارسال دسته‌ای {len(batch_data.get('create', []))} محصول جدید و {len(batch_data.get('update', []))} محصول برای آپدیت...")
    try:
        res = requests.post(f"{WC_PRODUCTS_API_URL}/batch", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=batch_data, verify=False)
        res.raise_for_status()
        response_data = res.json()
        
        stats['created'] += len(response_data.get('create', []))
        stats['updated'] += len(response_data.get('update', []))
        
        print(f"✅ عملیات دسته‌ای با موفقیت انجام شد.")
        # برگرداندن نقشه SKU به ID برای آپدیت متغیرها
        all_processed = response_data.get('create', []) + response_data.get('update', [])
        return {item['sku']: item['id'] for item in all_processed if 'sku' in item and 'id' in item}
    except Exception as e:
        print(f"   ❌ خطای جدی در ارسال دسته‌ای به ووکامرس: {e}")
        if 'res' in locals():
            print(f"   Response: {res.text}")
        return {}

def process_and_send_variations(wc_product_map, variations_map, stats):
    """
    متغیرها را برای محصولات ایجاد یا آپدیت شده، ارسال می‌کند.
    """
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for sku, product_id in wc_product_map.items():
            if sku in variations_map and variations_map[sku]:
                variations = variations_map[sku]
                print(f"   زمان‌بندی آپدیت {len(variations)} متغیر برای محصول ID: {product_id} (SKU: {sku})")
                
                # برای هر محصول، یک وظیفه جداگانه ارسال می‌کنیم
                variations_url = f"{WC_PRODUCTS_API_URL}/{product_id}/variations/batch"
                batch_data = {"create": variations} # همیشه از create استفاده می‌کنیم چون قبلی‌ها پاک شده‌اند
                
                # ابتدا متغیرهای قدیمی را پاک می‌کنیم
                requests.post(variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json={"delete": [v['id'] for v in requests.get(f"{WC_PRODUCTS_API_URL}/{product_id}/variations", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET)).json()]}, verify=False)

                futures.append(executor.submit(requests.post, variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=batch_data, verify=False))

        for future in as_completed(futures):
            try:
                res = future.result()
                if res.status_code not in [200, 201]:
                    print(f"   ❌ خطا در ثبت دسته متغیرها. Status: {res.status_code}, Response: {res.text}")
            except Exception as e:
                 print(f"   ❌ خطای شبکه در هنگام ثبت متغیرها: {e}")


def main():
    start_time = time.time()
    
    products_summary = get_all_products(1)
    if not products_summary: return
        
    variations_map_by_id = fetch_variation_data_concurrently(products_summary)
    
    batch_to_create = []
    batch_to_update = []
    variations_to_process = {}
    stats = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0}

    # دریافت تمام SKU های موجود در ووکامرس برای مقایسه
    print("\nدر حال دریافت SKU های موجود از ووکامرس...")
    existing_skus = {}
    page = 1
    while True:
        resp = requests.get(WC_PRODUCTS_API_URL, params={'per_page': 100, 'page': page, 'fields': 'id,sku'}, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
        if not resp.ok or not resp.json(): break
        for p in resp.json():
            existing_skus[p['sku']] = p['id']
        if len(resp.json()) < 100: break
        page += 1
    print(f"تعداد {len(existing_skus)} SKU موجود پیدا شد.")

    for product in products_summary:
        if not (product.get('in_stock', True) and product.get('price', 0) > 0):
            stats['skipped'] += 1
            continue
            
        product_id = product.get('id')
        product_name = product.get('name')
        variations_raw = variations_map_by_id.get(product_id)
        
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
        
        sku = f"NAMIN-{product.get('sku', product_id)}"
        
        if variations:
            wc_data = {
                "name": product_name, "type": "variable", "sku": sku,
                "description": product.get('short_description', ''),
                "categories": [{"id": 13}],
                "images": [{"src": img.get("src", "")} for img in product.get("images", [])],
                "attributes": [{"name": "رنگ", "visible": True, "variation": True, "options": sorted(list(color_options))}] + other_attrs,
                "default_attributes": [{"name": "رنگ", "option": sorted(list(color_options))[0]}] if color_options else []
            }
            variations_to_process[sku] = variations
        else:
            wc_data = {
                "name": product_name, "type": "simple", "sku": sku,
                "regular_price": process_price(product.get('price', 0)),
                "description": product.get('short_description', ''),
                "categories": [{"id": 13}],
                "images": [{"src": img.get("src", "")} for img in product.get("images", [])],
                "stock_status": "instock", "attributes": other_attrs
            }
        
        if sku in existing_skus:
            wc_data['id'] = existing_skus[sku]
            batch_to_update.append(wc_data)
        else:
            batch_to_create.append(wc_data)

    # ارسال دسته‌ای محصولات
    wc_product_map = send_batch_to_woocommerce({'create': batch_to_create, 'update': batch_to_update}, stats)
    
    # ارسال متغیرها برای محصولات ایجاد/آپدیت شده
    if wc_product_map:
        process_and_send_variations(wc_product_map, variations_to_process, stats)

    total = len(products_summary)
    end_time = time.time()
    
    print("\n===============================")
    print("📊 آمار نهایی همگام‌سازی:")
    print(f"⏱️ کل زمان اجرا: {end_time - start_time:.2f} ثانیه")
    print(f"📦 تعداد کل محصولات شناسایی شده: {total}")
    print(f"✅ محصولات پردازش شده: {stats['created'] + stats['updated']}")
    print(f"❌ محصولات نادیده گرفته شده: {stats['skipped']}")
    print(f"🟢 محصولات جدید ایجاد شده: {stats['created']}")
    print(f"🔵 محصولات آپدیت شده: {stats['updated']}")
    print("===============================")
    print("تمام محصولات پردازش شدند. فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
