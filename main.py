import requests
import urllib3
import os
import time
import argparse  # بهبود: برای دریافت آرگومان از خط فرمان
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# --- بخش تنظیمات و ثابت‌ها (Config & Constants) ---

# غیرفعال کردن هشدار SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# بهبود: استفاده از یک کلاس برای مدیریت بهتر تنظیمات
class Config:
    # اطلاعات ووکامرس (خوانده شده از Secrets)
    WC_API_URL_BASE = os.environ.get("WC_API_URL")
    WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
    WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

    # اطلاعات API سایت هدف
    API_BASE_URL = "https://panel.naminet.co/api"
    # بهبود: توکن از متغیرهای محیطی خوانده می‌شود (امنیت بالاتر)
    AUTH_TOKEN = os.environ.get("NAMINet_AUTH_TOKEN") 
    REFERER_URL = "https://naminet.co/"
    
    # تنظیمات اجرایی
    MAX_THREADS_PRODUCTS = 10
    MAX_THREADS_VARIATIONS = 5
    BATCH_SIZE = 50 # تعداد آیتم‌ها در هر درخواست دسته‌ای به ووکامرس

# --- توابع کمکی (Helper Functions) ---

def validate_config():
    """بررسی می‌کند که آیا تمام متغیرهای محیطی ضروری تنظیم شده‌اند یا خیر."""
    if not all([Config.WC_API_URL_BASE, Config.WC_CONSUMER_KEY, Config.WC_CONSUMER_SECRET]):
        print("❌ متغیرهای محیطی ووکامرس (WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET) به درستی تنظیم نشده‌اند.")
        return False
    if not Config.AUTH_TOKEN:
        print("❌ متغیر محیطی توکن سایت هدف (NAMINet_AUTH_TOKEN) تنظیم نشده است.")
        return False
    return True

def make_api_request(url, params=None, is_wc=False):
    """یک درخواست API ارسال می‌کند و نتیجه را برمی‌گرداند."""
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
        
        response = requests.get(url, headers=headers, params=params, auth=auth, timeout=45, verify=False)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {'error': str(e)}

def process_price(price_value):
    """قیمت را بر اساس منطق تعریف شده محاسبه و گرد می‌کند."""
    try:
        price = float(price_value)
    except (ValueError, TypeError):
        return "0"

    if price <= 1: return "0"
    if price <= 7_000_000: new_price = price + 260_000
    elif price <= 10_000_000: new_price = price * 1.035
    elif price <= 20_000_000: new_price = price * 1.025
    elif price <= 30_000_000: new_price = price * 1.02
    else: new_price = price * 1.015
    # گرد کردن به نزدیک‌ترین ۱۰,۰۰۰ تومان
    return str(int(round(new_price / 10000) * 10000))

def parse_attributes_from_description(description):
    """ویژگی‌ها را از متن توضیحات استخراج می‌کند."""
    attrs = []
    if not description:
        return attrs
    for line in description.splitlines():
        if ':' in line:
            parts = line.split(':', 1)
            name, value = parts[0].strip(), parts[1].strip()
            if name and value:
                attrs.append({"name": name, "visible": True, "options": [value]})
    return attrs

# --- توابع اصلی واکشی اطلاعات (Core Fetching Functions) ---

def get_source_products(category_id):
    """تمام محصولات را از یک دسته‌بندی خاص سایت مبدا واکشی می‌کند."""
    all_products = []
    page = 1
    url_template = f"{Config.API_BASE_URL}/categories/{category_id}/products/"
    print(f"شروع دریافت محصولات از دسته‌بندی {category_id}...")
    while True:
        params = {'page': page, 'pageSize': 100}
        data = make_api_request(url_template, params=params)
        
        if not data or 'error' in data:
            print(f"خطا در دریافت اطلاعات: {data.get('error', 'پاسخ نامعتبر')}")
            break
        
        products_in_page = data.get("products", [])
        if not products_in_page:
            break
        
        all_products.extend(products_in_page)
        
        if len(products_in_page) < 100:
            break
        page += 1
        time.sleep(0.2)
        
    print(f"✅ {len(all_products)} محصول از سایت مبدا دریافت شد.")
    return all_products

def fetch_variations_concurrently(products):
    """اطلاعات متغیرهای محصولات را به صورت موازی واکشی می‌کند."""
    product_variations = {}
    with ThreadPoolExecutor(max_workers=Config.MAX_THREADS_PRODUCTS) as executor:
        future_to_product = {
            executor.submit(make_api_request, f"{Config.API_BASE_URL}/products/attr/{p['id']}"): p['id']
            for p in products if p.get('id')
        }
        
        print("\nدر حال دریافت اطلاعات متغیرها به صورت موازی...")
        for future in tqdm(as_completed(future_to_product), total=len(future_to_product), desc="Fetching Variations"):
            product_id = future_to_product[future]
            result = future.result()
            if result and 'error' not in result:
                product_variations[product_id] = result
    
    return product_variations

