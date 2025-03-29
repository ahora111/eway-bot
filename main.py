import os
import time
import json
import asyncio
from persiantools.jdatetime import JalaliDate
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from telegram import Bot

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
        time.sleep(1.5)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height


def extract_product_data(driver, valid_brands):
    product_elements = driver.find_elements(By.CLASS_NAME, 'mantine-Text-root')
    products = []
    for product in product_elements:
        name = product.text.strip().replace("تومانءء", "").replace("تومان", "").replace("نامشخص", "").strip()
        parts = name.split()
        if len(parts) >= 3:
            brand = parts[0]
            model = " ".join(parts[1:-1])
            price = parts[-1].replace(",", "")
            if brand in valid_brands and price.isdigit():
                products.append({
                    "brand": brand,
                    "model": model,
                    "price": int(price)
                })
    return products


async def send_telegram_message(products, error_message=None):
    bot = Bot(token=TELEGRAM_TOKEN)

    if error_message:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"❗️ خطا در اجرای اسکریپت:\n{error_message}")
        return

    today = JalaliDate.today().strftime("%Y/%m/%d")
    message = f"✅ بروزرسانی انجام شد!\n📅 تاریخ: {today}\n📱 تعداد مدل‌ها: {len(products)} عدد\n\n"
    lines = []

    for i, product in enumerate(products, start=1):
        line = f"{i}. برند: {product['brand']}\n   مدل: {product['model']}\n   قیمت: {product['price']:,} تومان\n\n"
        lines.append(line)

    chunk = message
    for line in lines:
        if len(chunk) + len(line) > 4000:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=chunk)
            chunk = line
        else:
            chunk += line
    if chunk:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=chunk)


def main():
    try:
        driver = get_driver()
        driver.get('https://hamrahtel.com/quick-checkout')
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CLASS_NAME, 'mantine-Text-root')))
        scroll_page(driver)

        valid_brands = ["Galaxy", "POCO", "Redmi", "iPhone", "Redtone", "VOCAL", "TCL", "NOKIA", "Honor", "Huawei", "GLX", "+Otel"]
        products = extract_product_data(driver, valid_brands)

        if products:
            asyncio.run(send_telegram_message(products))
        else:
            asyncio.run(send_telegram_message([], error_message="هیچ محصولی پیدا نشد."))

        driver.quit()

    except Exception as e:
        asyncio.run(send_telegram_message([], error_message=str(e)))


if __name__ == "__main__":
    main()
