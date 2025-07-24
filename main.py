import requests
import urllib3
import os
import re
import time
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor

# غیرفعال کردن هشدار SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- اطلاعات ووکامرس (خوانده شده از Secrets گیت‌هاب) ---
WC_API_URL = os.environ.get("WC_API_URL")  # مثل https://pakhshemobile.ir/wp-json/wc/v3
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET]):
    print("❌ متغیرهای محیطی ووکامرس به درستی تنظیم نشده‌اند. برنامه متوقف می‌شود.")
    exit(1)
# ---------------------------------

# --- اطلاعات API سایت هدف ---
API_BASE_URL = "https://panel.naminet.co/api"
CATEGORY_ID = 13
PRODUCTS_LIST_URL_TEMPLATE = f"{API_BASE_URL}/categories/{CATEGORY_ID}/products/"
PRODUCT_ATTRIBUTES_API_URL_TEMPLATE = f"{API_BASE_URL}/products/attr/{{product_id}}"
AUTH_TOKEN = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJuYmYiOiIxNzUyMjUyMTE2IiwiZXhwIjoiMTc2MDAzMTcxNiIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL2VtYWlsYWRkcmVzcyI6IjA5MzcxMTExNTU4QGhtdGVtYWlsLm5leHQiLCJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6ImE3OGRkZjViLTVhMjMtNDVkZC04MDBlLTczNTc3YjBkMzQzOSIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL25hbWUiOiIwOTM3MTExMTU1OCIsIkN1c3RvbWVySWQiOiIxMDA4NCJ9.kXoXA0atw0M64b6m084Gt4hH9MoC9IFFDFwuHOEdazA"
REFERER_URL = "https://naminet.co/"
SOURCE_CATS_API = "https://panel.naminet.co/api/categories/"
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

# ------------------ انتقال دسته‌بندی‌ها ------------------

def get_source_categories():
    res = requests.get(SOURCE_CATS_API, verify=False)
    res.raise_for_status()
    return res.json()

def flatten_cats(tree, flat=None, parent_id=None):
    if flat is None:
        flat = []
    for cat in tree:
        cat = dict(cat)  # کپی برای تغییر
        if parent_id:
            cat["parent_category_id"] = parent_id
        flat.append(cat)
        if cat.get("childs"):
            flatten_cats(cat["childs"], flat, cat["id"])
    return flat

def build_source_cats_map(flat_cats):
    return {cat["id"]: cat for cat in flat_cats}

def get_wc_categories():
    wc_cats = []
    page = 1
    while True:
        url = f"{WC_API_URL}/products/categories"
        params = {"per_page": 100, "page": page}
        res = requests.get(url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), params=params, verify=False)
        if res.status_code != 200:
            break
        data = res.json()
        if not data:
            break
        wc_cats.extend(data)
        if len(data) < 100:
            break
        page += 1
    return wc_cats

def build_wc_cats_map(wc_cats):
    return {cat["name"].strip(): cat["id"] for cat in wc_cats}

def sort_cats_for_creation(flat_cats):
    sorted_cats = []
    id_to_cat = {cat["id"]: cat for cat in flat_cats}
    visited = set()
    def visit(cat):
        if cat["id"] in visited:
            return
        parent_id = cat.get("parent_category_id")
        if parent_id and parent_id in id_to_cat:
            visit(id_to_cat[parent_id])
        sorted_cats.append(cat)
        visited.add(cat["id"])
    for cat in flat_cats:
        visit(cat)
    return sorted_cats

def create_wc_category(cat, wc_cats_map, source_to_wc_id_map):
    data = {
        "name": cat["name"].strip(),
        "slug": cat.get("se_name", ""),
        "description": cat.get("description", ""),
    }
    img_url = cat.get("image", {}).get("src", "")
    if img_url:
        data["image"] = {"src": img_url}
    parent_id = cat.get("parent_category_id")
    if parent_id and parent_id in source_to_wc_id_map:
        data["parent"] = source_to_wc_id_map[parent_id]
    res = requests.post(
        f"{WC_API_URL}/products/categories",
        auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET),
        json=data,
        verify=False
    )
    if res.status_code in [200, 201]:
        new_id = res.json()["id"]
        print(f"✅ دسته‌بندی '{cat['name']}' ساخته شد. (id={new_id})")
        return new_id
    else:
        print(f"❌ خطا در ساخت دسته‌بندی '{cat['name']}': {res.text}")
        return None

