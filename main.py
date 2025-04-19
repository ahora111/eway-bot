#!/usr/bin/env python3
import os
import time
import requests
import logging
import json
import pytz
import sys
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, time as dt_time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from persiantools.jdatetime import JalaliDate

# تنظیمات ربات تلگرام و Google Sheets
BOT_TOKEN = "8187924543:AAH0jZJvZdpq_34um8R_yCyHQvkorxczXNQ"
CHAT_ID = "-1002505490886"
SPREADSHEET_ID = '1nMtYsaa9_ZSGrhQvjdVx91WSG4gANg2R0s4cSZAZu7E'
SHEET_NAME = 'Sheet1'

# تنظیمات لاگ
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# منطقه زمانی ایران
iran_tz = pytz.timezone('Asia/Tehran')
now = datetime.now(iran_tz)
current_time = now.time()
weekday = now.weekday()  # 0=دوشنبه، ..., 4=جمعه، 6=شنبه

# تعریف بازه‌های زمانی مجاز
start_time = dt_time(9, 30)
end_time = dt_time(22, 30)
friday_allowed_times = [
    dt_time(12, 0),
    dt_time(14, 0),
    dt_time(16, 0),
    dt_time(18, 0),
    dt_time(20, 0),
]

# بررسی بازه زمانی
if weekday == 4:  # جمعه
    if not any(abs((datetime.combine(now.date(), t) - datetime.combine(now.date(), current_time)).total_seconds()) < 150 for t in friday_allowed_times):
        logging.info("🕌 امروز جمعه است و جزو زمان‌های مجاز نیست. برنامه متوقف شد.")
        sys.exit()
else:
    if not (start_time <= current_time <= end_time):
        logging.info("🕒 خارج از بازه زمانی مجاز اجرا (۹:۳۰ تا ۲۲:۳۰). برنامه متوقف شد.")
        sys.exit()

# تابع راه‌اندازی WebDriver
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

# تابع اسکرول صفحه
def scroll_page(driver, scroll_pause_time=2):
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause_time)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

# تابع استخراج داده‌ها
def extract_product_data(driver, valid_brands):
    product_elements = driver.find_elements(By.CLASS_NAME, 'mantine-Text-root')
    brands, models = [], []
    for product in product_elements:
        name = product.text.strip().replace("تومان", "").strip()
        parts = name.split()
        brand = parts[0] if len(parts) >= 2 else name
        model = " ".join(parts[1:]) if len(parts) >= 2 else ""
        if brand in valid_brands:
            brands.append(brand)
            models.append(model)
        else:
            models.append(name)
            brands.append("")
    return brands, models

# اتصال به Google Sheets
def connect_to_google_sheets():
    json_credentials = os.getenv("GSHEET_CREDENTIALS_JSON")
    if not json_credentials:
        raise FileNotFoundError("❌ فایل JSON یافت نشد.")
    with open("temp_gsheet_credentials.json", "w") as temp_file:
        temp_file.write(json_credentials)
    credentials = Credentials.from_service_account_file('temp_gsheet_credentials.json', scopes=["https://www.googleapis.com/auth/spreadsheets"])
    client = gspread.authorize(credentials)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
    os.remove("temp_gsheet_credentials.json")
    return sheet

# مقداردهی اولیه Google Sheets
def initialize_google_sheet(sheet):
    headers = ['تاریخ', 'مسیج آی‌دی', 'شناسه', 'متن پیام']
    if not sheet.get_all_records():
        sheet.append_row(headers)
        logging.info("✅ شیت مقداردهی اولیه شد.")

# ثبت دسته‌ای داده‌ها
def batch_update_google_sheet(sheet, data):
    rows = [[item['date'], item['message_id'], item['identifier'], item['text']] for item in data]
    sheet.append_rows(rows)
    logging.info("✅ داده‌ها به‌صورت دسته‌ای ثبت شدند.")

# ارسال پیام تلگرام
def send_telegram_message(message, bot_token, chat_id):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    response = requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "MarkdownV2"})
    if response.json().get("ok"):
        logging.info("✅ پیام تلگرام ارسال شد.")
        return response.json()["result"]["message_id"]
    else:
        logging.error(f"❌ خطا در ارسال پیام تلگرام: {response.json()}")
        return None

# تابع اصلی
def main():
    try:
        # اتصال به Google Sheets
        sheet = connect_to_google_sheets()
        initialize_google_sheet(sheet)

        # راه‌اندازی WebDriver و استخراج داده‌ها
        driver = get_driver()
        if not driver:
            return
        driver.get('https://hamrahtel.com/quick-checkout?category=mobile')
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CLASS_NAME, 'mantine-Text-root')))
        scroll_page(driver)

        # فیلتر برندها و استخراج داده‌ها
        valid_brands = ["Galaxy", "POCO", "Redmi", "iPhone", "Honor"]
        brands, models = extract_product_data(driver, valid_brands)

        # آماده‌سازی داده‌ها برای Google Sheets
        data = [{"date": JalaliDate.today().strftime("%Y-%m-%d"), "message_id": None, "identifier": brand, "text": model} for brand, model in zip(brands, models)]
        batch_update_google_sheet(sheet, data)

        # بستن WebDriver
        driver.quit()
    except Exception as e:
        logging.error(f"❌ خطا در اجرای برنامه: {e}")

# اجرای برنامه
if __name__ == "__main__":
    main()
