import requests
import urllib3
from bs4 import BeautifulSoup
import re
import os
import json

# --- اطلاعات ووکامرس (فعلا لازم نیست) ---
# WC_API_URL = os.environ.get("WC_API_URL")
# ...

# --- اطلاعات سایت هدف (Eways.co) ---
BASE_URL = "https://panel.eways.co"
# مقدار کوکی 'Aut' که از مرورگر استخراج شد
AUT_COOKIE_VALUE = "eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2Q0JDLUhTNTEyIiwidHlwIjoiSldUIiwiY3R5IjoiSldUIn0..KEHfYmWlX93ZLEU4SyOXtw.78oT5HMz0ctFMseuBDRc0_sZ8HsLpqciyfZh0imeG_j-xkcCzE4MrMMjgD6QUAvQ56x6b-bJTOTtnefmq8BMtHg3t9OjPeb2gf4nOBCPylWo0wiAvMPJvn7nqDMkqIGmdGpB7AW1z3kBSYy4oQSM40zlym_0BtnB1hRcLj1ChHEv3X7leR8Ti4Qf2b4pC_f_GqlQCtp2SQQtrCy7BB7k7Uzc-NLxqBdO_Obf0wwX-qHqzxwEouPSSvGaGgYLEWFxjXmEnepUmZzFL_gYyof7QITyZfJFeYDIpTvQH8Ucpq-4TQVwwqnzOPQhP8_vlaYyS2SXyCVpJ_f8KalfAajjA_to0z7GbVQBJV8J_aplT-K-1a6LKJNbUAzB1I6ZWF1WHanxrxf_zm3U3wlHwg6m4d2txprpb6zGfoDdtpeUQb0vLpwt3OpJimeN4PLqS5kqz7RSpKG5uxtAWmJzQsmvFrcU9YFEOzR8QfIC0P8HFOGYkdGoffFtJw-mODWyG7AeHbihJG6Z5MiU3_V3cpjBrOB8XfYEZ6khVQtvk_T2m4OWVs9BH_DpTmpFanjl4RGHeG9OzSDA6a6e9xqQlpO2tf895QOQYjQOtpVOV__sVlBIxNU59VofEf2t59D4A9rybKbASb1sx-eCcvXfzCMXiPEo7cpkf3U5HgpEwTH7hG2RwtxDCOAwIWfStLPz7QBuRuYjwbY0B_BnO_9Ak2aDv7TVoTkij2qVIX4kGwy79Fef45RVEqzIlwqYtKcdHBTFhNHpNywTTdsFA00mh4Vw_hawWaWg8DK8Tb0vEpqKoQFhvi54Ru9DSLxHx9RsbpTXKhogqJ7-ZCRHfYVurP1uf5Z3PHIRb-1iZj1jQIhO9fKz8-X-Dqx4YMDbdDZw1Ty0LcuhCymc7JzX9I10_Cxc8S4qQJRJcU2gJN3KZVQ86G3ZipdiUfuGgfnpbPLJT9XneKpXaX6j2dKvoXE51teFJQ.GB4DSFc1I9jary-twCel9xaHRZAdIzrTOejsAgyrSls"

SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"

