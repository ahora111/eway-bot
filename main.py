import requests
import urllib3
import os
import re
import time
import sys
import random
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from threading import Lock

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

WC_API_URL = os.environ.get("WC_API_URL")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")
BASE_URL = "https://panel.eways.co"
AUT_COOKIE_VALUE = os.environ.get("EWAYS_AUTH_TOKEN")
SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"
PRODUCT_LIST_URL_TEMPLATE = f"{BASE_URL}/Store/List/{{category_id}}/2/2/0/0/0/10000000000?page={{page}}"

def get_session():
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

def extract_real_id_from_link(link):
    m = re.search(r'/store/(?:list|categorylist)/(\d+)', str(link), re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None

def extract_categories_from_html(html):
    soup = BeautifulSoup(html, 'lxml')
    root_ul = soup.find('ul', id='kanivmm-menu-id')
    if not root_ul:
        root_ul = soup.find('ul', class_='kanivmm-menu-class')
    if not root_ul:
        print("❌ منوی دسته‌بندی در HTML پیدا نشد!")
        return []

    flat_list = []
    for li in root_ul.find_all('li', recursive=False):
        a = li.find('a', recursive=False)
        if a:
            name = a.get_text(strip=True)
            link = a.get('href')
            real_id = extract_real_id_from_link(link)
            if not name or not link or not real_id:
                continue
            cat = {
                'id': real_id,
                'name': name,
                'link': link,
                'parent_id': None,
                'level': 0,
                'children': []
            }
            flat_list.append(cat)
    return flat_list

def get_products_from_category_page(session, category_id):
    all_products_in_category = []
    seen_product_ids = set()
    page_num = 1
    MAX_PAGES = 50  # حداکثر تعداد صفحات

    while page_num <= MAX_PAGES:
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page_num)
        try:
            response = session.get(url, timeout=30)
            if response.status_code != 200: break
            soup = BeautifulSoup(response.text, 'lxml')
            product_blocks = soup.select(".goods_item.goods-record")
            if not product_blocks:
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
                            "id": product_id,
                            "name": name,
                            "price": price,
                            "stock": stock,
                            "image": image_url,
                            "category_id": category_id
                        })
                except Exception as e:
                    print(f"      - خطا در پردازش یک بلاک محصول: {e}. رد شدن...")
            if not current_page_product_ids:
                break
            page_num += 1
            time.sleep(random.uniform(0.5, 1.5))
        except Exception as e:
            print(f"    - خطای کلی در پردازش صفحه محصولات: {e}")
            break
    return all_products_in_category

def sort_cats_for_creation(flat_cats):
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

def main():
    print("برای انتخاب دسته‌بندی‌ها می‌توانید یکی از این روش‌ها را استفاده کنید:")
    print("- متغیر محیطی SELECTED_CATEGORIES (مثلاً: 4285,لیستموبایل,16778)")
    print("- فایل selected_categories.txt (مثلاً: 4285,لیستموبایل,16778)")
    print("- یا در محیط تعاملی، به صورت دستی وارد کنید.\n")

    if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET, AUT_COOKIE_VALUE]):
        print("❌ یکی از متغیرهای محیطی ضروری (WC_* یا EWAYS_AUTH_TOKEN) تنظیم نشده است.")
        return

    session = get_session()
    main_menu_html = session.get(SOURCE_CATS_API_URL).text
    main_cats = extract_categories_from_html(main_menu_html)
    if not main_cats:
        print("❌ هیچ دسته‌بندی اصلی پیدا نشد.")
        return

    # چاپ دسته‌بندی‌های اصلی برای انتخاب راحت‌تر
    print("\nدسته‌بندی‌های اصلی موجود:")
    for cat in main_cats:
        print(f"[{cat['id']}] {cat['name']}")

    selected_env = os.environ.get("SELECTED_CATEGORIES")
    if selected_env:
        selected_raw = [x.strip() for x in selected_env.split(",") if x.strip()]
    elif os.path.exists("selected_categories.txt"):
        with open("selected_categories.txt") as f:
            selected_raw = [x.strip() for x in f.read().strip().split(",") if x.strip()]
    elif sys.stdin.isatty():
        print("\nلطفاً نام یا ID واقعی دسته‌بندی‌های اصلی را وارد کنید (مثلاً: 4285,لیستموبایل,16778):")
        selected_raw = input("نام یا ID ها: ").strip().split(",")
        selected_raw = [x.strip() for x in selected_raw if x.strip()]
    else:
        print("❌ هیچ ورودی معتبری برای انتخاب دسته‌بندی پیدا نشد.")
        return

    selected_ids = set()
    for item in selected_raw:
        if item.isdigit():
            matched = [cat for cat in main_cats if cat['id'] == int(item)]
        else:
            matched = [cat for cat in main_cats if cat['name'] == item]
        for cat in matched:
            selected_ids.add(cat['id'])
    if not selected_ids:
        print("❌ هیچ دسته‌بندی اصلی انتخاب نشد.")
        return

    print(f"\n✅ دسته‌بندی‌های انتخاب‌شده: {selected_ids}")

    filtered_categories = [cat for cat in main_cats if cat['id'] in selected_ids]
    category_mapping = transfer_categories_to_wc(filtered_categories)
    if not category_mapping:
        print("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد.")
        return

    products = get_all_products(session, filtered_categories)
    if not products:
        print("✅ هیچ محصولی برای پردازش یافت نشد.")
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
