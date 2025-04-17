#!/usr/bin/env python3
import os
import time
import requests
import logging
import json
import pytz
import sys
import gspread
import datetime
from datetime import datetime, time as dt_time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from persiantools.jdatetime import JalaliDate
from oauth2client.service_account import ServiceAccountCredentials


# --- تنظیمات Google Sheets ---
SPREADSHEET_ID = '1nMtYsaa9_ZSGrhQvjdVx91WSG4gANg2R0s4cSZAZu7E'
SHEET_NAME = 'Sheet1'
BOT_TOKEN = "8187924543:AAH0jZJvZdpq_34um8R_yCyHQvkorxczXNQ"
CHAT_ID = "-1002505490886"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def get_driver():
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        service = Service()
        driver = webdriver.Chrome(service=service, options=options)
        return driver
    except Exception as e:
        logging.error(f"خطا در ایجاد WebDriver: {e}")
        return None

def scroll_page(driver, scroll_pause_time=2):
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause_time)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

def extract_product_data(driver, valid_brands):
    product_elements = driver.find_elements(By.CLASS_NAME, 'mantine-Text-root')
    brands, models = [], []
    for product in product_elements:
        name = product.text.strip().replace("تومانءء", "").replace("تومان", "").replace("نامشخص", "").replace("جستجو در مدل‌ها", "").strip()
        parts = name.split()
        brand = parts[0] if len(parts) >= 2 else name
        model = " ".join(parts[1:]) if len(parts) >= 2 else ""
        if brand in valid_brands:
            brands.append(brand)
            models.append(model)
        else:
            models.append(brand + " " + model)
            brands.append("")

    return brands[25:], models[25:]

def is_number(model_str):
    try:
        float(model_str.replace(",", ""))
        return True
    except ValueError:
        return False

def process_model(model_str):
    # حذف کاراکترهای غیرضروری و بررسی اینکه آیا مقدار عددی است
    model_str = model_str.replace("٬", "").replace(",", "").strip()
    if is_number(model_str):
        model_value = float(model_str)
        # اعمال درصدهای مختلف بر اساس بازه عددی
        if model_value <= 1:
            model_value_with_increase = model_value * 0
        elif model_value <= 7000000:
            model_value_with_increase = model_value + 260000 
        elif model_value <= 10000000:
            model_value_with_increase = model_value * 1.035
        elif model_value <= 20000000:
            model_value_with_increase = model_value * 1.025
        elif model_value <= 30000000:
            model_value_with_increase = model_value * 1.02
        elif model_value <= 40000000:
            model_value_with_increase = model_value * 1.015
        else:  # مقادیر بالاتر از 40000000
            model_value_with_increase = model_value * 1.015
        
        # گرد کردن مقدار به 5 رقم آخر
        model_value_with_increase = round(model_value_with_increase, -5)
        return f"{model_value_with_increase:,.0f}"  # فرمت دهی عدد نهایی
    return model_str  # اگر مقدار عددی نباشد، همان مقدار اولیه بازگردانده می‌شود


def escape_markdown(text):
    escape_chars = ['\\', '(', ')', '[', ']', '~', '*', '_', '-', '+', '>', '#', '.', '!', '|']
    for char in escape_chars:
        text = text.replace(char, '\\' + char)
    return text

def split_message(message, max_length=4000):
    return [message[i:i+max_length] for i in range(0, len(message), max_length)]

