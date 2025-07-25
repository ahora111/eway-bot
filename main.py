import requests
import urllib3
import os
import re
import time
import sys
import random
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
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
    if not link:
        return None
    m = re.search(r'/Store/List/(\d+)', str(link))
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

    def recursive_extract(ul_tag, parent_id=None, level=0):
        categories = []
        for li in ul_tag.find_all('li', recursive=False):
            a = li.find('a', recursive=False)
            if a:
                name = a.get_text(strip=True)
                link = a.get('href')
                real_id = extract_real_id_from_link(link)
                print(f"DEBUG: name={name} | link={link} | real_id={real_id}")
                if not name or not link or not real_id:
                    continue
                cat = {
                    'id': real_id,
                    'name': name,
                    'link': link,
                    'parent_id': parent_id,
                    'level': level,
                    'children': []
                }
                flat_list.append(cat)
                sub_ul = li.find('ul', class_='sub-menu')
                if sub_ul:
                    cat['children'] = recursive_extract(sub_ul, parent_id=real_id, level=level+1)
                categories.append(cat)
        return categories

    all_categories = []
    for li in root_ul.find_all('li', recursive=False):
        a = li.find('a', recursive=False)
        if a:
            name = a.get_text(strip=True)
            link = a.get('href')
            real_id = extract_real_id_from_link(link)
            print(f"DEBUG: name={name} | link={link} | real_id={real_id}")
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
            sub_ul = li.find('ul', class_='sub-menu')
            if sub_ul:
                cat['children'] = recursive_extract(sub_ul, parent_id=real_id, level=1)
            all_categories.append(cat)
    return flat_list, all_categories
    
def get_and_parse_categories(session):
    print(f"⏳ دریافت دسته‌بندی‌ها از: {SOURCE_CATS_API_URL}")
    response = session.get(SOURCE_CATS_API_URL)
    if response.status_code != 200:
        print(f"❌ خطای وضعیت {response.status_code} در دریافت دسته‌بندی‌ها.")
        print("پاسخ سرور:", response.text[:500])
        response.raise_for_status()
    print("✅ HTML دسته‌بندی‌ها با موفقیت دریافت شد.")
    flat_list, all_categories = extract_categories_from_html(response.text)
    if not flat_list:
        print("❌ هیچ دسته‌بندی معتبری پیدا نشد!")
    else:
        print(f"✅ تعداد {len(flat_list)} دسته‌بندی استخراج شد.")
    return flat_list, all_categories

def get_selected_categories_flexible(source_categories):
    def print_tree(categories, all_cats, indent=0):
        for cat in categories:
            print('  ' * indent + f"[{cat['id']}] {cat['name']}")
            children = [c for c in all_cats if c.get('parent_id') == cat['id']]
            if children:
                print_tree(children, all_cats, indent+1)

    roots = [cat for cat in source_categories if not cat.get('parent_id')]
    print("\nلیست دسته‌بندی‌ها (ساختار درختی):\n")
    print_tree(roots, source_categories)

    selected_env = os.environ.get("SELECTED_CATEGORIES")
    if selected_env:
        selected_raw = [x.strip() for x in selected_env.split(",") if x.strip()]
        print(f"\n✅ دسته‌بندی‌های انتخاب‌شده از متغیر محیطی: {selected_raw}")
    elif os.path.exists("selected_categories.txt"):
        with open("selected_categories.txt") as f:
            selected_raw = [x.strip() for x in f.read().strip().split(",") if x.strip()]
        print(f"\n✅ دسته‌بندی‌های انتخاب‌شده از فایل: {selected_raw}")
    elif sys.stdin.isatty():
        print("\nلطفاً نام یا ID واقعی دسته‌بندی‌هایی که می‌خواهید منتقل شوند را با کاما جدا وارد کنید (مثلاً: 4949,گوشی موبایل,344):")
        selected_raw = input("نام یا ID ها: ").strip().split(",")
        selected_raw = [x.strip() for x in selected_raw if x.strip()]
    else:
        print("❌ هیچ ورودی معتبری برای انتخاب دسته‌بندی پیدا نشد (نه متغیر محیطی، نه فایل، نه محیط تعاملی). برنامه خاتمه می‌یابد.")
        exit(1)

    if not selected_raw:
        print("❌ هیچ دسته‌بندی انتخاب نشد. برنامه خاتمه می‌یابد.")
        exit(1)

    def collect_with_children(cat, all_cats, result):
        result.add(cat['id'])
        for c in all_cats:
            if c.get('parent_id') == cat['id']:
                collect_with_children(c, all_cats, result)

    final_ids = set()
    for item in selected_raw:
        if item.isdigit():
            matched = [cat for cat in source_categories if cat['id'] == int(item)]
        else:
            matched = [cat for cat in source_categories if cat['name'] == item]
        if not matched:
            print(f"⚠️ هشدار: هیچ دسته‌ای با '{item}' پیدا نشد.")
        for cat in matched:
            collect_with_children(cat, source_categories, final_ids)
    filtered_cats = [cat for cat in source_categories if cat['id'] in final_ids]
    print(f"\n✅ تعداد {len(filtered_cats)} دسته‌بندی برای انتقال انتخاب شد.")
    return filtered_cats

