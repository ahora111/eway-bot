import requests
import urllib3
import os
import re
import time
import json
import sys
import random
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from threading import Lock

# (بخش تنظیمات و متغیرها بدون تغییر باقی می‌ماند)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
WC_API_URL = os.environ.get("WC_API_URL")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")
BASE_URL = "https://panel.eways.co"
AUT_COOKIE_VALUE = os.environ.get("EWAYS_AUTH_TOKEN")
SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"
PRODUCT_LIST_URL_TEMPLATE = f"{BASE_URL}/Store/List/{{category_id}}/2/2/1/0/0/10000000000?page={{page}}" # صفحه بندی با پارامتر

# (تمام توابع دیگر مثل get_session, get_and_parse_categories, get_products_from_category_page و ... بدون تغییر باقی می‌مانند)
# ...
# فقط تابع main را با این نسخه نهایی جایگزین کنید تا از نقشه صحیح دسته‌بندی‌ها استفاده کند.

def main():
    if not all([WC_API_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET, AUT_COOKIE_VALUE]):
        print("❌ یکی از متغیرهای محیطی ضروری (WC_* یا EWAYS_AUTH_TOKEN) تنظیم نشده است.")
        return

    session = get_session()
    
    source_categories = get_and_parse_categories(session)
    if source_categories is None: return

    # در اینجا دسته‌بندی‌های مورد نظر را انتخاب کنید.
    # این ID ها باید از سایت مبدا باشند
    selected_ids = [4285, 16778] # مثال: ID برای گوشی و لپ‌تاپ
    filtered_categories = [c for c in source_categories if c['id'] in selected_ids]
    print(f"\n✅ دسته‌بندی‌های منتخب: {[c['name'] for c in filtered_categories]}")
    if not filtered_categories:
        print("❌ هیچکدام از دسته‌بندی‌های منتخب یافت نشدند.")
        return

    # *** مرحله کلیدی و اصلاح شده ***
    # 1. ابتدا دسته‌بندی‌ها را به ووکامرس منتقل می‌کنیم
    # 2. یک نقشه دقیق از ID سایت مبدا به ID سایت ووکامرس دریافت می‌کنیم
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
        # در اینجا از category_mapping صحیح استفاده می‌کنیم
        args_list = [(p, stats, category_mapping) for p in products]
        list(tqdm(executor.map(process_product_wrapper, args_list), total=len(products), desc="ارسال محصولات"))

    print("\n===============================")
    print(f"📦 محصولات پردازش شده: {len(products)}")
    print(f"🟢 ایجاد شده: {stats['created']}")
    print(f"🔵 آپدیت شده: {stats['updated']}")
    print("===============================\nتمام!")


if __name__ == "__main__":
    # اطمینان حاصل کنید که تمام توابع دیگر در فایل شما وجود دارند
    main()
