#!/usr/bin/env python3
# -*- coding: utf-8 -*-

## 📌 بخش 1: وارد کردن کتابخانه‌های مورد نیاز
import os
import time
import requests
import logging
import json
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
from tenacity import retry, stop_after_attempt

## 📌 بخش 2: تنظیمات اولیه
# تنظیمات لاگ‌گیری
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

# تنظیمات تلگرام (بهتره از متغیرهای محیطی استفاده بشه)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8187924543:AAH0jZJvZdpq_34um8R_yCyHQvkorxczXNQ")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1002505490886")

# تنظیمات Google Sheets
SPREADSHEET_ID = '1nMtYsaa9_ZSGrhQvjdVx91WSG4gANg2R0s4cSZAZu7E'
SHEET_NAME = 'Sheet1'

## 📌 بخش 3: توابع اصلی

### 🛠 تابع ایجاد درایور Chrome
def get_driver():
    """ایجاد و پیکربندی WebDriver برای کروم"""
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")  # اجرای بدون نمایش مرورگر
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        service = Service()
        driver = webdriver.Chrome(service=service, options=options)
        return driver
    except Exception as e:
        logging.error(f"خطا در ایجاد WebDriver: {e}")
        return None

### 🔄 تابع اسکرول صفحه
def scroll_page(driver, scroll_pause_time=2, timeout=60):
    """اسکرول کامل صفحه تا بارگذاری تمام محتوا"""
    last_height = driver.execute_script("return document.body.scrollHeight")
    start_time = time.time()
    
    while time.time() < start_time + timeout:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause_time)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
    else:
        logging.warning("اسکرول به دلیل timeout متوقف شد")

### 📊 تابع استخراج داده‌های محصولات
def extract_product_data(driver, valid_brands):
    """استخراج نام و مدل محصولات از صفحه وب"""
    try:
        product_elements = WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located((By.CLASS_NAME, 'mantine-Text-root'))
        )
        
        brands, models = [], []
        for product in product_elements:
            name = product.text.strip()
            # پاکسازی متن
            name = name.replace("تومانءء", "").replace("تومان", "").replace("نامشخص", "").replace("جستجو در مدل‌ها", "").strip()
            
            # تقسیم نام به برند و مدل
            parts = name.split()
            if not parts:
                continue
                
            brand = parts[0] if len(parts) >= 2 else name
            model = " ".join(parts[1:]) if len(parts) >= 2 else ""
            
            if brand in valid_brands:
                brands.append(brand)
                models.append(model)
            else:
                models.append(f"{brand} {model}".strip())
                brands.append("")
        
        return brands[25:], models[25:]  # حذف 25 آیتم اول (معمولاً هدرها)
    
    except Exception as e:
        logging.error(f"خطا در استخراج داده‌ها: {e}")
        return [], []

### 🔢 تابع پردازش مدل محصولات
def process_model(model_str):
    """پردازش و فرمت‌دهی مدل محصولات"""
    if not model_str or not isinstance(model_str, str):
        return model_str
        
    try:
        # پاکسازی متن و تبدیل به عدد
        cleaned = model_str.replace("٬", "").replace(",", "").strip()
        if not cleaned:
            return model_str
            
        model_value = float(cleaned)
        
        # اعمال درصد افزایش بر اساس بازه قیمتی
        if model_value <= 1:
            return "0"
        elif model_value <= 7_000_000:
            increased = model_value + 260_000
        elif model_value <= 10_000_000:
            increased = model_value * 1.035
        elif model_value <= 20_000_000:
            increased = model_value * 1.025
        elif model_value <= 30_000_000:
            increased = model_value * 1.02
        elif model_value <= 40_000_000:
            increased = model_value * 1.015
        else:
            increased = model_value * 1.015
        
        # گرد کردن و فرمت‌دهی
        rounded = round(increased, -5)  # گرد کردن به 100 هزار تومان
        return f"{rounded:,.0f}".replace(",", "،")  # تبدیل کاما به ویرگول فارسی
    
    except ValueError:
        return model_str  # اگر عدد نبود، متن اصلی برگردانده می‌شود

### ✉️ توابع مرتبط با تلگرام
def escape_markdown(text):
    """پاکسازی متن برای فرمت MarkdownV2 تلگرام"""
    if not text:
        return ""
        
    escape_chars = ['\\', '(', ')', '[', ']', '~', '*', '_', '-', '+', '>', '#', '.', '!', '|']
    for char in escape_chars:
        text = text.replace(char, '\\' + char)
    return text

