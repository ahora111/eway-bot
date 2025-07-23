import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
import time

category_url = "https://naminet.co/list/llp-13/%DA%AF%D9%88%D8%B4%DB%8C-%D8%B3%D8%A7%D9%85%D8%B3%D9%88%D9%86%DA%AF"

options = uc.ChromeOptions()
options.add_argument("--headless=new")  # حتماً فعال باشد
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--window-size=1920,1080")

driver = uc.Chrome(options=options)
driver.get(category_url)
time.sleep(10)

for _ in range(7):
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)

product_links = []
all_products = driver.find_elements(By.CSS_SELECTOR, 'div[id^="NAMI-"]')
for prod in all_products:
    try:
        a_tag = prod.find_element(By.TAG_NAME, "a")
        href = a_tag.get_attribute("href")
        if href and href not in product_links:
            product_links.append(href)
    except Exception as e:
        print("لینک محصول پیدا نشد:", e)

print(f"تعداد لینک محصول پیدا شده: {len(product_links)}")
for link in product_links:
    print(link)

driver.quit()
