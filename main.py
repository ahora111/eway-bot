#!/usr/bin/env python3
import os
import time
import requests
import logging
import json
import base64
import gspread
import sys
from collections import defaultdict
from oauth2client.service_account import ServiceAccountCredentials
from persiantools.jdatetime import JalaliDate
from pytz import timezone
from datetime import datetime, time as dt_time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ==============================================================================
# بخش ۱: تنظیمات و پیکربندی اولیه
# ==============================================================================

# غیرفعال کردن هشدار SSL
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# تنظیمات لاگ‌گیری
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# خواندن متغیرهای محیطی
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = 'Sheet1'
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# بررسی زمان اجرا
iran_tz = timezone('Asia/Tehran')
now = datetime.now(iran_tz)
start_time = dt_time(9, 30)
end_time = dt_time(23, 30)
if not (start_time <= now.time() <= end_time):
    logging.info("🕒 خارج از بازه مجاز اجرا (۹:۳۰ تا ۲۳:۳۰). اسکریپت متوقف شد.")
    sys.exit()

# ==============================================================================
# بخش ۲: توابع استخراج داده از منابع مختلف
# ==============================================================================

def fetch_from_naminet_api():
    logging.info("در حال دریافت اطلاعات از منبع اول (API Naminet)...")
    url = "https://panel.naminet.co/api/catalog/productGroupsAttrNew?term="
    headers = {
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJuYmYiOiIxNzUyMjUyMTE2IiwiZXhwIjoiMTc2MDAzMTcxNiIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL2VtYWlsYWRkcmVzcyI6IjA5MzcxMTExNTU4QGhtdGVtYWlsLm5leHQiLCJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6ImE3OGRkZjViLTVhMjMtNDVkZC04MDBlLTczNTc3YjBkMzQzOSIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL25hbWUiOiIwOTM3MTExMTU1OCIsIkN1c3RvbWVySWQiOiIxMDA4NCJ9.kXoXA0atw0M64b6m084Gt4hH9MoC9IFFDFwuHOEdazA"
    }
    try:
        response = requests.get(url, headers=headers, verify=False, timeout=30)
        response.raise_for_status()
        data = response.json()
        products = []
        for parent in data.get("ParentCategories", []):
            for category in parent.get("Data", []):
                for item in category.get("Data", []):
                    full_name = f"{item.get('ProductName', '')} {item.get('Name', '')}".strip()
                    price = item.get("final_price_value", 0)
                    if full_name and price > 0:
                        products.append({"name": full_name, "price": int(price)})
        logging.info(f"✅ از منبع اول {len(products)} محصول دریافت شد.")
        return products
    except Exception as e:
        logging.error(f"❌ خطا در دریافت اطلاعات از منبع اول: {e}")
        return []

def get_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    service = Service()
    return webdriver.Chrome(service=service, options=options)

def scroll_page(driver, pause_time=1):
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause_time)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

def fetch_from_hamrahtel_site():
    logging.info("در حال دریافت اطلاعات از منبع دوم (سایت Hamrahtel)...")
    driver = get_driver()
    products = []
    try:
        urls = {
            "mobile": "https://hamrahtel.com/quick-checkout?category=mobile",
            "tablet": "https://hamrahtel.com/quick-checkout?category=tablet",
            "console": "https://hamrahtel.com/quick-checkout?category=game-console"
        }
        for category, url in urls.items():
            driver.get(url)
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[class^="mantine-"] > .mantine-Text-root')))
            scroll_page(driver)
            elements = driver.find_elements(By.CSS_SELECTOR, 'div[class^="mantine-"] > .mantine-Text-root')
            cleaned_elements = [el.text.strip() for el in elements if el.text.strip()][25:]
            i = 0
            while i < len(cleaned_elements) - 1:
                name = cleaned_elements[i]
                price_str = cleaned_elements[i+1].replace("تومان", "").replace(",", "").replace("٬", "").strip()
                if price_str.isdigit():
                    products.append({"name": name, "price": int(price_str)})
                    i += 2
                else:
                    i += 1
        logging.info(f"✅ از منبع دوم {len(products)} محصول دریافت شد.")
        return products
    except Exception as e:
        logging.error(f"❌ خطا در دریافت اطلاعات از منبع دوم: {e}")
        return []
    finally:
        driver.quit()

# ==============================================================================
# بخش ۳: توابع پردازش داده و قیمت
# ==============================================================================

def process_price(price):
    if price <= 1: return 0
    elif price <= 7000000: increase = price + 260000
    elif price <= 10000000: increase = price * 1.035
    elif price <= 20000000: increase = price * 1.025
    elif price <= 30000000: increase = price * 1.02
    else: increase = price * 1.015
    return round(increase, -5)

