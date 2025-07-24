import requests
import urllib3
import os
import re
import time
import json
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from threading import Lock

# ==============================================================================
# --- غیرفعال کردن هشدار SSL ---
# ==============================================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# --- اطلاعات ووکامرس (خوانده شده از Secrets گیت‌هاب) ---
# ==============================================================================
WC_API_URL = os.environ.get("WC_API_URL")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

# ==============================================================================
# --- اطلاعات API سایت هدف (Eways.co) ---
# ==============================================================================
BASE_URL = "https://panel.eways.co"
# مقدار کوکی 'Aut' که باید از مرورگر یا Secrets خوانده شود
AUT_COOKIE_VALUE = os.environ.get("EWAYS_AUTH_TOKEN")
SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"
# الگو برای URL صفحه محصولات یک دسته‌بندی
PRODUCT_LIST_URL_TEMPLATE = f"{BASE_URL}/Store/List/{{category_id}}/2/2/0/0/0/10000000000?page={{page}}"

# ==============================================================================
# --- بخش ارتباط با سایت مبدا (Eways.co) ---
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
    """دسته‌بندی‌ها را از API دریافت و با BeautifulSoup تجزیه می‌کند."""
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

            name_tag = item.find('a', recursive=False) or item.select_one("a")
            name = name_tag.text.strip() if name_tag else f"بدون نام {cat_id}"

            if name and name != "#": # آیتم‌های خالی را نادیده بگیر
                cats_map[cat_id] = {"id": cat_id, "name": name, "parent_id": None}

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
                    parent_id = int(parent_match.group(1))
                    if cat_id in cats_map:
                        cats_map[cat_id]['parent_id'] = parent_id

        # حذف دسته‌بندی‌های سطح بالا و بی‌نام که نقش کانتینر دارند
        final_cats = [cat for cat in cats_map.values() if cat['name'] and not re.match(r'^menu-item-\d+$', cat['name'], re.IGNORECASE)]
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
            
            product_blocks = soup.select(".cat-body.row")
            if not product_blocks:
                print("    - هیچ محصولی در این صفحه یافت نشد. پایان صفحه‌بندی برای این دسته.")
                break

            print(f"    - تعداد {len(product_blocks)} بلاک محصول پیدا شد.")
            new_products_found = 0
            for block in product_blocks:
                name_tag = block.select_one(".goods-item-title a span")
                name = name_tag.text.strip() if name_tag else None
                
                price_tag = block.select_one(".price")
                price_text = "0"
                if price_tag:
                    # حذف قیمت قدیمی اگر وجود داشت
                    if price_tag.find('del'):
                        price_tag.find('del').decompose()
                    price_text = price_tag.text.strip()
                price = re.sub(r'[^\d]', '', price_text)
                if not price: price = "0"
                
                img_tag = block.select_one("img")
                image_url = ""
                if img_tag and 'src' in img_tag.attrs:
                    image_url = img_tag['src']
                    if not image_url.startswith('http'):
                        image_url = "https://staticcontent.eways.co" + image_url
                
                id_tag = block.select_one("a[data-productid]")
                product_id = id_tag['data-productid'] if id_tag else None

                # استخراج موجودی. این سلکتور بسیار شکننده است.
                stock_div = block.select(".col-lg-1.text-center.col-xs-6")
                stock = stock_div[1].text.strip() if len(stock_div) > 1 else "0"

                if product_id and name:
                    products.append({
                        "id": product_id,
                        "name": name,
                        "price": price,
                        "stock": int(re.sub(r'[^\d]', '', stock) or 0),
                        "image": image_url,
                        "category_id": category_id
                    })
                    new_products_found += 1
            
            # اگر در این صفحه محصولی پیدا نشد، احتمالا صفحه آخر بوده
            if new_products_found == 0:
                print("    - محصول جدیدی در این صفحه نبود، توقف.")
                break

            page_num += 1
            time.sleep(1) # کمی تاخیر برای جلوگیری از بلاک شدن

        except Exception as e:
            print(f"    - خطا در پردازش صفحه محصولات: {e}")
            break
            
    return products