def send_telegram_message(message, bot_token, chat_id, reply_markup=None):
    """ارسال پیام به تلگرام با قابلیت تقسیم پیام‌های طولانی"""
    if not message or not message.strip():
        logging.warning("پیام خالی برای ارسال به تلگرام")
        return None
        
    try:
        # تقسیم پیام‌های طولانی
        max_length = 4000
        message_parts = [message[i:i+max_length] for i in range(0, len(message), max_length)]
        last_msg_id = None
        
        for part in message_parts:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            params = {
                "chat_id": chat_id,
                "text": escape_markdown(part),
                "parse_mode": "MarkdownV2"
            }
            
            if reply_markup:
                params["reply_markup"] = json.dumps(reply_markup)
                
            response = requests.post(url, json=params)
            response.raise_for_status()
            
            if response.json().get('ok'):
                last_msg_id = response.json()["result"]["message_id"]
            else:
                logging.error(f"خطا در ارسال پیام: {response.text}")
                
        return last_msg_id
        
    except Exception as e:
        logging.error(f"خطا در ارسال به تلگرام: {e}")
        return None

## 📌 بخش 4: توابع مدیریت Google Sheets
@retry(stop=stop_after_attempt(3))
def get_worksheet():
    """اتصال به Google Sheets با اعتبارسنجی OAuth2"""
    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        
        # خواندن اعتبارنامه از متغیر محیطی
        creds_json = os.getenv("GSHEET_CREDENTIALS_JSON")
        if not creds_json:
            raise ValueError("اعتبارنامه Google Sheets یافت نشد")
            
        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            json.loads(creds_json), scope)
            
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = sheet.worksheet(SHEET_NAME)
        
        logging.info("اتصال به Google Sheets با موفقیت برقرار شد")
        return worksheet
        
    except Exception as e:
        logging.error(f"خطا در اتصال به Google Sheets: {e}")
        return None

## 📌 بخش 5: توابع اصلی برنامه
def main():
    """تابع اصلی اجرای برنامه"""
    driver = None
    try:
        # 1. راه‌اندازی درایور
        driver = get_driver()
        if not driver:
            raise RuntimeError("نمیتوان WebDriver را ایجاد کرد")
            
        # 2. استخراج داده‌ها
        categories_to_scrape = {
            "mobile": "گوشی موبایل",
            "laptop": "لپ‌تاپ",
            "tablet": "تبلت",
            "game-console": "کنسول بازی"
        }
        
        all_brands, all_models = [], []
        
        for category, name in categories_to_scrape.items():
            logging.info(f"در حال استخراج داده‌های {name}...")
            driver.get(f'https://hamrahtel.com/quick-checkout?category={category}')
            scroll_page(driver)
            
            valid_brands = ["Galaxy", "POCO", "Redmi", "iPhone", "Redtone", "VOCAL", "TCL", "NOKIA", "Honor", "Huawei", "GLX", "+Otel", "اینچی"]
            brands, models = extract_product_data(driver, valid_brands)
            
            all_brands.extend(brands)
            all_models.extend(models)
        
        # 3. پردازش و دسته‌بندی داده‌ها
        processed_data = [
            decorate_line(f"{process_model(model)} {brand}".strip())
            for brand, model in zip(all_brands, all_models)
        ]
        
        categorized = categorize_messages(processed_data)
        today = JalaliDate.today().strftime("%Y-%m-%d")
        
        # 4. بررسی تغییرات و تصمیم‌گیری برای ارسال/ویرایش
        last_data = get_last_data_from_sheet()
        data_changed = (json.dumps(categorized) != last_data) if last_data else True
        date_changed = (today != get_last_update_date())
        
        if data_changed and date_changed:
            send_new_posts(categorized, today)
        elif data_changed and not date_changed:
            update_existing_posts(categorized, today)
        elif not data_changed and date_changed:
            send_new_posts(categorized, today)
        else:
            logging.info("داده‌ها و تاریخ تغییری نکرده‌اند. عملیاتی انجام نمی‌شود.")
            
    except Exception as e:
        logging.error(f"خطای غیرمنتظره: {e}", exc_info=True)
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    main()
