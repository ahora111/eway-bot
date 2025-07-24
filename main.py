import requests
import urllib3
import os
import re
import time
import json
import sys
import random
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from threading import Lock

# ==============================================================================
# --- تنظیمات و متغیرهای سراسری ---
# ==============================================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- اطلاعات ووکامرس ---
WC_API_URL = os.environ.get("WC_API_URL")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

# --- اطلاعات سایت Eways ---
BASE_URL = "https://panel.eways.co"
AUT_COOKIE_VALUE = os.environ.get("EWAYS_AUTH_TOKEN")
SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"
PRODUCT_LIST_URL_TEMPLATE = f"{BASE_URL}/Store/List/{{category_id}}/2/2/0/0/0/10000000000?page={{page}}"

# ==============================================================================
# --- توابع مربوط به سایت مبدا (Eways.co) ---
# ==============================================================================

def get_session():
    """یک Session با کوکی احراز هویت ایجاد می‌کند."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f"{BASE_URL}/",
        'X-Requested-With': 'XMLHttpRequest'
    })
    if AUT_COOKIE_VALUE:
        session.cookies.set('Aut', AUT_COOKIE_VALUE, domain='panel.eways.co')
    session.verify = False
    return session

def get_and_parse_categories(session):
    """دسته‌بندی‌ها را از API دریافت و به صورت یک لیست مسطح برمی‌گرداند."""
    print(f"⏳ دریافت دسته‌بندی‌ها از: {SOURCE_CATS_API_URL}")
    try:
        response = session.get(SOURCE_CATS_API_URL, timeout=30)
        if response.status_code != 200:
            print(f"❌ خطای وضعیت {response.status_code} در دریافت دسته‌بندی‌ها.")
            return None
        print("✅ HTML دسته‌بندی‌ها با موفقیت دریافت شد.")
        
        soup = BeautifulSoup(response.text, 'lxml')
        all_menu_items = soup.select("li[id^='menu-item-']")
        
        if not all_menu_items:
            print("❌ هیچ آیتم دسته‌بندی در HTML پیدا نشد.")
            return []
            
        cats_map = {}
        for item in all_menu_items:
            cat_id_raw = item.get('id', '')
            match = re.search(r'(\d+)', cat_id_raw)
            if not match: continue
            cat_menu_id = int(match.group(1))

            a_tag = item.find('a', recursive=False) or item.select_one("a")
            if not a_tag or not a_tag.get('href'): continue
            
            name = a_tag.text.strip()
            real_id_match = re.search(r'/Store/List/(\d+)', a_tag['href'])
            real_id = int(real_id_match.group(1)) if real_id_match else None

            if name and real_id and name != "#":
                cats_map[cat_menu_id] = {"id": real_id, "name": name, "parent_id": None}

        for item in all_menu_items:
            cat_id_raw = item.get('id', '')
            match = re.search(r'(\d+)', cat_id_raw)
            if not match: continue
            cat_menu_id = int(match.group(1))

            parent_li = item.find_parent("li", class_="menu-item-has-children")
            if parent_li:
                parent_id_raw = parent_li.get('id', '')
                parent_match = re.search(r'(\d+)', parent_id_raw)
                if parent_match:
                    parent_menu_id = int(parent_match.group(1))
                    if cat_menu_id in cats_map and parent_menu_id in cats_map:
                        cats_map[cat_menu_id]['parent_id'] = cats_map[parent_menu_id]['id']
        
        final_cats = list(cats_map.values())
        print(f"✅ تعداد {len(final_cats)} دسته‌بندی معتبر استخراج شد.")
        return final_cats
    except Exception as e:
        print(f"❌ خطای ناشناخته در پردازش دسته‌بندی‌ها: {e}")
        return None

def get_products_from_category_page(session, category_id):
    """محصولات را از صفحات HTML یک دسته‌بندی استخراج می‌کند و صفحه‌بندی را هوشمندانه مدیریت می‌کند."""
    all_products_in_category = []
    seen_product_ids = set()
    page_num = 1
    
    while True:
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page_num)
        print(f"  - در حال دریافت محصولات از: {url}")
        
        try:
            response = session.get(url, timeout=30)
            if response.status_code != 200: break
                
            soup = BeautifulSoup(response.text, 'lxml')
            product_blocks = soup.select(".goods_item.goods-record")
            if not product_blocks:
                print("    - هیچ محصولی در این صفحه یافت نشد. پایان صفحه‌بندی.")
                break

            current_page_product_ids = []
            for block in product_blocks:
                try:
                    if 'noCount' in block.get('class', []): continue
                    
                    id_tag = block.select_one("a[data-productid]")
                    product_id = id_tag['data-productid'] if id_tag else None
                    if not product_id or product_id in seen_product_ids: continue

                    seen_product_ids.add(product_id)
                    current_page_product_ids.append(product_id)
                    
                    name = (block.select_one(".goods-record-title").text.strip() if block.select_one(".goods-record-title") else None)
                    price_tag = block.select_one(".goods-record-price")
                    price = "0"
                    if price_tag:
                        if price_tag.find('del'): price_tag.find('del').decompose()
                        price = re.sub(r'[^\d]', '', price_tag.text.strip()) or "0"
                    
                    img_tag = block.select_one("img.goods-record-image")
                    image_url = (img_tag['data-src'] if img_tag and 'data-src' in img_tag.attrs else "")
                    if image_url and not image_url.startswith('http'):
                        image_url = "https://staticcontent.eways.co" + image_url
                    
                    stock_tag = block.select_one(".goods-record-count span")
                    stock = int(stock_tag.text.strip()) if stock_tag else 1
                    
                    if name and int(price) > 0:
                        all_products_in_category.append({
                            "id": product_id, "name": name, "price": price, "stock": stock,
                            "image": image_url, "category_id": category_id
                        })
                except Exception as e:
                    print(f"      - خطا در پردازش یک بلاک محصول: {e}. رد شدن...")

            if not current_page_product_ids:
                print("    - محصول جدیدی در این صفحه یافت نشد، توقف صفحه‌بندی.")
                break

            page_num += 1
            time.sleep(random.uniform(0.5, 1.5))
        except Exception as e:
            print(f"    - خطای کلی در پردازش صفحه محصولات: {e}")
            break
            
    return all_products_in_category

def get_all_products(session, categories):
    """تمام محصولات را از تمام دسته‌بندی‌های انتخاب شده جمع‌آوری می‌کند."""
    all_products = {}
    print("\n⏳ شروع فرآیند جمع‌آوری تمام محصولات...")
    for category in tqdm(categories, desc="پردازش دسته‌بندی‌ها"):
        products_in_cat = get_products_from_category_page(session, category['id'])
        for product in products_in_cat:
            all_products[product['id']] = product
    print(f"\n✅ فرآیند جمع‌آوری کامل شد. تعداد کل محصولات یکتا و موجود: {len(all_products)}")
    return list(all_products.values())


# ==============================================================================
# --- توابع مربوط به ووکامرس و انتقال داده ---
# ==============================================================================
def sort_cats_for_creation(flat_cats):
    """دسته‌بندی‌ها را برای ایجاد مرتب می‌کند (اول والد، بعد فرزند)."""
    sorted_cats, id_to_cat, visited = [], {cat["id"]: cat for cat in flat_cats}, set()
    def visit(cat):
        if cat["id"] in visited: return
        parent_id = cat.get("parent_id")
        if parent_id and parent_id in id_to_cat:
            visit(id_to_cat[parent_id])
        sorted_cats.append(cat)
        visited.add(cat["id"])
    for cat in flat_cats: visit(cat)
    return sorted_cats

def get_wc_categories():
    """تمام دسته‌بندی‌های موجود در ووکامرس را دریافت می‌کند."""
    wc_cats, page = [], 1
    while True:
        try:
            res = requests.get(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), params={"per_page": 100, "page": page}, verify=False)
            res.raise_for_status()
            data = res.json()
            if not data: break
            wc_cats.extend(data)
            if len(data) < 100: break
            page += 1
        except Exception as e: break
    return wc_cats

def transfer_categories_to_wc(source_categories):
    """دسته‌بندی‌های انتخاب شده را به ووکامرس منتقل کرده و نقشه IDها را برمی‌گرداند."""
    print("\n⏳ انتقال دسته‌بندی‌ها به ووکامرس...")
    wc_cats = get_wc_categories()
    wc_cats_map = {cat["name"].strip().lower(): cat["id"] for cat in wc_cats}
    source_to_wc_id_map = {}
    
    for cat in tqdm(sort_cats_for_creation(source_categories), desc="انتقال دسته‌بندی‌ها"):
        name = cat["name"].strip()
        if name.lower() in wc_cats_map:
            wc_id = wc_cats_map[name.lower()]
            source_to_wc_id_map[cat["id"]] = wc_id
        else:
            data = {"name": name}
            parent_id = cat.get("parent_id")
            if parent_id and parent_id in source_to_wc_id_map:
                data["parent"] = source_to_wc_id_map[parent_id]
            try:
                res = requests.post(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
                if res.status_code in [200, 201]:
                    new_id = res.json()["id"]
                    source_to_wc_id_map[cat["id"]] = new_id
                    wc_cats_map[name.lower()] = new_id
                else: print(f"❌ خطا در ساخت '{name}': {res.text}")
            except Exception as e: print(f"❌ خطای شبکه در ساخت '{name}': {e}")
    print("✅ انتقال دسته‌بندی‌ها کامل شد.")
    return source_to_wc_id_map

def process_price(price_value):
    """فرمول محاسبه قیمت فروش بر اساس قیمت خرید."""
    try:
        price_value = float(re.sub(r'[^\d.]', '', str(price_value))) * 1000
    except (ValueError, TypeError): return "0"
    if price_value <= 1: return "0"
    elif price_value <= 7000000: new_price = price_value + 260000
    elif price_value <= 10000000: new_price = price_value * 1.035
    elif price_value <= 20000000: new_price = price_value * 1.025
    elif price_value <= 30000000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price, -4)))

def _send_to_woocommerce(sku, data, stats):
    """محصول را در ووکامرس ایجاد یا آپدیت می‌کند."""
    try:
        auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
        check_url = f"{WC_API_URL}/products?sku={sku}"
        
        r_check = requests.get(check_url, auth=auth, verify=False, timeout=20)
        r_check.raise_for_status()
        existing = r_check.json()
        
        if existing:
            product_id = existing[0]['id']
            update_data = {
                "regular_price": data["regular_price"],
                "stock_quantity": data["stock_quantity"],
                "stock_status": data["stock_status"]
            }
            res = requests.put(f"{WC_API_URL}/products/{product_id}", auth=auth, json=update_data, verify=False, timeout=20)
            if res.status_code == 200:
                with stats['lock']: stats['updated'] += 1
            else: print(f"   ❌ خطا در آپدیت '{data['name']}'. Status: {res.status_code}")
        else:
            res = requests.post(f"{WC_API_URL}/products", auth=auth, json=data, verify=False, timeout=20)
            if res.status_code == 201:
                with stats['lock']: stats['created'] += 1
            else: print(f"   ❌ خطا در ایجاد '{data['name']}'. Status: {res.status_code}")
    except Exception as e:
        print(f"   ❌ خطای کلی در ارتباط با ووکامرس برای SKU {sku}: {e}")

def process_product_wrapper(args):
    """Wrapper برای استفاده در ThreadPoolExecutor."""
    product, stats, category_mapping = args
    try:
        wc_cat_id = category_mapping.get(product.get('category_id'))
        if not wc_cat_id: return
            
        wc_data = {
            "name": product.get('name', 'بدون نام'), "type": "simple", "sku": f"EWAYS-{product.get('id')}",
            "regular_price": process_price(product.get('price', 0)),
            "categories": [{"id": wc_cat_id}],
            "images": [{"src": product.get("image")}] if product.get("image") else [],
            "stock_quantity": product.get('stock', 0), "manage_stock": True,
            "stock_status": "instock" if product.get('stock', 0) > 0 else "outofstock"
        }
        _send_to_woocommerce(wc_data['sku'], wc_data, stats)
    except Exception as e:
        print(f"   ❌ خطای جدی در پردازش محصول {product.get('id', '')}: {e}")

# ==============================================================================
# --- تابع اصلی برنامه ---
# ==============================================================================
def main():
    if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET, AUT_COOKIE_VALUE]):
        print("❌ یکی از متغیرهای محیطی ضروری (WC_* یا EWAYS_AUTH_TOKEN) تنظیم نشده است.")
        return

    session = get_session()
    
    source_categories = get_and_parse_categories(session)
    if source_categories is None: return

    # در اینجا دسته‌بندی‌های مورد نظر را انتخاب کنید.
    # می‌توانید این بخش را برای خواندن از فایل یا متغیر محیطی فعال کنید.
    selected_ids = [4285, 16778] # مثال: ID برای گوشی و لپ‌تاپ
    filtered_categories = [c for c in source_categories if c['id'] in selected_ids]
    print(f"\n✅ دسته‌بندی‌های منتخب: {[c['name'] for c in filtered_categories]}")
    if not filtered_categories:
        print("❌ هیچکدام از دسته‌بندی‌های منتخب یافت نشدند.")
        return

    category_mapping = transfer_categories_to_wc(filtered_categories)
    if not category_mapping:
        print("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد. برنامه خاتمه می‌یابد.")
        return

    products = get_all_products(session, filtered_categories)
    if not products:
        print("✅ هیچ محصولی برای پردازش یافت نشد. برنامه با موفقیت خاتمه می‌یابد.")
        return

    stats = {'created': 0, 'updated': 0, 'lock': Lock()}
    print(f"\n🚀 شروع پردازش و ارسال {len(products)} محصول به ووکامرس...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        args_list = [(p, stats, category_mapping) for p in products]
        list(tqdm(executor.map(process_product_wrapper, args_list), total=len(products), desc="ارسال محصولات"))

    print("\n===============================")
    print(f"📦 محصولات پردازش شده: {len(products)}")
    print(f"🟢 ایجاد شده: {stats['created']}")
    print(f"🔵 آپدیت شده: {stats['updated']}")
    print("===============================\nتمام!")


if __name__ == "__main__":
    main()
