import os
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
import time

# --- اطلاعات ووکامرس ---
WC_API_URL = os.environ.get("WC_API_URL")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

# ---------- استخراج محصولات و دسته‌بندی‌ها از HTML لیست ----------
def parse_html_products(html):
    soup = BeautifulSoup(html, 'html.parser')
    products = []
    current_category = None

    for tag in soup.find_all(['div'], class_=['cat-brand', 'cat-body']):
        if 'cat-brand' in tag.get('class', []):
            current_category = tag.find('b').text.strip()
        elif 'cat-body' in tag.get('class', []):
            try:
                img = tag.find('img')['src']
                name = tag.find('div', class_='goods-item-title').text.strip()
                price = tag.find('span', class_='price').text.strip().replace(',', '')
                stock = tag.find_all('div', class_='col-lg-1 text-center col-xs-6')[-1].text.strip()
                productid = tag.find('a', attrs={'data-productid': True})['data-productid']
                detail_link = tag.find('div', class_='goods-item-title').find('a')['href']
            except Exception as e:
                print(f"❌ خطا در استخراج محصول: {e}")
                continue

            products.append({
                'id': productid,
                'name': name,
                'category': current_category,
                'price': price,
                'stock': stock,
                'image': img,
                'detail_link': detail_link
            })
    return products

# ---------- استخراج توضیحات و تصویر اصلی از HTML جزئیات ----------
def parse_product_detail(html):
    soup = BeautifulSoup(html, 'html.parser')
    desc = ''
    desc_tag = soup.find('div', class_='product-desc')
    if desc_tag:
        desc = desc_tag.text.strip()
    else:
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc:
            desc = meta_desc['content'].strip()
    img = ''
    img_tag = soup.find('img', src=lambda x: x and 'ProductPictures' in x)
    if img_tag:
        img = img_tag['src']
    return {
        'description': desc,
        'image': img
    }

# ---------- ووکامرس: دریافت و ساخت دسته‌بندی ----------
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

def create_wc_category(cat, wc_cats_map, source_to_wc_id_map):
    data = {
        "name": cat.strip(),
        "slug": cat.strip().replace(' ', '-')
    }
    res = requests.post(
        f"{WC_API_URL}/products/categories",
        auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET),
        json=data,
        verify=False
    )
    if res.status_code in [200, 201]:
        new_id = res.json()["id"]
        print(f"✅ دسته‌بندی '{cat}' ساخته شد. (id={new_id})")
        return new_id
    else:
        print(f"❌ خطا در ساخت دسته‌بندی '{cat}': {res.text}")
        return None

# ---------- اعتبارسنجی محصول ----------
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

# ---------- ارسال به ووکامرس (ایجاد یا آپدیت) ----------
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

# ---------- پردازش و ارسال هر محصول ----------
def process_product(product, stats, category_mapping):
    product_name = product.get('name', 'بدون نام')
    product_id = product.get('id', '')
    category = product.get('category', '')
    wc_cat_id = category_mapping.get(category)
    if not wc_cat_id:
        print(f"❌ دسته‌بندی ووکامرس برای '{category}' یافت نشد.")
        return

    # توضیحات و تصویر از صفحه جزئیات
    detail_file = f"details/{product_id}.html"
    description = ''
    image = product['image']
    if os.path.exists(detail_file):
        with open(detail_file, 'r', encoding='utf-8') as f:
            detail_html = f.read()
        detail_info = parse_product_detail(detail_html)
        if detail_info['description']:
            description = detail_info['description']
        if detail_info['image']:
            image = detail_info['image']

    wc_data = {
        "name": product_name,
        "type": "simple",
        "sku": f"EWAYS-{product_id}",
        "regular_price": product.get('price', '0'),
        "description": description,
        "categories": [{"id": wc_cat_id}],
        "images": [{"src": image}],
        "stock_status": "instock" if int(product.get('stock', '0')) > 0 else "outofstock"
    }
    errors = validate_product(wc_data)
    if errors:
        print(f"   ❌ محصول '{wc_data.get('name', '')}' اعتبارسنجی نشد:")
        for err in errors:
            print(f"      - {err}")
        return
    _send_to_woocommerce(wc_data['sku'], wc_data, stats)

def process_product_wrapper(args):
    product, stats, category_mapping = args
    try:
        process_product(product, stats, category_mapping)
    except Exception as e:
        print(f"   ❌ خطا در پردازش محصول {product.get('id', '')}: {e}")

# ---------- اجرای اصلی ----------
def main():
    # 1. خواندن HTML لیست محصولات
    with open('categorylist.html', 'r', encoding='utf-8') as f:
        html = f.read()
    products = parse_html_products(html)
    print(f"\n🔹 تعداد کل محصولات استخراج شده: {len(products)}")

    # 2. استخراج دسته‌بندی‌ها
    categories = set(p['category'] for p in products)
    print(f"🔹 تعداد دسته‌بندی یکتا: {len(categories)}")
    print("دسته‌بندی‌ها:", ', '.join(categories))

    # 3. دریافت دسته‌بندی‌های ووکامرس و ساخت دسته جدید در صورت نیاز
    wc_cats = get_wc_categories()
    wc_cats_map = build_wc_cats_map(wc_cats)
    cat_name_to_id = {}
    for cat in categories:
        if cat in wc_cats_map:
            cat_name_to_id[cat] = wc_cats_map[cat]
        else:
            new_id = create_wc_category(cat, wc_cats_map, cat_name_to_id)
            if new_id:
                cat_name_to_id[cat] = new_id

    print(f"🔹 تعداد کل دسته‌بندی‌های نهایی: {len(cat_name_to_id)}")

    # 4. ارسال محصولات به ووکامرس (موازی)
    stats = {'created': 0, 'updated': 0}
    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(process_product_wrapper, [(p, stats, cat_name_to_id) for p in products]))

    # 5. آمار نهایی
    print("\n===============================")
    print(f"📦 تعداد کل محصولات پردازش شده: {len(products)}")
    print(f"🟢 محصولات جدید ایجاد شده: {stats['created']}")
    print(f"🔵 محصولات آپدیت شده: {stats['updated']}")
    print(f"🔸 تعداد کل دسته‌بندی‌ها: {len(cat_name_to_id)}")
    print("===============================")
    print("تمام محصولات پردازش شدند. فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
