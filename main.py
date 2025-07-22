import requests
from bs4 import BeautifulSoup
import urllib3

# غیرفعال کردن هشدار SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# اطلاعات ووکامرس سایت شما
WC_API_URL = "https://pakhshemobile.ir/wp-json/wc/v3/products"
WC_CONSUMER_KEY = "ck_251fa0545dc395c3d01788a5d9be814aab7575c8"
WC_CONSUMER_SECRET = "cs_b2b0dca5807d49e8e10ef2a9edcc00bd08c82af3"

def is_number(model_str):
    try:
        float(model_str.replace(",", ""))
        return True
    except ValueError:
        return False

def process_model(model_str):
    model_str = model_str.replace("٬", "").replace(",", "").strip()
    if is_number(model_str):
        model_value = float(model_str)
        if model_value <= 1:
            model_value_with_increase = model_value * 0
        elif model_value <= 7000000:
            model_value_with_increase = model_value + 260000
        elif model_value <= 10000000:
            model_value_with_increase = model_value * 1.035
        elif model_value <= 20000000:
            model_value_with_increase = model_value * 1.025
        elif model_value <= 30000000:
            model_value_with_increase = model_value * 1.02
        elif model_value <= 40000000:
            model_value_with_increase = model_value * 1.015
        else:
            model_value_with_increase = model_value * 1.015
        model_value_with_increase = round(model_value_with_increase, -5)
        return f"{model_value_with_increase:,.0f}"
    return model_str

def fetch_samsung_products():
    url = "https://naminet.co/list/llp-13/%DA%AF%D9%88%D8%B4%DB%8C-%D8%B3%D8%A7%D9%85%D8%B3%D9%88%D9%86%DA%AF"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    }
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")
    products = []

    for box in soup.find_all("div", id=lambda x: x and x.startswith("NAMI-")):
        try:
            # عنوان مدل
            name_tag = box.find("h2")
            product_name = name_tag.text.strip() if name_tag else ""
            # تصویر
            img_tag = box.find("img")
            image_url = img_tag["src"] if img_tag else ""
            # قیمت
            price_tag = box.find("span", class_="price")
            if not price_tag:
                price_tag = box.find("span", class_="price actual-price")
            price_p = price_tag.find("p") if price_tag else None
            price = price_p.text.strip() if price_p else ""
            price = price.replace("تومان", "").replace("از", "").replace("٬", "").replace(",", "").strip()
            if not price or not is_number(price):
                continue
            # رنگ (در این ساختار رنگ جدا نیست، پس فقط یک محصول با رنگ پیش‌فرض)
            color = "نامشخص"
            products.append({
                "product": product_name,
                "color": color,
                "price": price,
                "image": image_url
            })
        except Exception as e:
            print("خطا:", e)
            continue
    return products

def create_or_update_product(product_name, color, price, sku, image_url):
    params = {
        "consumer_key": WC_CONSUMER_KEY,
        "consumer_secret": WC_CONSUMER_SECRET,
        "sku": sku
    }
    r = requests.get(WC_API_URL, params=params, verify=False)
    try:
        products = r.json()
    except Exception as e:
        print("خطا در دریافت json:", e)
        print("Status code:", r.status_code)
        print("Response text:", r.text[:500])
        products = []
    data = {
        "name": f"{product_name} - {color}",
        "regular_price": str(price).replace(",", ""),
        "sku": sku,
        "manage_stock": False,
        "stock_status": "instock",
        "description": f"رنگ: {color}",
        "images": [{"src": image_url}] if image_url else [],
        "attributes": [
            {"name": "رنگ", "option": color}
        ]
    }
    if products and isinstance(products, list) and len(products) > 0:
        product_id = products[0]['id']
        url = f"{WC_API_URL}/{product_id}"
        r = requests.put(url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
        print(f"آپدیت شد: {product_name} - {color}")
    else:
        r = requests.post(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
        print(f"ایجاد شد: {product_name} - {color}")
        print("POST status:", r.status_code)
        print("POST text:", r.text[:500])

def main():
    products = fetch_samsung_products()
    if not products:
        print("❌ داده‌ای برای ارسال وجود ندارد!")
        return
    for p in products:
        sku = f"{p['product']}-{p['color']}".replace(" ", "-")
        price = process_model(p['price'])
        create_or_update_product(p['product'], p['color'], price, sku, p['image'])

if __name__ == "__main__":
    main()