def decorate_line(line):
    if line.startswith(('🔵', '🟡', '🍏', '🟣', '💻', '🟠', '🎮')):
        return line  
    if any(keyword in line for keyword in ["Nartab", "Tab", "تبلت"]):
        return f"🟠 {line}"
    elif "Galaxy" in line:
        return f"🔵 {line}"
    elif "POCO" in line or "Poco" in line or "Redmi" in line:
        return f"🟡 {line}"
    elif "iPhone" in line:
        return f"🍏 {line}"
    elif any(keyword in line for keyword in ["اینچی", "لپ تاپ"]):
        return f"💻 {line}"   
    elif any(keyword in line for keyword in ["RAM", "FA", "Classic", "Otel", "DOX"]):
        return f"🟣 {line}"
    elif any(keyword in line for keyword in ["Play Station", "کنسول بازی", "پلی استیشن", "بازی"]):  # اضافه کردن کلمات کلیدی کنسول بازی
        return f"🎮 {line}"
    else:
        return line

def sort_lines_together_by_price(lines):
    def extract_price(group):
        # این تابع قیمت را از آخرین خط هر گروه استخراج می‌کند
        for line in reversed(group):
            parts = line.split()
            for part in parts:
                try:
                    return float(part.replace(',', '').replace('،', ''))  # حذف کاما و تبدیل قیمت به عدد
                except ValueError:
                    continue
        return float('inf')  # مقدار پیش‌فرض برای گروه‌های بدون قیمت

    # تبدیل خطوط به گروه‌ها (حفظ ارتباط میان اطلاعات هر محصول)
    grouped_lines = []
    current_group = []
    for line in lines:
        if line.startswith(("🔵", "🟡", "🍏", "🟣", "💻", "🟠", "🎮")):
            if current_group:
                grouped_lines.append(current_group)
            current_group = [line]
        else:
            current_group.append(line)
    if current_group:
        grouped_lines.append(current_group)

    # مرتب‌سازی گروه‌ها براساس قیمت
    grouped_lines.sort(key=extract_price)

    # تبدیل گروه‌های مرتب‌شده به لیستی از خطوط
    sorted_lines = [line for group in grouped_lines for line in group]
    return sorted_lines

def remove_extra_blank_lines(lines):
    cleaned_lines = []
    blank_count = 0

    for line in lines:
        if line.strip() == "":  # بررسی خطوط خالی
            blank_count += 1
            if blank_count <= 1:  # فقط یک خط خالی نگه‌دار
                cleaned_lines.append(line)
        else:
            blank_count = 0
            cleaned_lines.append(line)

    return cleaned_lines
    
def prepare_final_message(category_name, category_lines, update_date):
        # گرفتن عنوان دسته از روی ایموجی
    category_title = get_category_name(category_name)
    # دریافت تاریخ امروز به شمسی
    update_date = JalaliDate.today().strftime("%Y/%m/%d")
    # تعریف نگاشت برای روزهای هفته به فارسی
    weekday_mapping = {
            "Saturday": "شنبه💪",
            "Sunday": "یکشنبه😃",
            "Monday": "دوشنبه☺️",
            "Tuesday": "سه شنبه🥱",
            "Wednesday": "چهارشنبه😕",
            "Thursday": "پنج شنبه☺️",
            "Friday": "جمعه😎"
    }
    weekday_english = JalaliDate.today().weekday()  # گرفتن ایندکس روز هفته
    weekday_farsi = list(weekday_mapping.values())[weekday_english]  # تبدیل ایندکس به روز فارسی
    update_date_formatted = f"{weekday_farsi} {update_date.replace('-', '/')}"

    print(f"نام روز هفته به انگلیسی: {weekday_english}")
    print(update_date_formatted)  # برای تست

    # ساخت هدر پیام
    header = (
        f"🗓 بروزرسانی {update_date_formatted}\n"
        f"✅ لیست پخش موبایل اهورا\n\n"
        f"⬅️ موجودی {category_title} ➡️\n\n"
    )

    formatted_lines = []
    current_product = None
    product_variants = []

    i = 0
    while i < len(category_lines):
        line = category_lines[i]

        if line.startswith(("🔵", "🟡", "🍏", "🟣", "💻", "🟠", "🎮")):
            # اگر محصول قبلی وجود داشت، اضافه‌اش کن
            if current_product:
                formatted_lines.append(current_product)
                if product_variants:
                    formatted_lines.extend(product_variants)
                formatted_lines.append("")  # اضافه کردن یک خط فاصله بین گوشی‌ها
                product_variants = []
            current_product = line.strip()
            i += 1
        else:
            # ترکیب رنگ و قیمت با فرض اینکه پشت سر هم هستند
            if i + 1 < len(category_lines):
                color = line.strip()
                price = category_lines[i + 1].strip()
                product_variants.append(f"{color} | {price}")
                i += 2
            else:
                # خط ناقص، فقط رنگ یا قیمت موجوده
                product_variants.append(line.strip())
                i += 1

    # افزودن آخرین محصول
    if current_product:
        formatted_lines.append(current_product)
        if product_variants:
            formatted_lines.extend(product_variants)

    # حذف | از سطرهایی که ایموجی دارند
    formatted_lines = [
        line for line in formatted_lines
        if not any(emoji in line for emoji in ["🔵", "🟡", "🍏", "🟣", "💻", "🟠", "🎮"]) or "|" not in line
    ]

    footer = "\n\n☎️ شماره های تماس :\n📞 09371111558\n📞 02833991417"
    final_message = f"{header}" + "\n".join(formatted_lines) + f"{footer}"

    return final_message




