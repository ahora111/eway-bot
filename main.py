import requests
import urllib3
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# غیرفعال کردن هشدار SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- اطلاعات ووکامرس (خوانده شده از Secrets گیت‌هاب) ---
WC_API_URL_BASE = os.environ.get("WC_API_URL")
WC_PRODUCTS_API_URL = f"{WC_API_URL_BASE}/products"
WC_CAT_API_URL = f"{WC_API_URL_BASE}/products/categories"
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

if not all([WC_API_URL_BASE, WC_CONSUMER_KEY, WC_CONSUMER_SECRET]):
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
            'Authorization': AUTH_TOKEN, 'Referer': REFERER_URL
        }
        response = requests.get(url, headers=headers, params=params, timeout=30, verify=False)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {'error': f"Error fetching {url}: {e}"}

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

def sync_categories():
    print("="*20 + " فاز ۰: شروع همگام‌سازی دسته‌بندی‌ها " + "="*20)
    stats = {'created': 0, 'existing': 0, 'failed': 0}
    
    api_response = make_api_request(CATEGORIES_API_URL)
    if not api_response or 'error' in api_response:
        print("دریافت دسته‌بندی‌ها از سایت منبع ناموفق بود.")
        return None, []
    
    source_cats_raw = api_response.get('mega_menu', [])
    
    all_source_cats = []
    def flatten_cats(categories, parent_id=0):
        for cat in categories:
            cat['parent_id'] = parent_id
            all_source_cats.append(cat)
            if 'childs' in cat and cat['childs']:
                flatten_cats(cat['childs'], cat['id'])
    flatten_cats(source_cats_raw)

    source_cats_map = {cat['id']: cat for cat in all_source_cats}

    wc_cats_map = {cat['name']: cat['id'] for cat in requests.get(f"{WC_CAT_API_URL}?per_page=100", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False).json()}

    for cat_data in all_source_cats:
        cat_name = cat_data.get('name')
        if not cat_name or cat_name in wc_cats_map:
            if cat_name in wc_cats_map: stats['existing'] += 1
            continue
            
        print(f"   ایجاد دسته‌بندی جدید: '{cat_name}'")
        new_cat_data = {"name": cat_name, "slug": cat_data.get('se_name')}
        parent_id = cat_data.get('parent_id', 0)
        if parent_id in source_cats_map:
             parent_name = source_cats_map[parent_id].get('name')
             if parent_name in wc_cats_map:
                 new_cat_data['parent'] = wc_cats_map[parent_name]

        res = requests.post(WC_CAT_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=new_cat_data, verify=False)
        if res.status_code == 201:
            stats['created'] += 1
            wc_cats_map[cat_name] = res.json()['id']
        else:
            stats['failed'] += 1
    
    print("\n===============================")
    print("📊 آمار همگام‌سازی دسته‌بندی‌ها:")
    print(f"   - 🟢 دسته‌بندی‌های جدید ایجاد شده: {stats['created']}")
    print(f"   - 🔵 دسته‌بندی‌های موجود: {stats['existing']}")
    print(f"   - 🔴 خطا در ایجاد: {stats['failed']}")
    print("===============================\n")

    source_to_wc_id_map = {src_id: wc_cats_map[src_data['name']] for src_id, src_data in source_cats_map.items() if src_data.get('name') in wc_cats_map}
    return source_to_wc_id_map, [c['id'] for c in all_source_cats]

def get_all_products_from_category(category_id):
    all_products = []
    page = 1
    while True:
        url = PRODUCTS_LIST_URL_TEMPLATE.format(category_id=category_id)
        params = {'page': page, 'pageSize': 100}
        data = make_api_request(url, params=params)
        
        if not data or 'error' in data: break
        products_in_page = data.get("products", [])
        if not products_in_page: break
        
        all_products.extend(products_in_page)
        if len(products_in_page) < 100: break
        page += 1
        time.sleep(0.5)
    return all_products

def fetch_variation_data_concurrently(products):
    product_variations = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_product = {
            executor.submit(make_api_request, PRODUCT_ATTRIBUTES_API_URL_TEMPLATE.format(product_id=p['id'])): p['id']
            for p in products if p.get('id')
        }
        
        for future in tqdm(as_completed(future_to_product), total=len(products), desc="Fetching Variations", unit="product"):
            product_id = future_to_product[future]
            result = future.result()
            if result and 'error' not in result:
                product_variations[product_id] = result
    
    return product_variations

def send_batch_to_woocommerce(batch_data, stats):
    if not any(batch_data.values()): return {}
    create_count, update_count = len(batch_data.get('create', [])), len(batch_data.get('update', []))
    print(f"\nدر حال ارسال دسته‌ای {create_count} محصول جدید و {update_count} محصول برای آپدیت...")
    try:
        res = requests.post(f"{WC_PRODUCTS_API_URL}/batch", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=batch_data, verify=False)
        res.raise_for_status()
        response_data = res.json()
        stats['created'] += len(response_data.get('create', []))
        stats['updated'] += len(response_data.get('update', []))
        print(f"✅ عملیات دسته‌ای محصولات اصلی با موفقیت انجام شد.")
        all_processed = response_data.get('create', []) + response_data.get('update', [])
        return {item['sku']: item['id'] for item in all_processed if 'sku' in item and 'id' in item}
    except Exception as e:
        stats['failed'] += create_count + update_count
        print(f"   ❌ خطای جدی در ارسال دسته‌ای به ووکامرس: {e}")
        if 'res' in locals(): print(f"   Response: {res.text}")
        return {}

