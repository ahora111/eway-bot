import requests
import urllib3
import os
import re
import time
import json
import sys
import random
from tqdm import tqdm
from bs4 import BeautifulSoup

# ==============================================================================
# --- تنظیمات و متغیرهای سراسری ---
# ==============================================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- اطلاعات سایت Eways ---
BASE_URL = "https://panel.eways.co"
AUT_COOKIE_VALUE = os.environ.get("EWAYS_AUTH_TOKEN")
SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"
PRODUCT_LIST_URL_TEMPLATE = f"{BASE_URL}/Store/List/{{category_id}}/2/2/0/0/0/10000000000?page={{page}}"
MAX_PAGES_PER_CATEGORY = 50 # محدودیت حداکثر صفحات برای هر دسته‌بندی

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
        if response.status_code != 200: return None
        print("✅ HTML دسته‌بندی‌ها با موفقیت دریافت شد.")
        soup = BeautifulSoup(response.text, 'lxml')
        all_menu_items = soup.select("li[id^='menu-item-']")
        if not all_menu_items: return []
            
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
    """محصولات را از صفحات HTML یک دسته‌بندی استخراج می‌کند."""
    all_products_in_category, seen_product_ids, page_num = [], set(), 1
    while True:
        if page_num > MAX_PAGES_PER_CATEGORY:
            print(f"    - ⚠️ به حداکثر تعداد صفحات ({MAX_PAGES_PER_CATEGORY}) رسیدیم. توقف.")
            break
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page_num)
        try:
            response = session.get(url, timeout=30)
            if response.status_code != 200: break
            soup = BeautifulSoup(response.text, 'lxml')
            product_blocks = soup.select(".goods_item.goods-record")
            if not product_blocks: break
            current_page_product_ids = []
            for block in product_blocks:
                try:
                    if 'noCount' in block.get('class', []): continue
                    id_tag = block.select_one("a[data-productid]")
                    product_id = id_tag['data-productid'] if id_tag else None
                    if not product_id or product_id in seen_product_ids: continue
                    seen_product_ids.add(product_id)
                    current_page_product_ids.append(product_id)
                    name = (block.select_one(".goods-record-title").text.strip() if block.select_one(".goods-record-title") else "نام یافت نشد")
                    price_tag = block.select_one(".goods-record-price")
                    price = "0"
                    if price_tag:
                        if price_tag.find('del'): price_tag.find('del').decompose()
                        price = re.sub(r'[^\d]', '', price_tag.text.strip()) or "0"
                    img_tag = block.select_one("img.goods-record-image")
                    image_url = (img_tag['data-src'] if img_tag and 'data-src' in img_tag.attrs else "")
                    if image_url and not image_url.startswith('http'): image_url = "https://staticcontent.eways.co" + image_url
                    stock_tag = block.select_one(".goods-record-count span")
                    stock = int(stock_tag.text.strip()) if stock_tag else 1
                    if int(price) > 0:
                        all_products_in_category.append({"id": product_id, "name": name, "price": price, "stock": stock, "image": image_url, "category_id": category_id})
                except Exception: continue
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
# --- تابع اصلی برنامه ---
# ==============================================================================
def main():
    if not AUT_COOKIE_VALUE:
        print("❌ متغیر محیطی کوکی (EWAYS_AUTH_TOKEN) تنظیم نشده است.")
        return

    session = get_session()
    
    # مرحله ۱: دریافت تمام دسته‌بندی‌ها
    source_categories = get_and_parse_categories(session)
    if not source_categories:
        print("❌ هیچ دسته‌بندی دریافت نشد. برنامه خاتمه می‌یابد.")
        return
    
    # مرحله ۲: دریافت تمام محصولات از تمام دسته‌بندی‌ها
    products = get_all_products(session, source_categories)
    
    # مرحله ۳: نمایش نتایج در لاگ
    if not products:
        print("\n✅ هیچ محصول موجودی در هیچ دسته‌بندی پیدا نشد.")
    else:
        print(f"\n\n===================================")
        print(f"  نمایش لیست {len(products)} محصول پیدا شده")
        print(f"===================================")
        # استفاده از json.dumps برای نمایش زیبا و خوانا
        print(json.dumps(products, indent=2, ensure_ascii=False))

    print("\n===============================")
    print("✅ اسکریپت در حالت فقط-خواندنی (Read-Only) اجرا شد.")
    print("هیچ داده‌ای به ووکامرس ارسال نشد.")
    print("===============================")


if __name__ == "__main__":
    main()