# این تابع کمکی برای گرفتن اسم دسته‌بندی‌ها
def get_category_name(emoji):
    mapping = {
        "🔵": "سامسونگ",
        "🟡": "شیائومی",
        "🍏": "آیفون",
        "💻": "لپ‌تاپ‌ها",
        "🟠": "تبلت‌ها",
        "🎮": "کنسول‌ بازی"
    }
    return mapping.get(emoji, "گوشیای متفرقه")

def categorize_messages(lines):
    categories = {"🔵": [], "🟡": [], "🍏": [], "🟣": [], "💻": [], "🟠": [], "🎮": []}  # اضافه کردن 🎮 برای کنسول بازی
    
    current_category = None

    for line in lines:
        if line.startswith("🔵"):
            current_category = "🔵"
        elif line.startswith("🟡"):
            current_category = "🟡"
        elif line.startswith("🍏"):
            current_category = "🍏"
        elif line.startswith("🟣"):
            current_category = "🟣"
        elif line.startswith("💻"):
            current_category = "💻"
        elif line.startswith("🟠"):  # اضافه کردن شرط برای تبلت
            current_category = "🟠"
        elif line.startswith("🎮"):  # اضافه کردن شرط برای کنسول بازی
            current_category = "🎮"
            
        if current_category:
            categories[current_category].append(line)

    # مرتب‌سازی و حذف خطوط خالی اضافی در هر دسته‌بندی
    for category in categories:
        categories[category] = sort_lines_together_by_price(categories[category])  # مرتب‌سازی
        categories[category] = remove_extra_blank_lines(categories[category])  # حذف خطوط خالی

    return categories

def send_telegram_message(message, bot_token, chat_id, reply_markup=None):
    message_parts = split_message(message)
    last_message_id = None
    for part in message_parts:
        part = escape_markdown(part)
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        params = {
            "chat_id": chat_id,
            "text": part,
            "parse_mode": "MarkdownV2"
        }
        if reply_markup:
            params["reply_markup"] = json.dumps(reply_markup)  # ✅ تبدیل `reply_markup` به JSON

        headers = {"Content-Type": "application/json"}  # ✅ اضافه کردن `headers` برای `POST`
        response = requests.post(url, json=params, headers=headers)  
        response_data = response.json()
        if response_data.get('ok'):
            last_message_id = response_data["result"]["message_id"]
        else:
            logging.error(f"❌ خطا در ارسال پیام: {response_data}")
            return None

    logging.info("✅ پیام ارسال شد!")
    return last_message_id  # برگشت message_id آخرین پیام