def normalize_name(name):
    # این تابع برای یکسان‌سازی نام‌ها برای مقایسه است
    return ' '.join(name.lower().strip().replace('ی', 'ي').replace('ک', 'ك').split())

def get_final_product_list():
    naminet_products = fetch_from_naminet_api()
    hamrahtel_products = fetch_from_hamrahtel_site()
    
    all_products_raw = defaultdict(list)
    
    # اضافه کردن محصولات با قیمت خام
    for p in naminet_products:
        all_products_raw[normalize_name(p['name'])].append(p['price'])
    for p in hamrahtel_products:
        all_products_raw[normalize_name(p['name'])].append(p['price'])
        
    final_products = []
    for name, prices in all_products_raw.items():
        # انتخاب کمترین قیمت خام
        best_raw_price = min(prices)
        # پردازش قیمت نهایی بر اساس کمترین قیمت خام
        final_price = process_price(best_raw_price)
        if final_price > 0:
            final_products.append({"name": name.title(), "price": final_price})
            
    # مرتب‌سازی لیست نهایی بر اساس قیمت
    final_products.sort(key=lambda x: x['price'])
    return final_products

# ==============================================================================
# بخش ۴: توابع آماده‌سازی پیام برای تلگرام
# ==============================================================================

def categorize_products(products):
    categorized = defaultdict(list)
    emoji_map = {
        "samsung": "🔵", "galaxy": "🔵",
        "xiaomi": "🟡", "poco": "🟡", "redmi": "🟡",
        "iphone": "🍏", "apple": "🍏",
        "nokia": "🟢",
        "nothing": "⚫️",
        "tablet": "🟠", "tab": "🟠", "pad": "🟠",
        "speaker": "🔉",
        "watch": "⌚️",
        "laptop": "💻",
        "console": "🎮", "playstation": "🎮",
    }
    
    for p in products:
        name_lower = p['name'].lower()
        assigned_emoji = "🟣" # ایموجی پیش‌فرض برای متفرقه
        for keyword, emoji in emoji_map.items():
            if keyword in name_lower:
                assigned_emoji = emoji
                break
        categorized[assigned_emoji].append(p)
        
    return categorized

def escape_special_characters(text):
    escape_chars = ['\\', '(', ')', '[', ']', '~', '*', '_', '-', '+', '>', '#', '.', '!', '|']
    for char in escape_chars:
        text = text.replace(char, '\\' + char)
    return text

def build_message_body(products):
    lines = []
    for p in products:
        lines.append(f"{p['name']}")
        lines.append(f"{p['price']:,} تومان")
        lines.append("")
    return "\n".join(lines)

def get_current_time_and_date():
    iran_tz = timezone('Asia/Tehran')
    now = datetime.now(iran_tz)
    current_time = now.strftime('%H:%M')
    jalali_date = JalaliDate.today()
    weekday_map = ["شنبه💪", "یکشنبه😃", "دوشنبه☺️", "سه‌شنبه🥱", "چهارشنبه😕", "پنج‌شنبه☺️", "جمعه😎"]
    weekday_farsi = weekday_map[jalali_date.weekday()]
    date_formatted = f"{weekday_farsi} {jalali_date.strftime('%Y/%m/%d')}"
    return current_time, date_formatted

def prepare_final_message(category_title, body):
    current_time, update_date_formatted = get_current_time_and_date()
    header = (
        f"🗓 بروزرسانی {update_date_formatted} 🕓 ساعت: {current_time}\n"
        f"✅ لیست پخش موبایل اهورا\n\n"
        f"⬅️ موجودی {category_title} ➡️\n\n"
    )
    footer = "\n\n☎️ شماره های تماس:\n📞 09371111558\n📞 02833991417"
    return f"{header}{body}{footer}"

def split_message(message, max_length=4000):
    parts = []
    while len(message) > max_length:
        split_pos = message.rfind('\n\n', 0, max_length)
        if split_pos == -1:
            split_pos = message.rfind('\n', 0, max_length)
        if split_pos == -1:
            split_pos = max_length
        parts.append(message[:split_pos])
        message = message[split_pos:].lstrip()
    parts.append(message)
    return parts

# ==============================================================================
# بخش ۵: توابع تعامل با گوگل شیت و API تلگرام
# ==============================================================================

# ... توابع این بخش (get_credentials, connect_to_sheet, check_and_create_headers, 
# load_sheet_data, update_sheet_data, send_telegram_message, edit_telegram_message, 
# delete_telegram_message, process_category_messages, update_final_message_in_sheet, 
# get_final_message_from_sheet, send_or_edit_final_message)
# تقریباً بدون تغییر از کدهای قبلی کپی می‌شوند.

