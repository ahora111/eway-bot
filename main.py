import requests
import urllib3
import os
import re
import time
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- تنظیمات ---
WC_API_URL = os.environ.get("WC_API_URL")  # باید https://pakhshemobile.ir/wp-json/wc/v3 باشد
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

API_BASE_URL = "https://panel.naminet.co/api"
CATEGORY_ID = 13
PRODUCTS_LIST_URL_TEMPLATE = f"{API_BASE_URL}/categories/{CATEGORY_ID}/products/"
PRODUCT_ATTRIBUTES_API_URL_TEMPLATE = f"{API_BASE_URL}/products/attr/{{product_id}}"
MEGA_MENU_API_URL = f"{API_BASE_URL}/mega-menu"
AUTH_TOKEN = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJuYmYiOiIxNzUyMjUyMTE2IiwiZXhwIjoiMTc2MDAzMTcxNiIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL2VtYWlsYWRkcmVzcyI6IjA5MzcxMTExNTU4QGhtdGVtYWlsLm5leHQiLCJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6ImE3OGRkZjViLTVhMjMtNDVkZC04MDBlLTczNTc3YjBkMzQzOSIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL25hbWUiOiIwOTM3MTExMTU1OCIsIkN1c3RvbWVySWQiOiIxMDA4NCJ9.kXoXA0atw0M64b6m084Gt4hH9MoC9IFFDFwuHOEdazA"
REFERER_URL = "https://naminet.co/"

# --- توابع دسته‌بندی ---
def extract_categories_from_mega_menu(mega_menu):
    categories = []
    def walk(node, parent_id=0):
        cat = {
            "id": node["id"],
            "name": node["name"].strip(),
            "parent": parent_id
        }
        categories.append(cat)
        for child in node.get("childs", []):
            walk(child, node["id"])
    for item in mega_menu:
        walk(item)
    return categories