def get_last_messages(bot_token, chat_id, limit=5):
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    response = requests.get(url)
    if response.json().get("ok"):
        messages = response.json().get("result", [])
        return [msg for msg in messages if "message" in msg][-limit:]
    return []

def get_worksheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials_str = os.environ.get("GSHEET_CREDENTIALS_JSON")
        credentials_dict = json.loads(credentials_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = sheet.worksheet(SHEET_NAME)
        logging.info("✅ اتصال به Google Sheets برقرار شد.")
        return worksheet
    except Exception as e:
        logging.error(f"❌ خطا در اتصال به Google Sheets: {e}")
        return None


def check_and_add_headers():
    ws = get_worksheet()
    rows = ws.get_all_values()
    if not rows:
        ws.append_row(["تاریخ", "شناسه پیام", "دسته‌بندی", "متن پیام"])

def get_message_id_and_text_from_sheet(today, category):
    ws = get_worksheet()
    rows = ws.get_all_values()
    headers = rows[0]
    for row in rows[1:]:
        record = dict(zip(headers, row))
        if record.get("تاریخ") == today and record.get("دسته‌بندی") == category:
            try:
                return int(record.get("شناسه پیام", 0)), record.get("متن پیام", "")
            except (ValueError, TypeError):
                return None, ""
    return None, ""



def save_message_id_and_text_to_sheet(today, category, message_id, text):
    try:
        ws = get_worksheet()
        if not ws:
            logging.error("❌ امکان اتصال به Google Sheets وجود ندارد.")
            return
        
        # خطایابی: تست ذخیره با داده‌های ساده
        logging.info("🔍 درحال تست ذخیره‌سازی با داده‌های ساده")
        ws.append_row(["تست تاریخ", "تست شناسه", "تست دسته‌بندی", "تست متن پیام"])

        # خطایابی: ذخیره داده‌های اصلی
        logging.info(f"🔍 درحال ذخیره‌سازی داده‌ها: تاریخ={today}, دسته‌بندی={category}, پیام ID={message_id}, متن={text}")
        ws.append_row([today, str(message_id), category, text])
        logging.info("✅ داده‌ها با موفقیت به Google Sheets اضافه شدند.")
    except Exception as e:
        logging.error(f"❌ خطا در ذخیره داده‌ها به Google Sheets: {e}")







# --- ویرایش منطق ارسال پیام ---
def send_or_edit_message(category, lines, update_date):
    today = JalaliDate.today().strftime("%Y-%m-%d")
    message_id, current_text = get_message_id_and_text_from_sheet(today, category)
    
    message = prepare_final_message(category, lines, update_date)
    
    if message_id:
        if message != current_text:
            edit_telegram_message(message_id, message, current_text)
            logging.info(f"✅ پیام دسته {category} ویرایش شد.")
    else:
        new_id = send_telegram_message(message, BOT_TOKEN, CHAT_ID)
        save_message_id_and_text_to_sheet(today, category, new_id, message)
        logging.info(f"✅ پیام جدید دسته {category} ارسال و ذخیره شد.")


def edit_telegram_message(message_id, new_text, current_text):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
        params = {
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "text": new_text,
            "parse_mode": "MarkdownV2"
        }

        # درخواست ویرایش پیام
        response = requests.post(url, json=params)
        response_data = response.json()

        if response_data.get('ok'):
            logging.info(f"✅ پیام با شناسه {message_id} با موفقیت ویرایش شد.")
        else:
            logging.error(f"❌ خطا در ویرایش پیام: {response_data}")
    except Exception as e:
        logging.error(f"❌ خطا در فراخوانی editMessageText: {e}")


def check_and_add_headers():
    try:
        # اتصال به شیت
        ws = get_worksheet()
        rows = ws.get_all_values()
        
        # بررسی اینکه آیا شیت خالی است یا اینکه هدرها موجود هستند
        if not rows or rows[0] != ["تاریخ", "شناسه پیام", "دسته‌بندی", "متن پیام"]:
            ws.insert_row(["تاریخ", "شناسه پیام", "دسته‌بندی", "متن پیام"], 1)  # اضافه کردن هدر به سطر اول
            logging.info("✅ هدرها به Google Sheets اضافه شدند.")
        else:
            logging.info("✅ هدرها موجود هستند و نیاز به تغییر ندارند.")
    except Exception as e:
        logging.error(f"❌ خطا در بررسی یا ایجاد هدرها: {e}")

def get_last_update_date():
    try:
        ws = get_worksheet()
        rows = ws.get_all_values()
        if len(rows) > 1:  # اگر اطلاعات ذخیره شده باشد
            last_row = rows[-1]
            return last_row[0]  # ستون اول (تاریخ) را برگرداند
        return None
    except Exception as e:
        logging.error(f"❌ خطا در بازیابی تاریخ آخرین به‌روزرسانی: {e}")
        return None


def main():
    try:
        # تنظیم WebDriver
        driver = get_driver()
        if not driver:
            logging.error("❌ نمی‌توان WebDriver را ایجاد کرد.")
            return

        # استخراج داده‌ها از همه دسته‌بندی‌ها
        logging.info("✅ شروع به استخراج داده‌ها...")
        valid_brands = ["Galaxy", "POCO", "Redmi", "iPhone", "Redtone", "VOCAL", "TCL", "NOKIA", "Honor", "Huawei", "GLX", "+Otel", "اینچی"]
        brands, models = extract_all_data(driver, valid_brands)

        if not brands or not models:
            logging.error("❌ هیچ داده‌ای استخراج نشد.")
            return

        logging.info(f"🔍 داده‌های استخراج‌شده: برندها={brands}, مدل‌ها={models}")

        # بررسی و ایجاد هدرها در Google Sheets
        check_and_add_headers()

        # دریافت تاریخ امروز و بازیابی تاریخ ذخیره‌شده
        today = JalaliDate.today().strftime("%Y-%m-%d")
        last_update_date = get_last_update_date()

        # مقایسه داده‌ها و شناسایی تغییرات
        changes_detected = compare_with_sheet_data(brands, models)

        if changes_detected:
            logging.info("✅ تغییرات شناسایی شد، ارسال پیام‌ها و ایجاد دکمه‌ها...")
            send_new_posts(brands, models, today)
            update_last_update_date(today)  # به‌روزرسانی تاریخ ذخیره‌شده
        else:
            logging.info("✅ داده‌ها تغییر نکرده‌اند. نیازی به ارسال یا ویرایش نیست.")

        # خروج از WebDriver
        driver.quit()

    except Exception as e:
        logging.error(f"❌ خطا در اجرای برنامه: {e}")


def extract_all_data(driver, valid_brands):
    try:
        categories = ["mobile", "laptop", "tablet", "game-console"]
        all_brands, all_models = [], []

        for category in categories:
            logging.info(f"✅ استخراج داده‌ها برای دسته {category}...")
            driver.get(f'https://hamrahtel.com/quick-checkout?category={category}')
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CLASS_NAME, 'mantine-Text-root')))
            scroll_page(driver)
            brands, models = extract_product_data(driver, valid_brands)
            all_brands.extend(brands)
            all_models.extend(models)

        return all_brands, all_models
    except Exception as e:
        logging.error(f"❌ خطا در استخراج داده‌ها: {e}")
        return [], []