def sort_cats_for_creation(flat_cats):
    sorted_cats = []
    id_to_cat = {cat["id"]: cat for cat in flat_cats}
    visited = set()
    def visit(cat):
        if cat["id"] in visited:
            return
        parent_id = cat.get("parent_id")
        if parent_id and parent_id in id_to_cat:
            visit(id_to_cat[parent_id])
        sorted_cats.append(cat)
        visited.add(cat["id"])
    for cat in flat_cats:
        visit(cat)
    return sorted_cats

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

def get_products_from_category_page(session, category_id):
    products = []
    page_num = 1
    while True:
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page_num)
        print(f"  - در حال دریافت محصولات از: {url}")
        response = session.get(url)
        if response.status_code != 200:
            print(f"    - خطای وضعیت {response.status_code}, توقف برای این دسته‌بندی.")
            break
        soup = BeautifulSoup(response.text, 'lxml')
        product_blocks = soup.select(".goods_item.goods-record")
        if not product_blocks:
            print("    - هیچ محصولی در این صفحه یافت نشد. پایان صفحه‌بندی برای این دسته.")
            break
        for block in product_blocks:
            is_available = 'noCount' not in block.get('class', [])
            if not is_available:
                continue
            name_tag = block.select_one(".goods-record-title")
            name = name_tag.text.strip() if name_tag else None
            price_tag = block.select_one(".goods-record-price")
            price_text = price_tag.text.strip() if price_tag else "0"
            price = re.sub(r'[^\d]', '', price_text)
            if not price: price = "0"
            img_tag = block.select_one("img.goods-record-image")
            image_url = ""
            if img_tag and 'data-src' in img_tag.attrs:
                image_url = img_tag['data-src']
                if not image_url.startswith('http'):
                    image_url = "https://staticcontent.eways.co" + image_url
            id_tag = block.select_one("a[data-productid]")
            product_id = id_tag['data-productid'] if id_tag else None
            stock = 1
            if product_id and name and int(price) > 0 and is_available:
                products.append({
                    "id": product_id,
                    "name": name,
                    "price": price.replace(",", ""),
                    "stock": stock,
                    "image": image_url,
                    "category_id": category_id
                })
        page_num += 1
        time.sleep(random.uniform(0.5, 1.5))
    return products

def get_all_products(session, categories):
    all_products = {}
    print("\n⏳ شروع فرآیند جمع‌آوری تمام محصولات از همه دسته‌بندی‌های انتخابی...")
    for category in tqdm(categories, desc="پردازش دسته‌بندی‌ها"):
        products_in_cat = get_products_from_category_page(session, category['id'])
        for product in products_in_cat:
            all_products[product['id']] = product
    print(f"\n✅ فرآیند جمع‌آوری کامل شد. تعداد کل محصولات یکتا و موجود: {len(all_products)}")
    return list(all_products.values())

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
    print("- متغیر محیطی SELECTED_CATEGORIES (مثلاً: 4949,گوشی موبایل,344)")
    print("- فایل selected_categories.txt (مثلاً: 4949,گوشی موبایل,344)")
    print("- یا در محیط تعاملی، به صورت دستی وارد کنید.\n")

    if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET, AUT_COOKIE_VALUE]):
        print("❌ یکی از متغیرهای محیطی ضروری (WC_* یا EWAYS_AUTH_TOKEN) تنظیم نشده است.")
        return

    session = get_session()
    source_categories, all_categories_tree = get_and_parse_categories(session)
    if not source_categories:
        print("❌ هیچ دسته‌بندی دریافت نشد. برنامه خاتمه می‌یابد.")
        return

    filtered_categories = get_selected_categories_flexible(source_categories)
    if not filtered_categories:
        print("❌ هیچ دسته‌بندی برای انتقال انتخاب نشد. برنامه خاتمه می‌یابد.")
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
