import os
import requests
from bs4 import BeautifulSoup
from collections import defaultdict
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
    # توضیحات (در div با کلاس خاص یا meta description)
    desc = ''
    desc_tag = soup.find('div', class_='product-desc')
    if desc_tag:
        desc = desc_tag.text.strip()
    else:
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc:
            desc = meta_desc['content'].strip()
    # تصویر اصلی (اولین img با src که به ProductPictures اشاره دارد)
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

def create_wc_category(cat_name, wc_cats_map):
    data = {
        "name": cat_name.strip(),
        "slug": cat_name.strip().replace(' ', '-')
    }
    res = requests.post(
        f"{WC_API_URL}/products/categories",
        auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET),
        json=data,
        verify=False
    )
    if res.status_code in [200, 201]:
        new_id = res.json()["id"]
        print(f"✅ دسته‌بندی '{cat_name}' ساخته شد. (id={new_id})")
        return new_id
    else:
        print(f"❌ خطا در ساخت دسته‌بندی '{cat_name}': {res.text}")
        return None

# ---------- ووکامرس: ارسال محصول ----------
def send_product_to_woocommerce(product, wc_cat_id):
    data = {
        "name": product['name'],
        "type": "simple",
        "sku": f"EWAYS-{product['id']}",
        "regular_price": product['price'],
        "categories": [{"id": wc_cat_id}],
        "images": [{"src": product['image']}],
        "stock_status": "instock" if int(product['stock']) > 0 else "outofstock",
        "description": product.get('description', '')
    }
    res = requests.post(
        f"{WC_API_URL}/products",
        auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET),
        json=data,
        verify=False
    )
    if res.status_code in [200, 201]:
        print(f"✅ محصول '{product['name']}' ثبت شد.")
        return True
    else:
        print(f"❌ خطا در ثبت محصول '{product['name']}': {res.text}")
        return False

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
            new_id = create_wc_category(cat, wc_cats_map)
            if new_id:
                cat_name_to_id[cat] = new_id

    # 4. استخراج توضیحات و تصویر اصلی هر محصول از HTML جزئیات
    success, fail = 0, 0
    for idx, p in enumerate(products, 1):
        detail_file = f"details/{p['id']}.html"
        if os.path.exists(detail_file):
            with open(detail_file, 'r', encoding='utf-8') as f:
                detail_html = f.read()
            detail_info = parse_product_detail(detail_html)
            if detail_info['description']:
                p['description'] = detail_info['description']
            if detail_info['image']:
                p['image'] = detail_info['image']
        else:
            print(f"⚠️ فایل جزئیات برای محصول {p['id']} وجود ندارد. (فقط اطلاعات لیست ثبت می‌شود)")

        wc_cat_id = cat_name_to_id.get(p['category'], None)
        if wc_cat_id:
            ok = send_product_to_woocommerce(p, wc_cat_id)
            if ok:
                success += 1
            else:
                fail += 1
        else:
            print(f"❌ دسته‌بندی ووکامرس برای '{p['category']}' یافت نشد.")
            fail += 1
        time.sleep(0.5)  # برای جلوگیری از بلاک شدن توسط سرور

    # 5. آمار نهایی
    print("\n===============================")
    print(f"📦 تعداد کل محصولات پردازش شده: {len(products)}")
    print(f"🟢 محصولات موفق ثبت شده: {success}")
    print(f"🔴 محصولات ناموفق: {fail}")
    print(f"🔸 تعداد کل دسته‌بندی‌ها: {len(cat_name_to_id)}")
    print("===============================")
    print("تمام محصولات پردازش شدند. فرآیند به پایان رسید.")

if __name__ == "__main__":
    main()