def compare_with_sheet_data(brands, models):
    try:
        # بازیابی داده‌های موجود از Google Sheets
        sheet_data = get_sheet_data()

        # مقایسه داده‌ها و شناسایی تغییرات
        new_data = set(zip(brands, models))
        existing_data = set(sheet_data)

        if new_data != existing_data:
            logging.info("✅ داده‌های جدید با داده‌های موجود تفاوت دارند.")
            return True
        else:
            logging.info("✅ داده‌های جدید مشابه داده‌های موجود هستند.")
            return False
    except Exception as e:
        logging.error(f"❌ خطا در مقایسه داده‌ها: {e}")
        return False


def send_new_posts(brands, models, today):
    try:
        # ایجاد و ارسال پیام‌های دسته‌بندی‌شده
        logging.info("✅ شروع به ارسال پیام‌های جدید...")
        processed_data = []
        for i in range(len(brands)):
            model_str = process_model(models[i])
            processed_data.append(f"{model_str} {brands[i]}")

        message_lines = []
        for row in processed_data:
            decorated = decorate_line(row)
            message_lines.append(decorated)

        categories = categorize_messages(message_lines)
        message_ids = {}

        for category, lines in categories.items():
            if lines:
                message = prepare_final_message(category, lines, today)
                message = escape_markdown(message)
                msg_id = send_telegram_message(message, BOT_TOKEN, CHAT_ID)
                if msg_id:
                    save_message_id_and_text_to_sheet(today, category, msg_id, message)
                    message_ids[category] = msg_id
                    logging.info(f"✅ پیام دسته {category} ارسال شد.")

        # ایجاد دکمه‌ها و ارسال پیام پایانی
        button_markup = create_buttons(message_ids)
        send_final_message(button_markup)

    except Exception as e:
        logging.error(f"❌ خطا در ارسال پیام‌های جدید: {e}")