def process_and_send_variations(wc_product_map, variations_map):
    tasks = [{'id': product_id, 'variations': variations_map[sku]} for sku, product_id in wc_product_map.items() if sku in variations_map and variations_map[sku]]
    if not tasks: return

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = []
        for task in tasks:
            variations_url = f"{WC_PRODUCTS_API_URL}/{task['id']}/variations/batch"
            delete_ids_resp = requests.get(f"{WC_PRODUCTS_API_URL}/{task['id']}/variations?per_page=100", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
            if delete_ids_resp.ok and delete_ids_resp.json():
                delete_ids = [v['id'] for v in delete_ids_resp.json()]
                if delete_ids:
                    requests.post(variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json={"delete": delete_ids}, verify=False)
            
            for i in range(0, len(task['variations']), 20):
                batch_data = {"create": task['variations'][i:i+20]}
                futures.append(executor.submit(requests.post, variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=batch_data, verify=False))
        
        for future in tqdm(as_completed(futures), total=len(futures), desc="Sending Variations", unit="batch"):
            try:
                res = future.result()
                if res.status_code not in [200, 201]:
                    print(f"\n   ❌ خطا در ثبت دسته متغیرها. Status: {res.status_code}, Response: {res.text}")
            except Exception as e:
                 print(f"\n   ❌ خطای شبکه در هنگام ثبت متغیرها: {e}")

def main():
    start_time = time.time()
    
    cat_map, source_cat_ids = sync_categories()
    if not cat_map: return

    all_products = []
    print("\n" + "="*20 + " فاز ۱: شروع دریافت محصولات " + "="*20)
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_cat = {executor.submit(get_all_products_from_category, cat_id): cat_id for cat_id in source_cat_ids}
        for future in tqdm(as_completed(future_to_cat), total=len(source_cat_ids), desc="Fetching Product Lists"):
            products_list = future.result()
            if products_list and 'error' not in products_list:
                all_products.extend(products_list)
            
    if not all_products:
        print("هیچ محصولی برای پردازش یافت نشد.")
        return
    
    unique_products = list({p['id']: p for p in all_products}.values())
    variations_map_by_id = fetch_variation_data_concurrently(unique_products)
    
    batch_to_create, batch_to_update, variations_to_process = [], [], {}
    stats = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0}

    print("\nدر حال دریافت SKU های موجود از ووکامرس...")
    existing_skus = {p['sku']: p['id'] for p in requests.get(f"{WC_PRODUCTS_API_URL}?per_page=100&fields=id,sku", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False).json()}

    for product in tqdm(unique_products, desc="Preparing Data"):
        if not (product.get('price', 0) > 0 and product.get('in_stock', True)):
            stats['skipped'] += 1
            continue
            
        product_id, product_name = product.get('id'), product.get('name')
        variations_raw = variations_map_by_id.get(product_id)
        
        variations, color_options = [], set()
        if variations_raw and isinstance(variations_raw, list):
            for var in variations_raw:
                if var.get("in_stock") and var.get("price", 0) > 0:
                    color_name = var.get("name", "").strip()
                    if not color_name: continue
                    color_options.add(color_name)
                    variations.append({"sku": f"NAMIN-{product_id}-{var.get('id', '')}", "regular_price": process_price(var.get("price", 0)), "stock_status": "instock", "attributes": [{"name": "رنگ", "option": color_name}]})

        other_attrs = parse_attributes_from_description(product.get('short_description', ''))
        sku = f"NAMIN-{product.get('sku', product_id)}"
        
        wc_categories = [{"id": cat_map[cid]} for cid in product.get('category_ids', []) if cid in cat_map]
        
        if variations:
            wc_data = {"name": product_name, "type": "variable", "sku": sku, "description": product.get('short_description', ''), "categories": wc_categories, "images": [{"src": img.get("src", "")} for img in product.get("images", [])], "attributes": [{"name": "رنگ", "visible": True, "variation": True, "options": sorted(list(color_options))}] + other_attrs, "default_attributes": [{"name": "رنگ", "option": sorted(list(color_options))[0]}] if color_options else []}
            variations_to_process[sku] = variations
        else:
            wc_data = {"name": product_name, "type": "simple", "sku": sku, "regular_price": process_price(product.get('price', 0)), "description": product.get('short_description', ''), "categories": wc_categories, "images": [{"src": img.get("src", "")} for img in product.get("images", [])], "stock_status": "instock", "attributes": other_attrs}
        
        if sku in existing_skus:
            wc_data['id'] = existing_skus[sku]
            batch_to_update.append(wc_data)
        else:
            batch_to_create.append(wc_data)

    wc_product_map = send_batch_to_woocommerce({'create': batch_to_create, 'update': batch_to_update}, stats)
    
    if wc_product_map:
        process_and_send_variations(wc_product_map, variations_to_process)

    total = len(unique_products)
    end_time = time.time()
    
    print("\n===============================")
    print("📊 آمار نهایی همگام‌سازی:")
    print(f"⏱️ کل زمان اجرا: {end_time - start_time:.2f} ثانیه")
    print(f"📦 تعداد کل گروه‌های محصول شناسایی شده: {total}")
    print(f"✅ محصولات پردازش شده: {stats['created'] + stats['updated']}")
    print(f"❌ محصولات نادیده گرفته شده: {stats['skipped']}")
    print(f"🟢 محصولات جدید ایجاد شده: {stats['created']}")
    print(f"🔵 محصولات آپدیت شده: {stats['updated']}")
    print(f"🔴 خطاها: {stats['failed']}")
    print("===============================")
    print("فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