def get_all_wc_categories():
    categories = []
    page = 1
    while True:
        url = f"{WC_API_URL}/products/categories?per_page=100&page={page}"
        r = requests.get(url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
        if r.status_code != 200:
            print(f"   ❌ خطا در دریافت دسته‌بندی‌های ووکامرس: {r.status_code}")
            break
        data = r.json()
        if not data:
            break
        categories.extend(data)
        if len(data) < 100:
            break
        page += 1
    return categories

def create_wc_category(name, parent=0):
    url = f"{WC_API_URL}/products/categories"
    data = {"name": name, "parent": parent}
    r = requests.post(url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
    if r.status_code in [200, 201]:
        return r.json()
    else:
        print(f"   ❌ خطا در ایجاد دسته‌بندی '{name}': {r.status_code} - {r.text}")
        return None

def sync_wc_categories_from_source(source_categories):
    wc_cats = get_all_wc_categories()
    wc_cat_name_parent = {(c["name"], c["parent"]): c["id"] for c in wc_cats}
    cat_id_map = {}
    for cat in source_categories:
        key = (cat["name"], cat["parent"])
        if key in wc_cat_name_parent:
            cat_id_map[cat["id"]] = wc_cat_name_parent[key]
        else:
            parent_wc_id = cat_id_map.get(cat["parent"], 0)
            new_cat = create_wc_category(cat["name"], parent=parent_wc_id)
            if new_cat and "id" in new_cat:
                cat_id_map[cat["id"]] = new_cat["id"]
    return cat_id_map

# --- سایر توابع ---
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
        price_value = re.sub(r'[^\d.]', '', str(price_value))
        price_value = float(price_value)
    except (ValueError, TypeError):
        return "0"
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

def validate_product(wc_data):
    errors = []
    if not wc_data.get('name'):
        errors.append("نام محصول وجود ندارد.")
    if wc_data.get('type') == 'simple':
        price = wc_data.get('regular_price')
        if not price or price == "0":
            errors.append("قیمت محصول ساده معتبر نیست.")
    if not wc_data.get('sku'):
        errors.append("کد SKU وجود ندارد.")
    if not wc_data.get('categories') or not isinstance(wc_data['categories'], list) or not wc_data['categories']:
        errors.append("دسته‌بندی محصول وجود ندارد.")
    return errors

def _send_to_woocommerce(sku, data, stats, retries=3):
    check_url = f"{WC_API_URL}/products?sku={sku}"
    for attempt in range(retries):
        try:
            r = requests.get(check_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
            r.raise_for_status()
            existing = r.json()
            product_id = None
            if existing and isinstance(existing, list) and len(existing) > 0:
                product_id = existing[0]['id']
                update_url = f"{WC_API_URL}/products/{product_id}"
                res = requests.put(update_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
                if res.status_code == 200:
                    stats['updated'] += 1
                else:
                    print(f"   ❌ خطا در آپدیت. Status: {res.status_code}, Response: {res.text}")
            else:
                res = requests.post(f"{WC_API_URL}/products", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
                if res.status_code == 201:
                    product_id = res.json()['id']
                    stats['created'] += 1
                else:
                    print(f"   ❌ خطا در ایجاد محصول. Status: {res.status_code}, Response: {res.text}")
            return product_id
        except requests.exceptions.HTTPError as e:
            status_code = getattr(r, 'status_code', None)
            if status_code in [429, 500] and attempt < retries - 1:
                print(f"   ⏳ تلاش مجدد به دلیل خطای {status_code} ...")
                time.sleep(5)
                continue
            print(f"   ❌ خطای کلی در ارتباط با ووکامرس: {e}")
            return None
        except Exception as e:
            print(f"   ❌ خطای کلی در ارتباط با ووکامرس: {e}")
            return None

def create_or_update_variations(product_id, variations):
    if not product_id or not variations: return
    variations_url = f"{WC_API_URL}/products/{product_id}/variations/batch"
    existing_vars_resp = requests.get(f"{WC_API_URL}/products/{product_id}/variations?per_page=100", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), verify=False)
    if existing_vars_resp.status_code == 200 and existing_vars_resp.json():
        delete_ids = [v['id'] for v in existing_vars_resp.json()]
        if delete_ids:
            requests.post(variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json={"delete": delete_ids}, verify=False)
    for i in range(0, len(variations), 10):
        batch = variations[i:i + 10]
        batch_data = {"create": batch}
        res_vars = requests.post(variations_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=batch_data, verify=False)
        if res_vars.status_code not in [200, 201]:
            print(f"   ❌ خطا در ثبت دسته متغیرها. Status: {res_vars.status_code}, Response: {res_vars.text}")
            break

def get_all_products():
    all_products = []
    page = 1
    while True:
        print(f"در حال دریافت صفحه {page} از لیست محصولات...")
        params = {'page': page, 'pageSize': 100}
        data = make_api_request(PRODUCTS_LIST_URL_TEMPLATE, params=params)
        if data is None: break
        products_in_page = data.get("products", [])
        if not products_in_page:
            print("صفحه آخر دریافت شد.")
            break
        all_products.extend(products_in_page)
        print(f"تعداد {len(products_in_page)} محصول از صفحه {page} دریافت شد.")
        if len(products_in_page) < 100: break
        page += 1
    print(f"\nدریافت اطلاعات از API کامل شد. کل محصولات دریافت شده: {len(all_products)}")
    return all_products

# --- تابع اصلی ارسال محصول با نگاشت دسته‌بندی ---
def process_product(product, stats, cat_id_map):
    product_name = product.get('name', 'بدون نام')
    product_id = product.get('id', '')

    # استخراج دسته‌بندی از محصول منبع و نگاشت به ووکامرس
    source_cat_ids = [cat["id"] for cat in product.get("categories", [])]
    wc_category_ids = [cat_id_map.get(cid, 13) for cid in source_cat_ids if cat_id_map.get(cid)]

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

    if variations:
        wc_data = {
            "name": product_name,
            "type": "variable",
            "sku": f"NAMIN-{product.get('sku', product_id)}",
            "description": product.get('short_description', ''),
            "categories": [{"id": cid} for cid in wc_category_ids if cid],
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
        errors = validate_product(wc_data)
        if errors:
            print(f"   ❌ محصول '{wc_data.get('name', '')}' اعتبارسنجی نشد:")
            for err in errors:
                print(f"      - {err}")
            return
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
                "categories": [{"id": cid} for cid in wc_category_ids if cid],
                "images": [{"src": img.get("src", "")} for img in product.get("images", [])],
                "stock_status": "instock",
                "attributes": other_attrs
            }
            errors = validate_product(wc_data)
            if errors:
                print(f"   ❌ محصول '{wc_data.get('name', '')}' اعتبارسنجی نشد:")
                for err in errors:
                    print(f"      - {err}")
                return
            _send_to_woocommerce(wc_data['sku'], wc_data, stats)
        else:
            print(f"   محصول ساده قیمت ندارد یا ناموجود است. نادیده گرفته شد.")

def process_product_wrapper(args):
    product, stats, cat_id_map = args
    try:
        if product.get('in_stock', True) and product.get('price', 0) > 0:
            process_product(product, stats, cat_id_map)
    except Exception as e:
        print(f"   ❌ خطا در پردازش محصول {product.get('id', '')}: {e}")

# --- main ---
def main():
    # 1. دریافت mega_menu از API منبع
    print("در حال دریافت دسته‌بندی‌ها از API منبع ...")
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Authorization': AUTH_TOKEN,
        'Referer': REFERER_URL
    }
    resp = requests.get(MEGA_MENU_API_URL, headers=headers, verify=False)
    if resp.status_code != 200:
        print("❌ خطا در دریافت mega_menu از API منبع:", resp.status_code)
        print(resp.text)
        return

    try:
        mega_menu_json = resp.json()
    except Exception as e:
        print("❌ خطا در تبدیل پاسخ به JSON:", e)
        print("پاسخ دریافتی:")
        print(resp.text)
        return

    mega_menu = mega_menu_json.get("mega_menu", [])
    if not mega_menu:
        print("❌ mega_menu خالی است یا کلید mega_menu وجود ندارد!")
        print("پاسخ دریافتی:")
        print(mega_menu_json)
        return

    # 2. استخراج و ساخت دسته‌بندی‌ها
    source_categories = extract_categories_from_mega_menu(mega_menu)
    cat_id_map = sync_wc_categories_from_source(source_categories)

    # 3. دریافت محصولات
    products = get_all_products()
    if not products:
        print("هیچ محصولی برای پردازش یافت نشد. برنامه خاتمه می‌یابد.")
        return

    total = len(products)
    available = sum(1 for p in products if p.get('in_stock', True) and p.get('price', 0) > 0)
    stats = {'created': 0, 'updated': 0}

    print(f"\n🔎 تعداد کل گروه‌های محصول شناسایی شده: {total}")
    print(f"✅ گروه‌های محصول موجود و با قیمت: {available}\n")

    with ThreadPoolExecutor(max_workers=5) as executor:
        list(tqdm(executor.map(process_product_wrapper, [(p, stats, cat_id_map) for p in products]), total=len(products), desc="در حال پردازش محصولات", unit="محصول"))

    print("\n===============================")
    print(f"📦 تعداد کل محصولات پردازش شده: {available} از {total}")
    print(f"🟢 محصولات جدید ایجاد شده: {stats['created']}")
    print(f"🔵 محصولات آپدیت شده: {stats['updated']}")
    print("===============================")
    print("تمام محصولات و دسته‌بندی‌ها پردازش شدند. فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