# غیرفعال کردن هشدار SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_session():
    """یک Session با کوکی احراز هویت ایجاد می‌کند."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f"{BASE_URL}/",
        'X-Requested-With': 'XMLHttpRequest'
    })
    # مهم: به جای هدر، کوکی را تنظیم می‌کنیم
    session.cookies.set('Aut', AUT_COOKIE_VALUE, domain='panel.eways.co')
    session.verify = False
    return session

def get_and_parse_categories(session):
    """دسته‌بندی‌ها را از API دریافت و با BeautifulSoup تجزیه می‌کند."""
    print(f"⏳ دریافت دسته‌بندی‌ها از: {SOURCE_CATS_API_URL}")
    try:
        response = session.get(SOURCE_CATS_API_URL)
        # حتی اگر 401 یا خطای دیگری باشد، متن آن را برای دیباگ نشان بده
        if response.status_code != 200:
            print(f"❌ خطای وضعیت {response.status_code} دریافت شد.")
            print("پاسخ سرور:", response.text[:500]) # نمایش 500 کاراکتر اول از خطا
            response.raise_for_status()
            
        print("✅ HTML دسته‌بندی‌ها با موفقیت دریافت شد.")
        
        soup = BeautifulSoup(response.text, 'lxml')
        all_menu_items = soup.select("li[id^='menu-item-']")
        
        if not all_menu_items:
            print("❌ هیچ آیتم دسته‌بندی در HTML پیدا نشد. شاید کوکی نامعتبر باشد یا ساختار صفحه تغییر کرده.")
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

            if name:
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

        return list(cats_map.values())

    except requests.exceptions.RequestException as e:
        print(f"❌ خطا در درخواست شبکه: {e}")
        if response and response.status_code == 401:
             print("خطای 401 (Unauthorized) - کوکی 'Aut' شما نامعتبر یا منقضی شده است.")
        return None
    except Exception as e:
        print(f"❌ خطای ناشناخته در پردازش دسته‌بندی‌ها: {e}")
        return None


# (کد قبلی را دست نخورده نگه دارید و این توابع را به آن اضافه کنید)

# الگو برای URL صفحه محصولات یک دسته‌بندی
PRODUCT_LIST_URL_TEMPLATE = f"{BASE_URL}/Store/List/{{category_id}}/2/2/0/0/0/10000000000"

def get_products_from_category_page(session, category_id):
    """محصولات را از صفحه HTML یک دسته‌بندی استخراج می‌کند."""
    
    products = []
    page_num = 1 # فرض می‌کنیم صفحه‌بندی با پارامتر page=N است
    
    while True:
        # آدرس URL را برای هر صفحه می‌سازیم
        # این الگو ممکن است نیاز به تغییر داشته باشد (مثلاً page= در پارامترها)
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id)
        # اگر صفحه‌بندی پارامتر داشت: params = {'page': page_num}
        
        print(f"  - در حال دریافت محصولات از صفحه {page_num} برای دسته‌بندی {category_id}...")
        print(f"    URL: {url}")
        
        try:
            response = session.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'lxml')
            
            # پیدا کردن بلاک‌های محصولات. این سلکتور کاملا حدسی است و باید اصلاح شود.
            # بر اساس کدهای قبلی، شاید چیزی شبیه به این باشد:
            product_blocks = soup.select(".cat-body") 
            if not product_blocks:
                # اگر سلکتور بالا کار نکرد، شاید این یکی باشد
                product_blocks = soup.select(".goods-item-title") 
            
            if not product_blocks:
                print("    - هیچ محصولی در این صفحه یافت نشد یا صفحه‌بندی تمام شد.")
                break # اگر محصولی پیدا نشد از حلقه خارج شو

            print(f"    - تعداد {len(product_blocks)} بلاک محصول پیدا شد.")

            for block in product_blocks:
                # استخراج اطلاعات از هر بلاک. این بخش نیاز به دقت و بررسی HTML واقعی دارد.
                # مثال‌های فرضی:
                name_tag = block.select_one("a span")
                name = name_tag.text.strip() if name_tag else "بدون نام"
                
                price_tag = block.select_one(".price")
                price_text = price_tag.text.strip() if price_tag else "0"
                # پاکسازی قیمت از حروف و کاما
                price = re.sub(r'[^\d]', '', price_text)
                
                img_tag = block.select_one("img")
                image_url = img_tag['src'] if img_tag and 'src' in img_tag.attrs else ""
                if image_url and not image_url.startswith('http'):
                    image_url = "https://staticcontent.eways.co" + image_url
                
                # پیدا کردن ID محصول از لینک 'add to cart' یا لینک جزئیات
                id_tag = block.select_one("a[data-productid]")
                product_id = id_tag['data-productid'] if id_tag else None

                stock_tag = block.select_one(".col-lg-1.text-center.col-xs-6")
                stock = stock_tag.text.strip() if stock_tag else "0"
                
                if product_id and name:
                    products.append({
                        "id": product_id,
                        "name": name,
                        "price": price,
                        "stock": stock,
                        "image": image_url,
                        "category_id": category_id
                    })
            
            # مدیریت صفحه‌بندی (بسیار مهم)
            # باید ببینیم آیا لینک "صفحه بعد" وجود دارد یا نه
            # این بخش فعلا غیرفعال است تا منطق اصلی را تست کنیم.
            # next_page_link = soup.select_one("a.pagination-next")
            # if not next_page_link:
            #     break
            
            # فعلا فقط صفحه اول را میخوانیم و از حلقه خارج میشویم
            break
            
            # page_num += 1

        except Exception as e:
            print(f"    - خطا در پردازش صفحه محصولات: {e}")
            break
            
    return products

# --- در تابع main ---
def main():
    # ... (کدهای قبلی برای دریافت دسته‌بندی‌ها) ...
    session = get_session()
    flat_categories = get_and_parse_categories(session)

    if flat_categories:
        # --- بخش جدید برای تست دریافت محصولات ---
        
        # یک دسته‌بندی برای تست انتخاب می‌کنیم (مثلاً اولین دسته‌بندی که پیدا شده)
        test_category_id = 4285 # این ID دسته‌بندی موبایل از URLهای قبلی شما بود
        print(f"\n--- تست دریافت محصولات برای دسته‌بندی ID: {test_category_id} ---")
        
        products = get_products_from_category_page(session, test_category_id)
        
        if products:
            print(f"\n✅ تعداد {len(products)} محصول با موفقیت از صفحه اول استخراج شد.")
            print("--- نمونه ۳ محصول اول ---")
            print(json.dumps(products[:3], indent=2, ensure_ascii=False))
        else:
            print("\n❌ استخراج محصول ناموفق بود. لطفاً سلکتورها را در کد بررسی کنید.")
    
    else:
        print("\n❌ استخراج دسته‌بندی‌ها ناموفق بود.")

