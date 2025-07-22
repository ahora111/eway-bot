import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def fetch_product_links():
    url = "https://naminet.co/list/llp-13/%DA%AF%D9%88%D8%B4%DB%8C-%D8%B3%D8%A7%D9%85%D8%B3%D9%88%D9%86%DA%AF"
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)
    driver.get(url)
    time.sleep(5)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    driver.quit()
    links = []
    divs = soup.find_all("div", id=lambda x: x and x.startswith("NAMI-"))
    print("تعداد div با id که با NAMI- شروع می‌شود:", len(divs))
    for idx, box in enumerate(divs):
        print(f"\n--- محصول شماره {idx+1} ---")
        for a_tag in box.find_all("a"):
            print("a:", a_tag)
            print("href:", a_tag.get("href"))
            href = a_tag.get("href")
            if href and "/product/" in href:
                if not href.startswith("http"):
                    href = "https://naminet.co" + href
                links.append(href)
    print("تعداد لینک محصولات پیدا شده:", len(links))
    return links

def main():
    product_links = fetch_product_links()
    print(f"تعداد محصولات پیدا شده: {len(product_links)}")
    for url in product_links:
        print("لینک محصول:", url)

if __name__ == "__main__":
    main()
