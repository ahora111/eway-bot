import requests
import urllib3
from bs4 import BeautifulSoup
import re
import os
import json # برای نمایش بهتر خروجی

# --- اطلاعات ووکامرس (باید از Secrets خوانده شود) ---
WC_API_URL = os.environ.get("WC_API_URL")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

# --- اطلاعات سایت هدف (Eways.co) ---
BASE_URL = "https://panel.eways.co"
# این مهمترین بخش است، باید آن را از هدر درخواست در مرورگر پیدا کنید
AUTH_TOKEN = os.environ.get("EWAYS_AUTH_TOKEN", "Bearer eyJhbGciOi...") 

SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"

# غیرفعال کردن هشدار SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_session():
    """یک Session برای حفظ کوکی‌ها و هدرها ایجاد می‌کند."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Authorization': AUTH_TOKEN,
        'Referer': f"{BASE_URL}/",
        'X-Requested-With': 'XMLHttpRequest'
    })
    session.verify = False
    return session

def get_and_parse_categories(session):
    """دسته‌بندی‌ها را از API دریافت و با BeautifulSoup تجزیه می‌کند."""
    print(f"⏳ دریافت دسته‌بندی‌ها از: {SOURCE_CATS_API_URL}")
    try:
        response = session.get(SOURCE_CATS_API_URL)
        response.raise_for_status()
        print("✅ HTML دسته‌بندی‌ها با موفقیت دریافت شد.")
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        # سلکتور دقیق برای پیدا کردن آیتم‌های منو
        # ما تمام li هایی که id آنها با 'menu-item-' شروع می‌شود را میخواهیم
        all_menu_items = soup.select("li[id^='menu-item-']")
        
        if not all_menu_items:
            print("❌ هیچ آیتم دسته‌بندی در HTML پیدا نشد. سلکتور را بررسی کنید.")
            return []
            
        print(f"🔎 تعداد {len(all_menu_items)} آیتم منو پیدا شد. در حال پردازش...")
        
        # یک دیکشنری برای نگهداری تمام دسته‌بندی‌ها با ساختار درست
        # key: cat_id, value: {name, parent_id, children_ids}
        cats_map = {}
        
        for item in all_menu_items:
            # استخراج ID
            cat_id_raw = item.get('id', '')
            match = re.search(r'(\d+)', cat_id_raw)
            if not match:
                continue
            cat_id = int(match.group(1))

            # استخراج نام
            # ما اولین تگ a که فرزند مستقیم li هست را میخواهیم
            name_tag = item.find('a', recursive=False)
            if not name_tag:
                # اگر تگ a فرزند مستقیم نبود (مثل بعضی ساختارها)، اینطور پیدایش میکنیم
                name_tag = item.select_one("a")
            name = name_tag.text.strip() if name_tag else f"بدون نام {cat_id}"

            cats_map[cat_id] = {"id": cat_id, "name": name, "parent_id": None, "children": []}

        # حالا در یک حلقه دوم، روابط والد-فرزندی را مشخص می‌کنیم
        for item in all_menu_items:
            cat_id_raw = item.get('id', '')
            match = re.search(r'(\d+)', cat_id_raw)
            if not match:
                continue
            cat_id = int(match.group(1))

            # تمام فرزندان این آیتم را پیدا کن
            child_items = item.select("ul > li[id^='menu-item-']")
            for child in child_items:
                child_id_raw = child.get('id', '')
                child_match = re.search(r'(\d+)', child_id_raw)
                if not child_match:
                    continue
                child_id = int(child_match.group(1))
                
                # اگر فرزند در نقشه ما وجود داشت، والدش را ثبت کن
                if child_id in cats_map:
                    cats_map[child_id]['parent_id'] = cat_id
                    if cat_id in cats_map:
                         cats_map[cat_id]['children'].append(child_id)


        # تبدیل نقشه به لیست مسطح (flat list)
        flat_cats = list(cats_map.values())
        return flat_cats

    except requests.exceptions.RequestException as e:
        print(f"❌ خطا در درخواست شبکه: {e}")
        if "401" in str(e):
             print("خطای 401 (Unauthorized) - توکن شما نامعتبر یا منقضی شده است.")
        return None
    except Exception as e:
        print(f"❌ خطای ناشناخته در پردازش دسته‌بندی‌ها: {e}")
        return None


def main():
    """تابع اصلی برای اجرای برنامه."""
    if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET]):
        print("❌ متغیرهای محیطی ووکامرس (WC_*) به درستی تنظیم نشده‌اند.")
        return

    if not AUTH_TOKEN or "Bearer eyJ" in AUTH_TOKEN:
        print("❌ توکن EWAYS_AUTH_TOKEN تنظیم نشده است. لطفاً آن را در متغیرهای محیطی یا مستقیم در کد قرار دهید.")
        return

    session = get_session()
    flat_categories = get_and_parse_categories(session)

    if flat_categories:
        print("\n✅ پردازش دسته‌بندی‌ها با موفقیت انجام شد.")
        print(f"تعداد کل دسته‌بندی‌های استخراج شده: {len(flat_categories)}")
        
        # نمایش ۵ دسته‌بندی اول برای بررسی
        print("\n--- نمونه ۵ دسته‌بندی اول ---")
        print(json.dumps(flat_categories[:5], indent=2, ensure_ascii=False))
        
        # نمایش یک دسته‌بندی والد و فرزندانش برای نمونه
        parent_sample = next((cat for cat in flat_categories if cat.get('children')), None)
        if parent_sample:
            print("\n--- نمونه یک دسته‌بندی والد ---")
            print(json.dumps(parent_sample, indent=2, ensure_ascii=False))

    else:
        print("\n❌ استخراج دسته‌بندی‌ها ناموفق بود. لطفاً خروجی خطا را بررسی کنید.")
        
    # اینجا بخش انتقال دسته‌بندی‌ها به ووکامرس فراخوانی می‌شود
    # transfer_categories(flat_categories) ...


if __name__ == "__main__":
    main()
