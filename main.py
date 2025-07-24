import requests
import urllib3
import os
import re
import time
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from threading import Lock
import sys

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

def extract_categories_from_html(html):
    soup = BeautifulSoup(html, 'lxml')
    root_ul = soup.find('ul', id='kanivmm-menu-id')
    if not root_ul:
        root_ul = soup.find('ul', class_='kanivmm-menu-class')
    if not root_ul:
        print("❌ منوی دسته‌بندی در HTML پیدا نشد!")
        return []

    flat_list = []
    id_counter = [1]
    seen = set()

    def recursive_extract(ul_tag, parent_id=None, level=0):
        categories = []
        for li in ul_tag.find_all('li', recursive=False):
            a = li.find('a', recursive=False)
            if a:
                name = a.get_text(strip=True)
                link = a.get('href')
                if not name or not link or (name, link) in seen:
                    continue
                seen.add((name, link))
                cat_id = id_counter[0]
                id_counter[0] += 1
                cat = {
                    'id': cat_id,
                    'name': name,
                    'link': link,
                    'parent_id': parent_id,
                    'level': level,
                    'children': []
                }
                flat_list.append(cat)
                sub_ul = li.find('ul', class_='sub-menu')
                if sub_ul:
                    cat['children'] = recursive_extract(sub_ul, parent_id=cat_id, level=level+1)
                categories.append(cat)
        return categories

    all_categories = []
    for li in root_ul.find_all('li', recursive=False):
        a = li.find('a', recursive=False)
        if a:
            name = a.get_text(strip=True)
            link = a.get('href')
            if not name or not link or (name, link) in seen:
                continue
            seen.add((name, link))
            cat_id = id_counter[0]
            id_counter[0] += 1
            cat = {
                'id': cat_id,
                'name': name,
                'link': link,
                'parent_id': None,
                'level': 0,
                'children': []
            }
            flat_list.append(cat)
            sub_ul = li.find('ul', class_='sub-menu')
            if sub_ul:
                cat['children'] = recursive_extract(sub_ul, parent_id=cat_id, level=1)
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

def print_categories_tree(categories, all_cats, indent=0):
    for cat in categories:
        print('  ' * indent + f"[{cat['id']}] {cat['name']}")
        children = [c for c in all_cats if c.get('parent_id') == cat['id']]
        if children:
            print_categories_tree(children, all_cats, indent+1)

