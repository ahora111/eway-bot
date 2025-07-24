import requests
import urllib3
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# --- بخش تنظیمات و ثابت‌ها (Config & Constants) ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class Config:
    WC_API_URL_BASE = os.environ.get("WC_API_URL")
    WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
    WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")
    API_BASE_URL = "https://panel.naminet.co/api"
    AUTH_TOKEN = os.environ.get("NAMINet_AUTH_TOKEN")
    REFERER_URL = "https://naminet.co/"
    
    # --- بهینه‌سازی‌ها برای جلوگیری از خطای سرور ---
    MAX_THREADS_CATEGORIES = 5
    MAX_THREADS_VARIATIONS = 10
    # **اصلاح کلیدی:** کاهش اندازه بچ برای جلوگیری از خطای 500
    BATCH_SIZE = 25 
    # **اصلاح کلیدی:** اضافه کردن مکث بین درخواست‌های بچ
    BATCH_SLEEP_INTERVAL = 2 # 2 ثانیه مکث بین هر بچ

# --- توابع کمکی (Helper Functions) ---
# (توابع کمکی بدون تغییر باقی می‌مانند، برای خوانایی حذف شده‌اند)
def validate_config():
    if not all([Config.WC_API_URL_BASE, Config.WC_CONSUMER_KEY, Config.WC_CONSUMER_SECRET]):
        print("❌ متغیرهای محیطی ووکامرس به درستی تنظیم نشده‌اند.")
        return False
    if not Config.AUTH_TOKEN:
        print("❌ متغیر محیطی NAMINet_AUTH_TOKEN تنظیم نشده است.")
        return False
    return True

def make_api_request(url, params=None, is_wc=False, timeout=60):
    try:
        if is_wc:
            auth = (Config.WC_CONSUMER_KEY, Config.WC_CONSUMER_SECRET)
            headers = {}
        else:
            auth = None
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
                'Authorization': f"Bearer {Config.AUTH_TOKEN}",
                'Referer': Config.REFERER_URL
            }
        response = requests.get(url, headers=headers, params=params, auth=auth, timeout=timeout, verify=False)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        error_response = {}
        if e.response is not None:
            try: error_response = e.response.json()
            except requests.exceptions.JSONDecodeError: error_response = {'raw_text': e.response.text}
        return {'error': str(e), 'response_body': error_response}

def process_price(price_value):
    try: price = float(price_value)
    except (ValueError, TypeError): return "0"
    if price <= 1: return "0"
    if price <= 7_000_000: new_price = price + 260_000
    elif price <= 10_000_000: new_price = price * 1.035
    elif price <= 20_000_000: new_price = price * 1.025
    elif price <= 30_000_000: new_price = price * 1.02
    else: new_price = price * 1.015
    return str(int(round(new_price / 10000) * 10000))

def parse_attributes_from_description(description):
    attrs = []
    if not description: return attrs
    for line in description.splitlines():
        if ':' in line:
            parts = line.split(':', 1)
            name, value = parts[0].strip(), parts[1].strip()
            if name and value:
                attrs.append({"name": name, "visible": True, "options": [value]})
    return attrs

# --- توابع اصلی واکشی اطلاعات ---
# (این توابع هم بدون تغییر، برای خوانایی حذف شده‌اند)
def get_all_source_categories():
    url = f"{Config.API_BASE_URL}/categories/"
    print("۱. در حال دریافت لیست تمام دسته‌بندی‌ها از سایت مبدا...")
    data = make_api_request(url)
    if isinstance(data, dict) and 'error' in data:
        print(f"❌ خطای جدی در حین درخواست دسته‌بندی‌ها: {data['error']}")
        return []
    categories_list = []
    if isinstance(data, dict) and 'categories' in data and isinstance(data['categories'], list):
        categories_list = data['categories']
    else:
        print(f"❌ خطای ساختاری: پاسخ API دسته‌بندی‌ها نامعتبر است. محتوای دریافتی: {data}")
        return []
    if not categories_list:
        print("⚠️ هشدار: هیچ دسته‌بندی در پاسخ API یافت نشد.")
        return []
    published_categories = [cat for cat in categories_list if cat.get('published')]
    category_ids = [cat['id'] for cat in published_categories if 'id' in cat]
    print(f"✅ {len(categories_list)} دسته‌بندی در کل یافت شد. {len(category_ids)} دسته‌بندی 'منتشر شده' برای پردازش انتخاب شد.")
    return category_ids