def get_all_products(session, categories):
    """تمام محصولات را از تمام دسته‌بندی‌ها جمع‌آوری می‌کند."""
    all_products = {}
    print("\n⏳ شروع فرآیند جمع‌آوری تمام محصولات از همه دسته‌بندی‌ها...")
    
    for category in tqdm(categories, desc="پردازش دسته‌بندی‌ها"):
        cat_id = category['id']
        cat_name = category['name']
        print(f"\nدر حال دریافت محصولات دسته‌بندی: '{cat_name}' (ID: {cat_id})")
        
        products_in_cat = get_products_from_category_page(session, cat_id)
        
        for product in products_in_cat:
            if product['stock'] > 0 and int(product['price']) > 0:
                 all_products[product['id']] = product # ذخیره با ID برای جلوگیری از تکرار
            else:
                 print(f"   - محصول '{product['name']}' ناموجود یا بدون قیمت است. نادیده گرفته شد.")

    print(f"\n✅ فرآیند جمع‌آوری کامل شد. تعداد کل محصولات یکتا و موجود: {len(all_products)}")
    return list(all_products.values())

# ==============================================================================
# --- بخش پردازش و انتقال به ووکامرس (بر اساس کد اولیه شما) ---
# ==============================================================================
# ... (تمام توابع مربوط به ووکامرس مثل get_wc_categories, create_wc_category, process_price, 
# _send_to_woocommerce, process_product, و... از کد اصلی شما باید اینجا کپی شوند) ...
# من چند تابع کلیدی را برای کامل بودن کد می‌آورم.

def process_price(price_value):
    # این تابع از کد اولیه شماست و باید بر اساس نیازهای جدید تنظیم شود.
    try:
        price_value = float(re.sub(r'[^\d.]', '', str(price_value)))
    except (ValueError, TypeError):
        return "0"
    
    # اضافه کردن سه صفر به انتهای قیمت چون قیمت‌ها در eways کوتاه شده‌اند
    price_value = price_value * 1000

    if price_value <= 1: return "0"
    elif price_value <= 7000000: new_price = price_value + 260000
    elif price_value <= 10000000: new_price = price_value * 1.035
    elif price_value <= 20000000: new_price = price_value * 1.025
    elif price_value <= 30000000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price, -4))) # رند کردن به نزدیک‌ترین ده هزار