def get_selected_categories(source_categories):
    # 1. ورودی از متغیر محیطی
    selected_ids_env = os.environ.get("SELECTED_CATEGORY_IDS")
    if selected_ids_env:
        selected_ids = [int(x) for x in selected_ids_env.split(",") if x.strip().isdigit()]
        print(f"\n✅ دسته‌بندی‌های انتخاب‌شده از متغیر محیطی: {selected_ids}")

    # 2. ورودی از فایل متنی
    elif os.path.exists("selected_ids.txt"):
        with open("selected_ids.txt") as f:
            selected_ids = [int(x) for x in f.read().strip().split(",") if x.strip().isdigit()]
        print(f"\n✅ دسته‌بندی‌های انتخاب‌شده از فایل: {selected_ids}")

    # 3. محیط تعاملی (فقط اگر stdin باز باشد)
    elif sys.stdin.isatty():
        roots = [cat for cat in source_categories if not cat.get('parent_id')]
        print("\nلیست دسته‌بندی‌ها (ساختار درختی):\n")
        print_categories_tree(roots, source_categories)
        print("\nلطفاً ID دسته‌بندی‌هایی که می‌خواهید منتقل شوند را با کاما جدا وارد کنید (مثلاً: 12,15,22):")
        selected_ids = input("ID ها: ").strip()
        selected_ids = [int(x) for x in selected_ids.split(",") if x.strip().isdigit()]

    # 4. هیچ ورودی معتبری نبود
    else:
        print("❌ هیچ ورودی معتبری برای انتخاب دسته‌بندی پیدا نشد (نه متغیر محیطی، نه فایل، نه محیط تعاملی). برنامه خاتمه می‌یابد.")
        exit(1)

    if not selected_ids:
        print("❌ هیچ دسته‌بندی انتخاب نشد. برنامه خاتمه می‌یابد.")
        exit(1)

    # جمع‌آوری همه زیرمجموعه‌ها
    def collect_with_children(cat_id, all_cats, result):
        result.add(cat_id)
        for c in all_cats:
            if c.get('parent_id') == cat_id:
                collect_with_children(c['id'], all_cats, result)
    final_ids = set()
    for cid in selected_ids:
        collect_with_children(cid, source_categories, final_ids)
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
    print("⏳ انتقال دسته‌بندی‌های تو در تو به ووکامرس...")
    wc_cats = get_wc_categories()
    wc_cats_map = {cat["name"].strip(): cat["id"] for cat in wc_cats}
    source_to_wc_id_map = {}
    sorted_cats = sort_cats_for_creation(source_categories)
    for cat in sorted_cats:
        name = cat["name"].strip()
        if name in wc_cats_map:
            wc_id = wc_cats_map[name]
            print(f"⏩ دسته‌بندی '{name}' قبلاً وجود دارد. (id={wc_id})")
            source_to_wc_id_map[cat["id"]] = wc_id
        else:
            data = {
                "name": name,
                "slug": name.replace(' ', '-'),
                "description": "",
            }
            parent_id = cat.get("parent_id")
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
                print(f"✅ دسته‌بندی '{name}' ساخته شد. (id={new_id})")
                source_to_wc_id_map[cat["id"]] = new_id
            else:
                print(f"❌ خطا در ساخت دسته‌بندی '{name}': {res.text}")
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
            if product_id and name and int(price) > 0:
                products.append({
                    "id": product_id,
                    "name": name,
                    "price": price.replace(",", ""),
                    "stock": stock,
                    "image": image_url,
                    "category_id": category_id
                })
        page_num += 1
        time.sleep(1)
    return products

def get_all_products(session, categories):
    all_products = {}
    print("\n⏳ شروع فرآیند جمع‌آوری تمام محصولات از همه دسته‌بندی‌های انتخابی...")
    for category in tqdm(categories, desc="پردازش دسته‌بندی‌ها"):
        cat_id = category['id']
        cat_name = category['name']
        print(f"\nدر حال دریافت محصولات دسته‌بندی: '{cat_name}' (ID: {cat_id})")
        products_in_cat = get_products_from_category_page(session, cat_id)
        for product in products_in_cat:
            if product['stock'] > 0 and int(product['price']) > 0:
                 all_products[product['id']] = product
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
    product_id_source = product.get('id', '')
    wc_cat_id = category_mapping.get(product.get('category_id'))
    if not wc_cat_id:
        print(f"   - هشدار: برای محصول '{product_name}' دسته‌بندی معادل یافت نشد.")
        return
    wc_data = {
        "name": product_name,
        "type": "simple",
        "sku": f"EWAYS-{product_id_source}",
        "regular_price": process_price(product.get('price', 0)),
        "description": "",
        "short_description": "",
        "categories": [{"id": wc_cat_id}],
        "images": [{"src": product.get("image")}] if product.get("image") else [],
        "stock_quantity": product.get('stock'),
        "manage_stock": True,
    }
    if int(wc_data.get("stock_quantity", 0)) > 0:
        wc_data["stock_status"] = "instock"
    else:
        wc_data["stock_status"] = "outofstock"
    _send_to_woocommerce(wc_data['sku'], wc_data, stats)