def create_buttons(message_ids):
    button_markup = {"inline_keyboard": []}

    for category, msg_id in message_ids.items():
        if category == "🔵":
            button_markup["inline_keyboard"].append([{"text": "📱 لیست سامسونگ", "url": f"https://t.me/c/{CHAT_ID.replace('-100', '')}/{msg_id}"}])
        elif category == "🟡":
            button_markup["inline_keyboard"].append([{"text": "📱 لیست شیائومی", "url": f"https://t.me/c/{CHAT_ID.replace('-100', '')}/{msg_id}"}])
        elif category == "🍏":
            button_markup["inline_keyboard"].append([{"text": "📱 لیست آیفون", "url": f"https://t.me/c/{CHAT_ID.replace('-100', '')}/{msg_id}"}])
        elif category == "💻":
            button_markup["inline_keyboard"].append([{"text": "💻 لیست لپ‌تاپ", "url": f"https://t.me/c/{CHAT_ID.replace('-100', '')}/{msg_id}"}])
        elif category == "🟠":
            button_markup["inline_keyboard"].append([{"text": "📱 لیست تبلت", "url": f"https://t.me/c/{CHAT_ID.replace('-100', '')}/{msg_id}"}])
        elif category == "🎮":
            button_markup["inline_keyboard"].append([{"text": "🎮 کنسول بازی", "url": f"https://t.me/c/{CHAT_ID.replace('-100', '')}/{msg_id}"}])

    return button_markup


def send_final_message(button_markup):
    final_message = (
        "✅ لیست گوشی و سایر کالاهای بالا بروز شده است. ثبت خرید تا ساعت 10:30 شب انجام می‌شود و تحویل کالا ساعت 11:30 صبح روز بعد خواهد بود.\n\n"
        "✅ اطلاعات واریز:\n"
        "🔷 شماره شبا: IR970560611828006154229701\n"
        "🔷 شماره کارت: 6219861812467917\n"
        "🔷 بلو بانک: حسین گرئی\n\n"
        "⭕️ لطفاً رسید واریز را به آیدی تلگرام زیر ارسال کنید:\n"
        "🆔 @lhossein1\n\n"
        "✅ شماره‌های تماس ثبت سفارش:\n"
        "📞 09371111558\n"
        "📞 09386373926\n"
        "📞 09308529712\n"
        "📞 028-3399-1417"
    )

    send_telegram_message(final_message, BOT_TOKEN, CHAT_ID, reply_markup=button_markup)


if __name__ == "__main__":
    main()