def get_products_from_category(category_id):
    products = []
    page = 1
    url_template = f"{Config.API_BASE_URL}/categories/{category_id}/products/"
    while True:
        params = {'page': page, 'pageSize': 100}
        data = make_api_request(url_template, params=params)
        products_in_page = []
        if isinstance(data, dict) and 'products' in data:
            products_in_page = data.get("products", [])
        if not data or 'error' in data or not products_in_page:
            break
        products.extend(products_in_page)
        if len(products_in_page) < 100: break
        page += 1
        time.sleep(0.1)
    return products

def get_all_products_concurrently(category_ids):
    print("\n۲. شروع دریافت موازی محصولات از تمام دسته‌بندی‌ها...")
    all_products_map = {}
    with ThreadPoolExecutor(max_workers=Config.MAX_THREADS_CATEGORIES) as executor:
        future_to_cat = {executor.submit(get_products_from_category, cat_id): cat_id for cat_id in category_ids}
        for future in tqdm(as_completed(future_to_cat), total=len(category_ids), desc="Fetching from Categories"):
            products_list = future.result()
            if products_list:
                for product in products_list:
                    if 'id' in product:
                        all_products_map[product['id']] = product
    final_list = list(all_products_map.values())
    print(f"✅ در مجموع {len(final_list)} محصول منحصر به فرد از {len(category_ids)} دسته‌بندی دریافت شد.")
    return final_list

def fetch_variations_concurrently(products):
    product_variations = {}
    url_template = f"{Config.API_BASE_URL}/products/attr/{{product_id}}"
    with ThreadPoolExecutor(max_workers=Config.MAX_THREADS_VARIATIONS) as executor:
        future_to_product = {
            executor.submit(make_api_request, url_template.format(product_id=p['id'])): p['id']
            for p in products if p.get('id')
        }
        print("\n۳. در حال دریافت اطلاعات متغیرها (رنگ و قیمت) به صورت موازی...")
        for future in tqdm(as_completed(future_to_product), total=len(future_to_product), desc="Fetching Variations"):
            product_id = future_to_product[future]
            result = future.result()
            if result and 'error' not in result:
                product_variations[product_id] = result
    return product_variations

def get_existing_woocommerce_products():
    print("\n۴. در حال دریافت محصولات موجود از فروشگاه شما (ووکامرس)...")
    existing_products = {}
    page = 1
    url = f"{Config.WC_API_URL_BASE}/products"
    while True:
        params = {'per_page': 100, 'page': page, 'fields': 'id,sku,variations'}
        resp_data = make_api_request(url, params=params, is_wc=True)
        if not resp_data or 'error' in resp_data: break
        for p in resp_data:
            if p.get('sku'):
                existing_products[p['sku']] = {'id': p['id'], 'variation_ids': p.get('variations', [])}
        if len(resp_data) < 100: break
        page += 1
    print(f"✅ {len(existing_products)} محصول موجود در فروشگاه شما شناسایی شد.")
    return existing_products

