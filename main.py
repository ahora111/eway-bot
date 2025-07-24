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

WC_API_URL = os.environ.get("WC_API_URL")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

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
        response = session.get(SOURCE_CATS_API_URL)
        if response.status_code != 200:
            print(f"❌ خطای وضعیت {response.status_code} در دریافت دسته‌بندی‌ها.")
            print("پاسخ سرور:", response.text[:500])
            response.raise_for_status()
        print("✅ HTML دسته‌بندی‌ها با موفقیت دریافت شد.")
        
        soup = BeautifulSoup(response.text, 'lxml')
        all_menu_items = soup.select("li[id^='menu-item-']")
        
        if not all_menu_items:
            print("❌ هیچ آیتم دسته‌بندی در HTML پیدا نشد.")
            return []
            
        print(f"🔎 تعداد {len(all_menu_items)} آیتم منو پیدا شد. در حال پردازش...")
        
        cats_map = {}
        for item in all_menu_items:
            cat_id_raw = item.get('id', '')
            match = re.search(r'(\d+)', cat_id_raw)
            if not match: continue
            cat_id = int(match.group(1))

            a_tag = item.find('a', recursive=False) or item.select_one("a")
            if not a_tag or not a_tag.get('href'): continue
            
            name = a_tag.text.strip()
            real_id_match = re.search(r'/Store/List/(\d+)', a_tag['href'])
            real_id = int(real_id_match.group(1)) if real_id_match else None

            if name and real_id:
                cats_map[cat_id] = {"id": real_id, "name": name, "parent_id": None, "menu_item_id": cat_id}

        for item in all_menu_items:
            cat_id_raw = item.get('id', '')
            match = re.search(r'(\d+)', cat_id_raw)
            if not match: continue
            cat_id = int(match.group(1))

            parent_li = item.find_parent("li", class_="menu-item-has-children")
            if parent_li:
                parent_id_raw = parent_li.get('id', '')
                parent_match = re.search(r'(\d+)', parent_id_raw)
                if parent_match:
                    parent_menu_id = int(parent_match.group(1))
                    if cat_id in cats_map and parent_menu_id in cats_map:
                        cats_map[cat_id]['parent_id'] = cats_map[parent_menu_id]['id']
        
        final_cats = list(cats_map.values())
        print(f"✅ تعداد {len(final_cats)} دسته‌بندی معتبر استخراج شد.")
        return final_cats
    except Exception as e:
        print(f"❌ خطای ناشناخته در پردازش دسته‌بندی‌ها: {e}")
        return None

def get_products_from_category_page(session, category_id):
    """محصولات را از صفحات HTML یک دسته‌بندی استخراج می‌کند و صفحه‌بندی را مدیریت می‌کند."""
    products = []
    page_num = 1
    
    while True:
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page_num)
        print(f"  - در حال دریافت محصولات از: {url}")
        
        try:
            response = session.get(url)
            if response.status_code != 200:
                print(f"    - خطای وضعیت {response.status_code}, توقف برای این دسته‌بندی.")
                break
                
            soup = BeautifulSoup(response.text, 'lxml')
            
            product_blocks = soup.select(".goods_item.goods-record")
            if not product_blocks:
                print("    - هیچ محصولی در این صفحه یافت نشد. پایان صفحه‌بندی برای این دسته.")
                break

            print(f"    - تعداد {len(product_blocks)} بلاک محصول پیدا شد.")
            new_products_found_in_page = 0
            
            for block in product_blocks:
                try:
                    is_available = 'noCount' not in block.get('class', [])
                    if not is_available:
                        continue

                    name_tag = block.select_one(".goods-record-title")
                    name = name_tag.text.strip() if name_tag else None
                    
                    price_tag = block.select_one(".goods-record-price")
                    price_text = "0"
                    if price_tag:
                        if price_tag.find('del'): price_tag.find('del').decompose()
                        price_text = price_tag.text.strip()
                    price = re.sub(r'[^\d]', '', price_text) or "0"
                    
                    img_tag = block.select_one("img.goods-record-image")
                    image_url = (img_tag['data-src'] if img_tag and 'data-src' in img_tag.attrs else "")
                    if image_url and not image_url.startswith('http'):
                        image_url = "https://staticcontent.eways.co" + image_url
                    
                    id_tag = block.select_one("a[data-productid]")
                    product_id = id_tag['data-productid'] if id_tag else None

                    stock_tag = block.select_one(".goods-record-count span")
                    stock = int(stock_tag.text.strip()) if stock_tag else 1
                    
                    if product_id and name and int(price) > 0:
                        products.append({
                            "id": product_id,
                            "name": name,
                            "price": price,
                            "stock": stock,
                            "image": image_url,
                            "category_id": category_id
                        })
                        new_products_found_in_page += 1
                except Exception as e:
                    print(f"      - خطا در پردازش یک بلاک محصول: {e}. رد شدن...")

            if new_products_found_in_page == 0 and page_num > 1:
                print("    - محصول جدیدی در این صفحه نبود، توقف.")
                break

            page_num += 1
            time.sleep(random.uniform(0.5, 1.5))

        except Exception as e:
            print(f"    - خطای کلی در پردازش صفحه محصولات: {e}")
            break
            
    return products
    
