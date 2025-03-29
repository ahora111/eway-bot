import os
import time
import asyncio
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from persiantools.jdatetime import JalaliDate
from telegram import Bot


TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def get_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    service = Service('/usr/bin/chromedriver')
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
    product_data = {}

    for product in product_elements:
        name = product.text.strip().replace("تومانءء", "").replace("تومان", "").replace("نامشخص", "").strip()
        parts = name.split()
        if len(parts) >= 3:
            brand = parts[0]
            model = " ".join(parts[1:-1])
            price = parts[-1].replace(",", "")
            color = parts[-2] if len(parts) > 3 else "نامشخص"
            if brand in valid_brands:
                if model not in product_data:
                    product_data[model] = {'brand': brand, 'prices': [], 'colors': []}
                product_data[model]['prices'].append(price)
                product_data[model]['colors'].append(color)

    return product_data


async def send_telegram_message(product_data):
    bot = Bot(token=TELEGRAM_TOKEN)
    today = JalaliDate.today().strftime("%Y/%m/%d")
    message = f"✅ بروزرسانی انجام شد!\n📅 تاریخ: {today}\n📱 تعداد مدل‌ها: {len(product_data)} عدد\n\n"
    
    for i, (model, data) in enumerate(product_data.items(), start=1):
        message += f"{i}. برند: {data['brand']}\n   مدل: {model}\n"
        for price, color in zip(data['prices'], data['colors']):
            message += f"   قیمت: {price} تومان  {color}\n"
        message += "\n"

    if len(message) > 4000:
        for i in range(0, len(message), 4000):
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message[i:i+4000])
    else:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)


async def main():
    driver = get_driver()
    driver.get('https://hamrahtel.com/quick-checkout')
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CLASS_NAME, 'mantine-Text-root')))

    scroll_page(driver)

    valid_brands = ["Galaxy", "POCO", "Redmi", "iPhone", "Redtone", "VOCAL", "TCL", "NOKIA", "Honor", "Huawei", "GLX", "+Otel"]
    product_data = extract_product_data(driver, valid_brands)

    if product_data:
        await send_telegram_message(product_data)

    driver.quit()


if __name__ == "__main__":
    asyncio.run(main())