def get_credentials():
    encoded = os.getenv("GSHEET_CREDENTIALS_JSON")
    if not encoded:
        raise Exception("GSHEET_CREDENTIALS_JSON not found in environment variables")
    decoded = base64.b64decode(encoded)
    temp_path = "/tmp/creds.json"
    with open(temp_path, "wb") as f:
        f.write(decoded)
    return temp_path

def connect_to_sheet():
    creds_path = get_credentials()
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

def check_and_create_headers(sheet):
    try:
        if not sheet.get_all_values():
            sheet.update(values=[["emoji", "date", "part", "message_id", "text"]], range_name="A1:E1")
            logging.info("✅ هدرها به شیت اضافه شدند.")
    except gspread.exceptions.APIError as e:
        if "exceeds grid limits" in str(e): # Sheet is empty
            sheet.update(values=[["emoji", "date", "part", "message_id", "text"]], range_name="A1:E1")
            logging.info("✅ هدرها به شیت خالی اضافه شدند.")
        else:
            raise e

def load_sheet_data(sheet):
    records = sheet.get_all_records()
    data = defaultdict(list)
    for row in records:
        if row.get("emoji") and row.get("date"):
            data[(row["emoji"], row["date"])].append({
                "part": int(row["part"]),
                "message_id": row["message_id"],
                "text": row["text"]
            })
    return data

def update_sheet_data(sheet, emoji, messages):
    today = JalaliDate.today().strftime("%Y-%m-%d")
    records = sheet.get_all_records()
    rows_to_delete = [i + 2 for i, row in enumerate(records) if row.get("emoji") == emoji and row.get("date") == today]
    if rows_to_delete:
        sheet.delete_rows(min(rows_to_delete), max(rows_to_delete))

    rows_to_append = []
    for part, (message_id, text) in enumerate(messages, 1):
        rows_to_append.append([emoji, today, part, message_id, text])
    if rows_to_append:
        sheet.append_rows(rows_to_append)

def send_telegram_message(text, bot_token, chat_id, reply_markup=None):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    params = {"chat_id": chat_id, "text": escape_special_characters(text), "parse_mode": "MarkdownV2"}
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
    response = requests.post(url, json=params)
    if response.ok:
        return response.json()["result"]["message_id"]
    logging.error(f"خطا در ارسال پیام: {response.text}")
    return None

def edit_telegram_message(msg_id, text, bot_token, chat_id, reply_markup=None):
    url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
    params = {"chat_id": chat_id, "message_id": msg_id, "text": escape_special_characters(text), "parse_mode": "MarkdownV2"}
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
    response = requests.post(url, json=params)
    return response.ok

def delete_telegram_message(msg_id, bot_token, chat_id):
    url = f"https://api.telegram.org/bot{bot_token}/deleteMessage"
    params = {"chat_id": chat_id, "message_id": msg_id}
    response = requests.post(url, json=params)
    return response.ok

def process_category_messages(emoji, messages, sheet, today):
    sheet_data = load_sheet_data(sheet)
    prev_msgs = sorted(sheet_data.get((emoji, today), []), key=lambda x: x["part"])
    new_msgs, changed = [], False

    for i, msg_text in enumerate(messages):
        if i < len(prev_msgs):
            msg_id = prev_msgs[i]["message_id"]
            if prev_msgs[i]["text"] != msg_text:
                if not edit_telegram_message(msg_id, msg_text, BOT_TOKEN, CHAT_ID):
                    msg_id = send_telegram_message(msg_text, BOT_TOKEN, CHAT_ID) # Fallback to send
                changed = True
        else:
            msg_id = send_telegram_message(msg_text, BOT_TOKEN, CHAT_ID)
            changed = True
        if msg_id: new_msgs.append((msg_id, msg_text))

    for j in range(len(messages), len(prev_msgs)):
        delete_telegram_message(prev_msgs[j]["message_id"], BOT_TOKEN, CHAT_ID)
        changed = True

    if changed: update_sheet_data(sheet, emoji, new_msgs)
    return [msg_id for msg_id, _ in new_msgs], changed

def send_or_edit_final_message(sheet, final_message_text, button_markup, should_force_send):
    today = JalaliDate.today().strftime("%Y-%m-%d")
    sheet_data = load_sheet_data(sheet)
    prev_msg_data = sheet_data.get(("FINAL", today))
    prev_msg_id = prev_msg_data[0]["message_id"] if prev_msg_data else None
    
    if prev_msg_id and not should_force_send:
        # فقط دکمه‌ها را ویرایش کن اگر متن یکسان است
        if edit_telegram_message(prev_msg_id, final_message_text, BOT_TOKEN, CHAT_ID, button_markup):
            logging.info("✅ پیام نهایی (فقط دکمه‌ها) ویرایش شد.")
            return
    
    # اگر نیاز به ارسال/ویرایش کامل است
    if prev_msg_id:
        # تلاش برای حذف پیام قدیمی
        delete_telegram_message(prev_msg_id, BOT_TOKEN, CHAT_ID)

    new_msg_id = send_telegram_message(final_message_text, BOT_TOKEN, CHAT_ID, button_markup)
    if new_msg_id:
        update_sheet_data(sheet, "FINAL", [(new_msg_id, final_message_text)])
        logging.info("✅ پیام نهایی جدید ارسال شد.")