def _send_to_woocommerce(sku, data, stats, retries=3):
    check_url = f"{WC_API_URL}/products?sku={sku}"
    for attempt in range(retries):
        try:
            auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
            r_check = requests.get(check_url, auth=auth, verify=False)
            r_check.raise_for_status()
            existing_products = r_check.json()
            if existing_products:
                product_id = existing_products[0]['id']
                update_url = f"{WC_API_URL}/products/{product_id}"
                res = requests.put(update_url, auth=auth, json=data, verify=False)
                if res.status_code == 200:
                    with stats['lock']: stats['updated'] += 1
                    print(f"   ✅ محصول '{data['name']}' با موفقیت آپدیت شد.")
                else:
                    print(f"   ❌ خطا در آپدیت محصول '{data['name']}'. Status: {res.status_code}, Response: {res.text}")
            else:
                create_url = f"{WC_API_URL}/products"
                res = requests.post(create_url, auth=auth, json=data, verify=False)
                if res.status_code == 201:
                    with stats['lock']: stats['created'] += 1
                    print(f"   ✅ محصول '{data['name']}' با موفقیت ایجاد شد.")
                else:
                    print(f"   ❌ خطا در ایجاد محصول '{data['name']}'. Status: {res.status_code}, Response: {res.text}")
            return
        except requests.exceptions.RequestException as e:
            print(f"   ❌ خطای شبکه در ارتباط با ووکامرس: {e}")
            if attempt < retries - 1:
                print(f"   ⏳ تلاش مجدد پس از 5 ثانیه...")
                time.sleep(5)
            else:
                print("   ❌ تلاش‌ها به پایان رسید.")
                break

def process_product_wrapper(args):
    product, stats, category_mapping = args
    try:
        process_product(product, stats, category_mapping)
    except Exception as e:
        print(f"   ❌ خطای جدی در پردازش محصول {product.get('id', '')}: {e}")

def main():
    print("برای انتخاب دسته‌بندی‌ها می‌توانید یکی از این روش‌ها را استفاده کنید:")
    print("- متغیر محیطی SELECTED_CATEGORY_IDS (مثلاً: 2,6,129)")
    print("- فایل selected_ids.txt (مثلاً: 2,6,129)")
    print("- یا در محیط تعاملی، به صورت دستی وارد کنید.\n")

    if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET]):
        print("❌ متغیرهای محیطی ووکامرس (WC_*) به درستی تنظیم نشده‌اند. برنامه متوقف می‌شود.")
        return
    if not AUT_COOKIE_VALUE:
        print("❌ متغیر محیطی کوکی (EWAYS_AUTH_TOKEN) تنظیم نشده است. برنامه متوقف می‌شود.")
        return

    session = get_session()
    source_categories, all_categories_tree = get_and_parse_categories(session)
    if not source_categories:
        print("❌ هیچ دسته‌بندی دریافت نشد. برنامه خاتمه می‌یابد.")
        return

    filtered_categories = get_selected_categories(source_categories)
    if not filtered_categories:
        print("❌ هیچ دسته‌بندی برای انتقال انتخاب نشد. برنامه خاتمه می‌یابد.")
        return

    category_mapping = transfer_categories_to_wc(filtered_categories)
    if not category_mapping:
        print("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد. برنامه خاتمه می‌یابد.")
        return

    products = get_all_products(session, filtered_categories)
    if not products:
        print("❌ هیچ محصولی برای پردازش یافت نشد. برنامه خاتمه می‌یابد.")
        return

    stats = {'created': 0, 'updated': 0, 'lock': Lock()}
    total = len(products)
    print(f"\n🚀 شروع پردازش و ارسال {total} محصول به ووکامرس...")

    with ThreadPoolExecutor(max_workers=5) as executor:
        args_list = [(p, stats, category_mapping) for p in products]
        list(tqdm(executor.map(process_product_wrapper, args_list), total=total, desc="ارسال محصولات", unit="محصول"))

    print("\n===============================")
    print(f"📦 تعداد کل محصولات پردازش شده: {total}")
    print(f"🟢 محصولات جدید ایجاد شده: {stats['created']}")
    print(f"🔵 محصولات آپدیت شده: {stats['updated']}")
    print("===============================")
    print("تمام! فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