def prepare_product_data(product, variations_data):
    product_id = product.get('id')
    sku = f"NAMIN-{product.get('sku', product_id)}"
    base_data = {
        "name": product.get('name', 'بدون نام'), "sku": sku,
        "description": product.get('description', ''),
        "short_description": product.get('short_description', ''),
        "categories": [{"id": cat_id} for cat_id in product.get('category_ids', []) if cat_id],
        "images": [{"src": img.get("src")} for img in product.get("images", []) if img.get("src")],
        "attributes": parse_attributes_from_description(product.get('short_description', ''))
    }
    variations, color_options = [], set()
    if variations_data and isinstance(variations_data, list):
        for var in variations_data:
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
    if variations:
        sorted_colors = sorted(list(color_options))
        base_data.update({
            "type": "variable",
            "attributes": base_data["attributes"] + [{"name": "رنگ", "visible": True, "variation": True, "options": sorted_colors}],
            "default_attributes": [{"name": "رنگ", "option": sorted_colors[0]}] if sorted_colors else []
        })
        return base_data, variations
    else:
        base_data.update({
            "type": "simple", "regular_price": process_price(product.get('price', 0)), "stock_status": "instock"
        })
        return base_data, []

# --- تابع ارسال بچ با اصلاحات ---
def send_batches_to_woocommerce(batch_data, stats):
    """بچ‌ها را در قطعات کوچک‌تر به ووکامرس ارسال می‌کند تا از خطای سرور جلوگیری شود."""
    all_wc_product_map = {}
    for batch_type, items in batch_data.items():
        if not items: continue
        
        print(f"\nدر حال ارسال {len(items)} محصول برای '{batch_type}' در دسته‌های {Config.BATCH_SIZE} تایی...")
        
        for i in range(0, len(items), Config.BATCH_SIZE):
            chunk = items[i:i + Config.BATCH_SIZE]
            
            # **اصلاح کلیدی:** اضافه کردن مکث بین بچ‌ها
            if i > 0:
                print(f"   ... مکث به مدت {Config.BATCH_SLEEP_INTERVAL} ثانیه برای کاهش فشار روی سرور ...")
                time.sleep(Config.BATCH_SLEEP_INTERVAL)

            print(f"   - در حال ارسال دسته {i // Config.BATCH_SIZE + 1}...")
            try:
                url = f"{Config.WC_API_URL_BASE}/products/batch"
                # افزایش timeout برای درخواست‌های بچ
                res = requests.post(url, auth=(Config.WC_CONSUMER_KEY, Config.WC_CONSUMER_SECRET), json={batch_type: chunk}, verify=False, timeout=240)
                res.raise_for_status()
                response_data = res.json()
                
                # پردازش پاسخ برای هر آیتم در بچ
                processed_items = response_data.get(batch_type, [])
                
                success_count = 0
                for item in processed_items:
                    if 'error' in item:
                        stats['failed'] += 1
                        sku_info = item.get('sku', f"ID {item.get('id', 'N/A')}")
                        print(f"     ⚠️ خطا برای SKU {sku_info}: {item['error']['message']}")
                    else:
                        success_count += 1
                        if item.get('sku') and item.get('id'):
                            all_wc_product_map[item['sku']] = item['id']

                if batch_type == 'create':
                    stats['created'] += success_count
                elif batch_type == 'update':
                    stats['updated'] += success_count

            except requests.exceptions.RequestException as e:
                print(f"   ❌ خطای جدی در ارسال دسته '{batch_type}': {e}")
                if 'res' in locals():
                    print(f"     Response Status: {res.status_code}, Response Text: {res.text[:500]}...") # نمایش بخشی از پاسخ خطا
                stats['failed'] += len(chunk)
    
    return all_wc_product_map


def sync_variations(wc_product_id, new_variations):
    variations_url = f"{Config.WC_API_URL_BASE}/products/{wc_product_id}/variations"
    try:
        existing_vars_resp = requests.get(variations_url, auth=(Config.WC_CONSUMER_KEY, Config.WC_CONSUMER_SECRET), verify=False, params={'per_page': 100})
        existing_vars_resp.raise_for_status()
        existing_vars = {v['sku']: v for v in existing_vars_resp.json() if 'sku' in v}
    except requests.exceptions.RequestException: existing_vars = {}

    new_vars_by_sku = {v['sku']: v for v in new_variations if 'sku' in v}
    batch_payload = {'create': [], 'update': [], 'delete': []}
    
    for sku, new_var_data in new_vars_by_sku.items():
        if sku in existing_vars:
            if existing_vars[sku].get('regular_price') != new_var_data.get('regular_price'):
                update_data = new_var_data.copy()
                update_data['id'] = existing_vars[sku]['id']
                batch_payload['update'].append(update_data)
        else:
            batch_payload['create'].append(new_var_data)
    
    batch_payload['delete'] = [v['id'] for sku, v in existing_vars.items() if sku not in new_vars_by_sku]

    if any(batch_payload.values()):
        try:
            requests.post(f"{variations_url}/batch", auth=(Config.WC_CONSUMER_KEY, Config.WC_CONSUMER_SECRET), json=batch_payload, verify=False).raise_for_status()
        except requests.exceptions.RequestException:
            pass
    return True