# ... (بقیه توابع مثل get_all_products, process_price, و ... از کد شما اینجا قرار میگیرند) ...
# من فقط تابع main را با چند اصلاح کوچک می‌آورم تا کامل باشد.
def get_all_products(session, categories):
    all_products = {}
    print("\n⏳ شروع فرآیند جمع‌آوری تمام محصولات از همه دسته‌بندی‌های انتخابی...")
    for category in tqdm(categories, desc="پردازش دسته‌بندی‌ها"):
        cat_id = category['id']
        cat_name = category['name']
        print(f"\nدر حال دریافت محصولات دسته‌بندی: '{cat_name}' (ID: {cat_id})")
        products_in_cat = get_products_from_category_page(session, cat_id)
        for product in products_in_cat:
            all_products[product['id']] = product # ذخیره با ID برای جلوگیری از تکرار
    print(f"\n✅ فرآیند جمع‌آوری کامل شد. تعداد کل محصولات یکتا و موجود: {len(all_products)}")
    return list(all_products.values())

def process_price(price_value):
    try:
        price_value = float(re.sub(r'[^\d.]', '', str(price_value)))
    except (ValueError, TypeError):
        return "0"
    price_value = price_value * 1000
    if price_value <= 1: return "0"
    elif price_value <= 7000000: new_price = price_value + 260000
    elif price_value <= 10000000: new_price = price_value * 1.035
    elif price_value <= 20000000: new_price = price_value * 1.025
    elif price_value <= 30000000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price, -4)))

def process_product(product, stats, category_mapping):
    product_name = product.get('name', 'بدون نام')
    wc_cat_id = category_mapping.get(product.get('category_id'))
    if not wc_cat_id:
        print(f"   - هشدار: برای محصول '{product_name}' دسته‌بندی معادل یافت نشد.")
        return
    wc_data = {
        "name": product_name, "type": "simple", "sku": f"EWAYS-{product.get('id')}",
        "regular_price": process_price(product.get('price', 0)),
        "categories": [{"id": wc_cat_id}],
        "images": [{"src": product.get("image")}] if product.get("image") else [],
        "stock_quantity": product.get('stock'), "manage_stock": True,
        "stock_status": "instock" if product.get('stock', 0) > 0 else "outofstock"
    }
    _send_to_woocommerce(wc_data['sku'], wc_data, stats)

def _send_to_woocommerce(sku, data, stats, retries=3):
    check_url = f"{WC_API_URL}/products?sku={sku}"
    for attempt in range(retries):
        try:
            auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
            r_check = requests.get(check_url, auth=auth, verify=False)
            r_check.raise_for_status()
            existing = r_check.json()
            if existing:
                product_id = existing[0]['id']
                res = requests.put(f"{WC_API_URL}/products/{product_id}", auth=auth, json=data, verify=False)
                if res.status_code == 200:
                    with stats['lock']: stats['updated'] += 1
                else: print(f"   ❌ خطا در آپدیت '{data['name']}'. Status: {res.status_code}")
            else:
                res = requests.post(f"{WC_API_URL}/products", auth=auth, json=data, verify=False)
                if res.status_code == 201:
                    with stats['lock']: stats['created'] += 1
                else: print(f"   ❌ خطا در ایجاد '{data['name']}'. Status: {res.status_code}")
            return
        except requests.exceptions.RequestException as e:
            print(f"   ❌ خطای شبکه: {e}")
            if attempt < retries - 1: time.sleep(5)
            else: print("   ❌ تلاش‌ها تمام شد.")

def process_product_wrapper(args):
    try: process_product(*args)
    except Exception as e: print(f"   ❌ خطای جدی در پردازش محصول: {e}")

# (توابع transfer_categories_to_wc و get_selected_categories_flexible و ... را اینجا کپی کنید)
# ...

def main():
    if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET, AUT_COOKIE_VALUE]):
        print("❌ یکی از متغیرهای محیطی ضروری (WC_* یا EWAYS_AUTH_TOKEN) تنظیم نشده است.")
        return

    session = get_session()
    source_categories = get_and_parse_categories(session)
    if not source_categories: return

    # اینجا باید تابع get_selected_categories_flexible را فراخوانی کنید
    # filtered_categories = get_selected_categories_flexible(source_categories)
    # برای تست، فعلا چند دسته را دستی انتخاب میکنیم
    filtered_categories = [c for c in source_categories if c['id'] in [4285, 16778]] # مثال: موبایل و لپتاپ
    print(f"\n✅ تست با دسته‌بندی‌های منتخب: {[c['name'] for c in filtered_categories]}")
    if not filtered_categories: return

    # اینجا باید تابع transfer_categories_to_wc را فراخوانی کنید
    # category_mapping = transfer_categories_to_wc(filtered_categories)
    # برای تست، یک نقشه موقت میسازیم
    category_mapping = {cat['id']: cat['id'] for cat in filtered_categories}
    print("⚠️ هشدار: از نقشه موقت برای دسته‌بندی‌ها استفاده می‌شود.")
    
    products = get_all_products(session, filtered_categories)
    if not products:
        print("❌ هیچ محصولی برای پردازش یافت نشد.")
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