def get_existing_woocommerce_products():
    """تمام محصولات موجود در ووکامرس را با SKU آن‌ها واکشی می‌کند."""
    print("\nدر حال دریافت محصولات موجود از ووکامرس...")
    existing_products = {}
    page = 1
    while True:
        url = f"{Config.WC_API_URL_BASE}/products"
        params = {'per_page': 100, 'page': page, 'fields': 'id,sku,variations'}
        resp_data = make_api_request(url, params=params, is_wc=True)
        
        if not resp_data or 'error' in resp_data:
            break
        
        for p in resp_data:
            if p.get('sku'):
                existing_products[p['sku']] = {
                    'id': p['id'],
                    'variation_ids': p.get('variations', []) # برای محصولات متغیر
                }

        if len(resp_data) < 100:
            break
        page += 1
    print(f"✅ {len(existing_products)} محصول موجود در ووکامرس شناسایی شد.")
    return existing_products

# --- توابع پردازش و ارسال به ووکامرس (Processing & WooCommerce Sync Functions) ---

def prepare_product_data(product, variations_data):
    """داده‌های یک محصول را برای ارسال به ووکامرس آماده می‌کند."""
    product_id = product.get('id')
    sku = f"NAMIN-{product.get('sku', product_id)}"
    
    # داده‌های پایه که بین همه محصولات مشترک است
    base_data = {
        "name": product.get('name', 'بدون نام'),
        "sku": sku,
        "description": product.get('short_description', ''),
        "categories": [{"id": cat_id} for cat_id in product.get('category_ids', []) if cat_id],
        "images": [{"src": img.get("src")} for img in product.get("images", []) if img.get("src")],
        "attributes": parse_attributes_from_description(product.get('short_description', ''))
    }
    
    variations = []
    color_options = set()
    
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
            "type": "simple",
            "regular_price": process_price(product.get('price', 0)),
            "stock_status": "instock"
        })
        return base_data, []

def send_batch_to_woocommerce(batch_data, stats):
    """یک بچ از محصولات را به ووکامرس ارسال می‌کند."""
    if not any(batch_data.values()):
        return {}

    create_count = len(batch_data.get('create', []))
    update_count = len(batch_data.get('update', []))
    
    if create_count == 0 and update_count == 0:
        return {}

    print(f"\nدر حال ارسال دسته‌ای: {create_count} محصول جدید و {update_count} محصول برای آپدیت...")
    
    wc_product_map = {}
    try:
        url = f"{Config.WC_API_URL_BASE}/products/batch"
        res = requests.post(url, auth=(Config.WC_CONSUMER_KEY, Config.WC_CONSUMER_SECRET), json=batch_data, verify=False, timeout=120)
        res.raise_for_status()
        response_data = res.json()
        
        # پردازش پاسخ برای استخراج ID ها
        created_items = response_data.get('create', [])
        updated_items = response_data.get('update', [])
        
        stats['created'] += len(created_items)
        stats['updated'] += len(updated_items)

        for item in created_items + updated_items:
            if item.get('sku') and item.get('id'):
                # بررسی خطا در سطح آیتم
                if 'error' in item:
                    print(f"   ⚠️ خطا برای SKU {item['sku']}: {item['error']['message']}")
                    stats['failed'] += 1
                else:
                    wc_product_map[item['sku']] = item['id']

        print("✅ عملیات دسته‌ای محصولات با موفقیت انجام شد.")
        return wc_product_map
    except requests.exceptions.RequestException as e:
        print(f"   ❌ خطای جدی در ارسال دسته‌ای به ووکامرس: {e}")
        if 'res' in locals():
            print(f"   Response: {res.text}")
        return {}