def process_product(product, stats, category_mapping):
    """یک محصول از eways را به فرمت ووکامرس تبدیل و ارسال می‌کند."""
    # (نکته: این سایت متغیرها را به سادگی در دسترس قرار نمیدهد، پس فعلا فقط محصول ساده میسازیم)
    
    product_name = product.get('name', 'بدون نام')
    product_id_source = product.get('id', '')
    
    # نگاشت دسته‌بندی
    wc_cat_id = category_mapping.get(product.get('category_id'))
    if not wc_cat_id:
        print(f"   - هشدار: برای محصول '{product_name}' دسته‌بندی معادل یافت نشد.")
        return # یا میتوانید یک دسته پیشفرض بگذارید

    wc_data = {
        "name": product_name,
        "type": "simple",
        "sku": f"EWAYS-{product_id_source}",
        "regular_price": process_price(product.get('price', 0)),
        "description": "", # توضیحات در لیست محصولات نیست
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

    # ارسال به ووکامرس
    _send_to_woocommerce(wc_data['sku'], wc_data, stats)


def _send_to_woocommerce(sku, data, stats, retries=3):
    """محصول را در ووکامرس ایجاد یا آپدیت می‌کند."""
    check_url = f"{WC_API_URL}/products?sku={sku}"
    for attempt in range(retries):
        try:
            auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
            # چک کردن وجود محصول
            r_check = requests.get(check_url, auth=auth, verify=False)
            r_check.raise_for_status()
            existing_products = r_check.json()
            
            if existing_products:
                # آپدیت محصول
                product_id = existing_products[0]['id']
                update_url = f"{WC_API_URL}/products/{product_id}"
                res = requests.put(update_url, auth=auth, json=data, verify=False)
                if res.status_code == 200:
                    with stats['lock']: stats['updated'] += 1
                    print(f"   ✅ محصول '{data['name']}' با موفقیت آپدیت شد.")
                else:
                    print(f"   ❌ خطا در آپدیت محصول '{data['name']}'. Status: {res.status_code}, Response: {res.text}")
            else:
                # ایجاد محصول جدید
                create_url = f"{WC_API_URL}/products"
                res = requests.post(create_url, auth=auth, json=data, verify=False)
                if res.status_code == 201:
                    with stats['lock']: stats['created'] += 1
                    print(f"   ✅ محصول '{data['name']}' با موفقیت ایجاد شد.")
                else:
                    print(f"   ❌ خطا در ایجاد محصول '{data['name']}'. Status: {res.status_code}, Response: {res.text}")
            return # موفقیت، خروج از حلقه تلاش مجدد
        except requests.exceptions.RequestException as e:
            print(f"   ❌ خطای شبکه در ارتباط با ووکامرس: {e}")
            if attempt < retries - 1:
                print(f"   ⏳ تلاش مجدد پس از 5 ثانیه...")
                time.sleep(5)
            else:
                print("   ❌ تلاش‌ها به پایان رسید.")
                break


def process_product_wrapper(args):
    """Wrapper برای استفاده در ThreadPoolExecutor."""
    product, stats, category_mapping = args
    try:
        process_product(product, stats, category_mapping)
    except Exception as e:
        print(f"   ❌ خطای جدی در پردازش محصول {product.get('id', '')}: {e}")

# ==============================================================================
# --- تابع اصلی برنامه ---
# ==============================================================================
def main():
    """تابع اصلی برای اجرای کل فرآیند."""
    # --- اعتبارسنجی اولیه ---
    if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET]):
        print("❌ متغیرهای محیطی ووکامرس (WC_*) به درستی تنظیم نشده‌اند. برنامه متوقف می‌شود.")
        return
    if not AUT_COOKIE_VALUE:
        print("❌ متغیر محیطی کوکی (EWAYS_AUTH_TOKEN) تنظیم نشده است. برنامه متوقف می‌شود.")
        return

    # --- شروع فرآیند ---
    session = get_session()
    
    # 1. دریافت دسته‌بندی‌ها از سایت مبدا
    source_categories = get_and_parse_categories(session)
    if not source_categories:
        print("❌ هیچ دسته‌بندی دریافت نشد. برنامه خاتمه می‌یابد.")
        return
        
    print(f"\n✅ تعداد {len(source_categories)} دسته‌بندی از سایت مبدا دریافت شد.")
    # اینجا باید منطق انتقال/نگاشت دسته‌بندی‌ها به ووکامرس پیاده‌سازی شود
    # برای سادگی، فعلا فقط یک نقشه ساده از ID به ID میسازیم.
    # در واقعیت باید تابع transfer_categories از کد اولتان را اینجا بیاورید.
    category_mapping = {cat['id']: cat['id'] for cat in source_categories} # نقشه موقت!
    print("⚠️ هشدار: از نقشه موقت برای دسته‌بندی‌ها استفاده می‌شود. باید تابع انتقال کامل را پیاده‌سازی کنید.")

    # 2. دریافت تمام محصولات از سایت مبدا
    products = get_all_products(session, source_categories)
    if not products:
        print("❌ هیچ محصولی برای پردازش یافت نشد. برنامه خاتمه می‌یابد.")
        return

    # 3. پردازش و ارسال محصولات به ووکامرس
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