def transfer_categories():
    print("⏳ دریافت دسته‌بندی‌های منبع ...")
    source_tree = get_source_categories()
    flat_cats = flatten_cats(source_tree)
    source_cats = build_source_cats_map(flat_cats)
    print(f"تعداد کل دسته‌بندی منبع: {len(flat_cats)}")

    print("⏳ دریافت دسته‌بندی‌های ووکامرس ...")
    wc_cats = get_wc_categories()
    wc_cats_map = build_wc_cats_map(wc_cats)
    print(f"تعداد کل دسته‌بندی ووکامرس: {len(wc_cats)}")

    sorted_cats = sort_cats_for_creation(flat_cats)
    source_to_wc_id_map = {}

    for cat in sorted_cats:
        name = cat["name"].strip()
        if name in wc_cats_map:
            wc_id = wc_cats_map[name]
            print(f"⏩ دسته‌بندی '{name}' قبلاً وجود دارد. (id={wc_id})")
            source_to_wc_id_map[cat["id"]] = wc_id
        else:
            new_id = create_wc_category(cat, wc_cats_map, source_to_wc_id_map)
            if new_id:
                wc_cats_map[name] = new_id
                source_to_wc_id_map[cat["id"]] = new_id

    print("\n✅ انتقال دسته‌بندی‌ها کامل شد.")
    return source_to_wc_id_map

# ----------------------------------------------------------

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
            if existing:
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
            if r.status_code in [429, 500] and attempt < retries - 1:
                print(f"   ⏳ تلاش مجدد به دلیل خطای {r.status_code} ...")
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

def extract_category_ids(product, category_mapping):
    category_ids = product.get("category_ids")
    if not category_ids:
        category_ids = [cat.get("id") for cat in product.get("categories", []) if cat.get("id")]
    if not category_ids:
        category_ids = [13]  # پیش‌فرض
    wc_cat_ids = [category_mapping.get(cid, 13) for cid in category_ids]
    return wc_cat_ids

def process_product(product, stats, category_mapping):
    product_name = product.get('name', 'بدون نام')
    product_id = product.get('id', '')

    category_ids = extract_category_ids(product, category_mapping)

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
            "categories": [{"id": cid} for cid in category_ids if cid],
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
                "categories": [{"id": cid} for cid in category_ids if cid],
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

def process_product_wrapper(args):
    product, stats, category_mapping = args
    try:
        if product.get('in_stock', True) and product.get('price', 0) > 0:
            process_product(product, stats, category_mapping)
    except Exception as e:
        print(f"   ❌ خطا در پردازش محصول {product.get('id', '')}: {e}")

def main():
    # انتقال دسته‌بندی‌ها
    category_mapping = transfer_categories()
    print("نگاشت id منبع به id ووکامرس:")
    print(category_mapping)
    print("\n⏳ انتقال محصولات ...")

    products = get_all_products()
    if not products:
        print("هیچ محصولی برای پردازش یافت نشد. برنامه خاتمه می‌یابد.")
        return
        
    total = len(products)
    available = sum(1 for p in products if p.get('in_stock', True) and p.get('price', 0) > 0)
    unavailable = total - available
    
    stats = {'created': 0, 'updated': 0}
    
    print(f"\n🔎 تعداد کل گروه‌های محصول شناسایی شده: {total}")
    print(f"✅ گروه‌های محصول موجود و با قیمت: {available}\n")
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        list(tqdm(executor.map(process_product_wrapper, [(p, stats, category_mapping) for p in products]), total=len(products), desc="در حال پردازش محصولات", unit="محصول"))
        
    print("\n===============================")
    print(f"📦 تعداد کل محصولات پردازش شده: {available} از {total}")
    print(f"🟢 محصولات جدید ایجاد شده: {stats['created']}")
    print(f"🔵 محصولات آپدیت شده: {stats['updated']}")
    print("===============================")
    print("تمام محصولات پردازش شدند. فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
