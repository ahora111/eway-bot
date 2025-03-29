import json
import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from persiantools.jdatetime import JalaliDate
from telegram import Bot

# تنظیمات
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def get_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    service = Service()
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def scroll_page(driver):
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height


def extract_product_data(driver, valid_brands):
    product_elements = driver.find_elements(By.CLASS_NAME, 'mantine-Text-root')
    brands, models, prices, colors = [], [], [], []

    for product in product_elements:
        name = product.text.strip().replace("تومانءء", "").replace("تومان", "").replace("نامشخص", "").strip()
        parts = name.split()
        if len(parts) >= 3:
            brand = parts[0]
            model = " ".join(parts[1:-1])
            price = parts[-1].replace(",", "")
            color = parts[-2] if len(parts) > 2 else 'نامشخص'
            if brand in valid_brands and price.isdigit():
                brands.append(brand)
                models.append(model)
                prices.append(price)
                colors.append(color)

    return brands, models, prices, colors


def send_telegram_message(brands, models, prices, colors, error_message=None):
    bot = Bot(token=TELEGRAM_TOKEN)
    if error_message:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"❗️ خطا در اجرای اسکریپت:\n{error_message}")
        return

    today = JalaliDate.today().strftime("%Y/%m/%d")
    message = f"✅ بروزرسانی انجام شد!\n📅 تاریخ: {today}\n📱 تعداد مدل‌ها: {len(brands)} عدد\n\n"
    lines = []
    for i, (brand, model, price, color) in enumerate(zip(brands, models, prices, colors), start=1):
        line = f"{i}. برند: {brand}\n   مدل: {model}\n   رنگ: {color}\n   قیمت: {int(price):,} تومان\n\n"
        lines.append(line)

    chunk = message
    for line in lines:
        if len(chunk) + len(line) > 4000:
            bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=chunk)
            chunk = line
        else:
            chunk += line
    if chunk:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=chunk)


def main():
    try:
        driver = get_driver()
        driver.get('https://hamrahtel.com/quick-checkout')
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CLASS_NAME, 'mantine-Text-root')))
        scroll_page(driver)

        valid_brands = ["Galaxy", "POCO", "Redmi", "iPhone", "Redtone", "VOCAL", "TCL", "NOKIA", "Honor", "Huawei", "GLX", "+Otel"]
        brands, models, prices, colors = extract_product_data(driver, valid_brands)

        if brands:
            send_telegram_message(brands, models, prices, colors)

        driver.quit()

    except Exception as e:
        send_telegram_message([], [], [], [], error_message=str(e))


if __name__ == "__main__":
    main()