def sync_variations(wc_product_id, new_variations):
    """
    بهبود بزرگ: متغیرها را هوشمندانه همگام‌سازی می‌کند.
    (فقط موارد مورد نیاز را ایجاد، آپدیت یا حذف می‌کند)
    """
    variations_url = f"{Config.WC_API_URL_BASE}/products/{wc_product_id}/variations"
    
    # 1. دریافت متغیرهای فعلی
    try:
        existing_vars_resp = requests.get(variations_url, auth=(Config.WC_CONSUMER_KEY, Config.WC_CONSUMER_SECRET), verify=False)
        existing_vars_resp.raise_for_status()
        existing_vars = {v['sku']: v for v in existing_vars_resp.json() if 'sku' in v}
    except requests.exceptions.RequestException:
        # اگر محصول متغیر جدید باشد، متغیری وجود ندارد
        existing_vars = {}

    new_vars_by_sku = {v['sku']: v for v in new_variations if 'sku' in v}
    
    # 2. آماده‌سازی بچ برای آپدیت
    batch_payload = {'create': [], 'update': [], 'delete': []}
    
    # آیتم‌های جدید و آپدیتی
    for sku, new_var_data in new_vars_by_sku.items():
        if sku in existing_vars:
            # آپدیت: اگر قیمت یا وضعیت تغییر کرده
            existing_var = existing_vars[sku]
            if existing_var.get('regular_price') != new_var_data.get('regular_price'):
                update_data = new_var_data.copy()
                update_data['id'] = existing_var['id']
                batch_payload['update'].append(update_data)
        else:
            # ایجاد
            batch_payload['create'].append(new_var_data)
            
    # آیتم‌های حذفی
    for sku, existing_var in existing_vars.items():
        if sku not in new_vars_by_sku:
            batch_payload['delete'].append(existing_var['id'])

    # 3. ارسال بچ به ووکامرس در صورت وجود تغییرات
    if any(batch_payload.values()):
        try:
            batch_url = f"{variations_url}/batch"
            res = requests.post(batch_url, auth=(Config.WC_CONSUMER_KEY, Config.WC_CONSUMER_SECRET), json=batch_payload, verify=False)
            res.raise_for_status()
            return f"تغییرات متغیر برای محصول {wc_product_id} اعمال شد."
        except requests.exceptions.RequestException as e:
            return f"خطا در همگام‌سازی متغیرهای محصول {wc_product_id}: {e}"
    return f"متغیرهای محصول {wc_product_id} به‌روز بودند."

# --- تابع اصلی (Main Function) ---

def main(category_id):
    start_time = time.time()
    
    if not validate_config():
        exit(1)

    # 1. واکشی اطلاعات از مبدا
    source_products = get_source_products(category_id)
    if not source_products:
        print("هیچ محصولی برای پردازش یافت نشد. پایان کار.")
        return
        
    variations_map = fetch_variations_concurrently(source_products)
    
    # 2. واکشی اطلاعات از ووکامرس
    existing_wc_products = get_existing_woocommerce_products()

    # 3. آماده‌سازی داده‌ها
    batch_to_create, batch_to_update = [], []
    variations_to_process = {}
    stats = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0}

    print("\nدر حال آماده‌سازی داده‌ها برای ارسال...")
    for product in tqdm(source_products, desc="Preparing Products"):
        if not (product.get('in_stock', True) and product.get('price', 0) > 0):
            stats['skipped'] += 1
            continue
            
        product_data, variations = prepare_product_data(product, variations_map.get(product.get('id')))
        sku = product_data['sku']
        
        if variations:
            variations_to_process[sku] = variations
        
        if sku in existing_wc_products:
            product_data['id'] = existing_wc_products[sku]['id']
            batch_to_update.append(product_data)
        else:
            batch_to_create.append(product_data)

    # 4. ارسال محصولات اصلی به ووکامرس
    wc_product_map = send_batch_to_woocommerce({'create': batch_to_create, 'update': batch_to_update}, stats)
    
    # 5. همگام‌سازی متغیرها
    if wc_product_map and variations_to_process:
        print("\nشروع فرآیند همگام‌سازی متغیرها...")
        tasks = []
        for sku, product_id in wc_product_map.items():
            if sku in variations_to_process:
                tasks.append((product_id, variations_to_process[sku]))
        
        with ThreadPoolExecutor(max_workers=Config.MAX_THREADS_VARIATIONS) as executor:
            futures = [executor.submit(sync_variations, pid, var_data) for pid, var_data in tasks]
            for future in tqdm(as_completed(futures), total=len(futures), desc="Syncing Variations"):
                # می‌توانید نتیجه را برای لاگ‌گیری دقیق‌تر چاپ کنید
                # print(future.result())
                pass

    # 6. نمایش آمار نهایی
    end_time = time.time()
    print("\n" + "="*30)
    print("📊 آمار نهایی همگام‌سازی:")
    print(f"⏱️ کل زمان اجرا: {end_time - start_time:.2f} ثانیه")
    print(f"📦 کل محصولات شناسایی شده از مبدا: {len(source_products)}")
    print(f"✅ محصولات جدید ایجاد شده: {stats['created']}")
    print(f"🔵 محصولات آپدیت شده: {stats['updated']}")
    print(f"⚪️ محصولات نادیده گرفته شده (ناموجود/بدون قیمت): {stats['skipped']}")
    print(f"🔴 محصولات ناموفق در پردازش دسته‌ای: {stats['failed']}")
    print("="*30)
    print("🚀 فرآیند به پایان رسید.")

if __name__ == "__main__":
    # بهبود: استفاده از argparse برای دریافت category_id از خط فرمان
    parser = argparse.ArgumentParser(description="اسکریپت همگام‌سازی محصولات از Naminet به WooCommerce")
    parser.add_argument("category_id", type=int, help="ID دسته‌بندی محصول در سایت مبدا (Naminet)")
    args = parser.parse_args()
    
    main(args.category_id)
