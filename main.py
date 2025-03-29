#!/usr/bin/env python3
import os
import time
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from persiantools.jdatetime import JalaliDate

# تنظیمات مربوط به تلگرام
BOT_TOKEN = "8187924543:AAH0jZJvZdpq_34um8R_yCyHQvkorxczXNQ"
CHAT_ID = "1233959486"

def get_driver():
    """ایجاد و بازگرداندن یک نمونه WebDriver headless برای Chrome."""
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    service = Service()  # مطمئن شوید chromedriver در PATH شما قرار دارد
    driver = webdriver.Chrome(service=service, options=options)
    return driver

def scroll_page(driver, scroll_pause_time=2):
    """صفحه را به پایین اسکرول می‌کند تا محتوای داینامیک بارگذاری شود."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause_time)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

def extract_product_data(driver, valid_brands):
    """
    استخراج داده‌های محصولات از المان‌های صفحه.
    
    Returns:
        سه لیست: brands, models, dates (شروع از ایندکس 25)
    """
    product_elements = driver.find_elements(By.CLASS_NAME, 'mantine-Text-root')
    brands, models, dates = [], [], []
    for product in product_elements:
        name = product.text.strip().replace("تومانءء", "").replace("تومان", "").replace("نامشخص", "").strip()
        parts = name.split()
        brand = parts[0] if len(parts) >= 2 else name
        model = " ".join(parts[1:]) if len(parts) >= 2 else ""
        if brand in valid_brands:
            brands.append(brand)
            models.append(model)
            dates.append("")
        else:
            models.append(brand + " " + model)
            brands.append("")
            dates.append("")
    return brands[25:], models[25:], dates[25:]

def is_number(model_str):
    """بررسی می‌کند که آیا رشته ورودی یک عدد است یا خیر."""
    try:
        float(model_str.replace(",", ""))
        return True
    except ValueError:
        return False

def process_model(model_str):
    """
    اگر مقدار مدل عددی باشد، مقدار را 1.5% افزایش داده و به صورت فرمت شده برمی‌گرداند.
    """
    model_str = model_str.replace("٬", "").replace(",", "").strip()
    if is_number(model_str):
        model_value = float(model_str)
        model_value_with_increase = model_value * 1.015
        return f"{model_value_with_increase:,.0f}"
    return model_str

def send_telegram_message(message, bot_token, chat_id):
    """ارسال پیام به تلگرام از طریق Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    params = {"chat_id": chat_id, "text": message}
    response = requests.get(url, params=params)
    return response.json()

def main():
    try:
        driver = get_driver()
        driver.get('https://hamrahtel.com/quick-checkout')
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CLASS_NAME, 'mantine-Text-root')))
        print("✅ داده‌ها آماده‌ی استخراج هستند!")
        scroll_page(driver)
        
        valid_brands = ["Galaxy", "POCO", "Redmi", "iPhone", "Redtone", "VOCAL", "TCL", "NOKIA", "Honor", "Huawei", "GLX", "+Otel"]
        brands, models, dates = extract_product_data(driver, valid_brands)
        driver.quit()

        if brands:
            processed_data = []
            for i in range(len(brands)):
                model_str = process_model(models[i])
                update_date = JalaliDate.today().strftime("%Y-%m-%d")
                processed_data.append((model_str, brands[i], update_date))
            
            # آماده‌سازی پیام برای تلگرام (هر ردیف در یک خط)
            message_lines = ["خلاصه داده‌های استخراج‌شده:"]
            for row in processed_data:
                message_lines.append(" | ".join(row))
            message = "\n".join(message_lines)
            
            # چاپ نمونه خروجی در کنسول
            print("نمونه خروجی:")
            print(message)
            
            # ارسال پیام به تلگرام
            telegram_response = send_telegram_message(message, BOT_TOKEN, CHAT_ID)
            print("✅ داده‌ها با موفقیت به تلگرام ارسال شدند!")
            print("پاسخ تلگرام:", telegram_response)
        else:
            print("❌ داده‌ای برای ارسال وجود ندارد!")
    except Exception as e:
        print(f"❌ خطا: {e}")

if __name__ == "__main__":
    main()
