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


def main():
    """تابع اصلی برای اجرای برنامه."""
    session = get_session()
    flat_categories = get_and_parse_categories(session)

    if flat_categories:
        print("\n✅ پردازش دسته‌بندی‌ها با موفقیت انجام شد.")
        print(f"تعداد کل دسته‌بندی‌های استخراج شده: {len(flat_categories)}")
        
        # مرتب‌سازی بر اساس parent_id برای نمایش بهتر
        sorted_cats = sorted(flat_categories, key=lambda x: (x['parent_id'] or -1, x['id']))
        
        print("\n--- نمونه دسته‌بندی‌ها (مرتب شده) ---")
        print(json.dumps(sorted_cats[:15], indent=2, ensure_ascii=False))
    else:
        print("\n❌ استخراج دسته‌بندی‌ها ناموفق بود. لطفاً خروجی خطا را بررسی کنید.")

if __name__ == "__main__":
    main()