# --- تابع اصلی (Main Function) ---

def main():
    start_time = time.time()
    if not validate_config(): exit(1)

    category_ids = get_all_source_categories()
    if not category_ids:
        print("هیچ دسته‌بندی فعالی برای پردازش یافت نشد. پایان کار.")
        return
    
    source_products = get_all_products_concurrently(category_ids)
    if not source_products:
        print("هیچ محصولی برای پردازش یافت نشد. پایان کار.")
        return
        
    variations_map = fetch_variations_concurrently(source_products)
    existing_wc_products = get_existing_woocommerce_products()

    print("\n۵. در حال آماده‌سازی داده‌ها برای ارسال به ووکامرس...")
    batch_to_create, batch_to_update = [], []
    variations_to_process = {}
    stats = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0}

    for product in tqdm(source_products, desc="Preparing Products"):
        variations_data = variations_map.get(product.get('id'))
        is_simple_available = not variations_data and product.get('in_stock', True) and product.get('price', 0) > 0
        is_variable_available = variations_data and any(v.get("in_stock") and v.get("price", 0) > 0 for v in variations_data)
        if not (is_simple_available or is_variable_available):
            stats['skipped'] += 1
            continue
        product_data, variations = prepare_product_data(product, variations_data)
        sku = product_data['sku']
        if variations: variations_to_process[sku] = variations
        if sku in existing_wc_products:
            product_data['id'] = existing_wc_products[sku]['id']
            batch_to_update.append(product_data)
        else:
            batch_to_create.append(product_data)

    # مرحله ۶: ارسال محصولات با بچ‌های کوچک‌تر
    wc_product_map = send_batches_to_woocommerce({'create': batch_to_create, 'update': batch_to_update}, stats)
    
    # مرحله ۷: همگام‌سازی متغیرها
    if wc_product_map and variations_to_process:
        print("\n۷. شروع فرآیند همگام‌سازی متغیرها...")
        tasks = [(pid, variations_to_process[sku]) for sku, pid in wc_product_map.items() if sku in variations_to_process]
        if tasks:
            with ThreadPoolExecutor(max_workers=Config.MAX_THREADS_VARIATIONS) as executor:
                futures = [executor.submit(sync_variations, pid, var_data) for pid, var_data in tasks]
                for _ in tqdm(as_completed(futures), total=len(futures), desc="Syncing Variations"):
                    pass

    # مرحله ۸: نمایش آمار نهایی
    end_time = time.time()
    total_duration = end_time - start_time
    print("\n" + "="*40)
    print("📊 آمار نهایی همگام‌سازی کامل")
    print(f"⏱️ کل زمان اجرا: {total_duration:.2f} ثانیه ({total_duration/60:.2f} دقیقه)")
    print(f"📦 کل محصولات منحصر به فرد شناسایی شده: {len(source_products)}")
    print(f"✅ محصولات جدید ایجاد شده: {stats['created']}")
    print(f"🔵 محصولات آپدیت شده: {stats['updated']}")
    print(f"⚪️ محصولات نادیده گرفته شده (ناموجود/بدون قیمت): {stats['skipped']}")
    print(f"🔴 محصولات ناموفق: {stats['failed']}")
    print("="*40)
    print("🚀 فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
