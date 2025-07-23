from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import shutil

category_url = "https://naminet.co/list/llp-13/%DA%AF%D9%88%D8%B4%DB%8C-%D8%B3%D8%A7%D9%85%D8%B3%D9%88%D9%86%DA%AF"

options = Options()
# options.add_argument("--headless")  # فعلاً headless نباشد تا صفحه را ببینی
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--window-size=1920,1080")
if shutil.which("google-chrome"):
    options.binary_location = shutil.which("google-chrome")

driver = webdriver.Chrome(options=options)
driver.get(category_url)
wait = WebDriverWait(driver, 20)
wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[id^="NAMI-"]')))
time.sleep(3)

# اسکرول برای لود کامل محصولات (در صورت lazy load)
for _ in range(7):
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)

# ذخیره سورس صفحه برای بررسی دستی
with open("category_debug.html", "w", encoding="utf-8") as f:
    f.write(driver.page_source)

# استخراج لینک محصولات
product_links = []
all_products = driver.find_elements(By.CSS_SELECTOR, 'div[id^="NAMI-"]')
for prod in all_products:
    try:
        link = prod.find_element(By.TAG_NAME, "a").get_attribute("href")
        if link and link not in product_links:
            product_links.append(link)
    except Exception as e:
        print("لینک محصول پیدا نشد:", e)

print(f"تعداد لینک محصول پیدا شده: {len(product_links)}")
for link in product_links:
    print(link)

driver.quit()