# ==============================================================================
# بخش ۶: تابع اصلی و اجرای برنامه
# ==============================================================================

def main():
    try:
        # ۱. اتصال به شیت و بررسی هدرها
        sheet = connect_to_sheet()
        check_and_create_headers(sheet)

        # ۲. دریافت و پردازش لیست نهایی محصولات
        final_products = get_final_product_list()
        if not final_products:
            logging.warning("❌ هیچ محصولی برای پردازش یافت نشد. اسکریپت متوقف می‌شود.")
            return

        # ۳. دسته‌بندی محصولات
        categorized_products = categorize_products(final_products)

        # ۴. پردازش و ارسال پیام برای هر دسته
        today_str = JalaliDate.today().strftime("%Y-%m-%d")
        all_message_ids = {}
        any_change_detected = False

        category_titles = {
            "🔵": "سامسونگ", "🟡": "شیائومی", "🍏": "آیفون", "🟢": "نوکیا",
            "⚫️": "ناتینگ فون", "🟠": "تبلت", "🔉": "اسپیکر", "⌚️": "ساعت هوشمند",
            "💻": "لپ‌تاپ", "🎮": "کنسول بازی", "🟣": "متفرقه"
        }

        for emoji, products in categorized_products.items():
            if not products: continue
            
            category_title = category_titles.get(emoji, "سایر کالاها")
            message_body = build_message_body(products)
            full_message = prepare_final_message(category_title, message_body)
            message_parts = split_message(full_message)

            msg_ids, changed = process_category_messages(emoji, message_parts, sheet, today_str)
            if msg_ids: all_message_ids[emoji] = msg_ids
            if changed: any_change_detected = True
        
        # ۵. آماده‌سازی و ارسال پیام نهایی汇总
        final_message_text = (
            "✅ لیست گوشی و سایر کالاهای بالا بروز میباشد. ثبت خرید تا ساعت 10:30 شب انجام میشود و تحویل کالا ساعت 11:30 صبح روز بعد می باشد.\n\n"
            "✅اطلاعات واریز\n"
            "🔷 شماره شبا : IR970560611828006154229701\n"
            "🔷 شماره کارت : 6219861812467917\n"
            "🔷 بلو بانک   حسین گرئی\n\n"
            "⭕️ حتما رسید واریز به ایدی تلگرام زیر ارسال شود .\n"
            "🆔 @lhossein1\n\n"
            "✅شماره تماس ثبت سفارش :\n"
            "📞 09371111558\n"
            "📞 09386373926\n"
            "📞 09308529712\n"
            "📞 028-3399-1417"
        )

        button_labels = {
            "🔵": "📱 لیست سامسونگ", "🟡": "📱 لیست شیائومی", "🍏": "📱 لیست آیفون",
            "🟠": "📱 لیست تبلت", "💻": "💻 لیست لپ‌تاپ", "🎮": "🎮 کنسول بازی",
            "🟣": "📱 لیست متفرقه", "🟢": "📱 لیست نوکیا", "⚫️": "📱 لیست ناتینگ فون",
            "🔉": "🔉 لیست اسپیکر", "⌚️": "⌚️ ساعت هوشمند"
        }
        
        button_markup = {"inline_keyboard": []}
        # مرتب‌سازی دکمه‌ها بر اساس یک ترتیب دلخواه
        sorted_emojis = sorted(all_message_ids.keys(), key=lambda e: "🔵🟡🍏🟠💻🎮🟣⚫️🟢🔉⌚️".find(e))

        for emoji in sorted_emojis:
            msg_id = all_message_ids[emoji][0] # لینک به اولین پیام هر دسته
            label = button_labels.get(emoji, f"لیست {emoji}")
            url = f"https://t.me/c/{CHAT_ID.replace('-100', '')}/{msg_id}"
            button_markup["inline_keyboard"].append([{"text": label, "url": url}])

        send_or_edit_final_message(sheet, final_message_text, button_markup, any_change_detected)

    except Exception as e:
        logging.error(f"❌ خطای اصلی در برنامه: {e}", exc_info=True)
        # در صورت بروز خطا، یک پیام به تلگرام ارسال کن
        error_message = f"ربات با خطای جدی مواجه شد:\n\n`{str(e)}`"
        send_telegram_message(error_message, BOT_TOKEN, CHAT_ID)

if __name__ == "__main__":
    main()
