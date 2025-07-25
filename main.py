import requests
from bs4 import BeautifulSoup

url = "https://panel.eways.co/store/categorylist/4285/"
headers = {
    "User-Agent": "Mozilla/5.0"
}

response = requests.get(url, headers=headers)
soup = BeautifulSoup(response.text, "html.parser")

# پیدا کردن همه divهای محصول (کلاس را باید با inspect دقیق جایگزین کنی)
products = soup.find_all("div")  # فعلاً همه divها را می‌گیریم

with open("products_log.txt", "w", encoding="utf-8") as f:
    for i, product in enumerate(products):
        f.write(f"--- Product {i+1} ---\n")
        f.write(product.prettify())
        f.write("\n\n")

print("تمام اطلاعات محصولات در فایل products_log.txt ذخیره شد.")
