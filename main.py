import requests
from bs4 import BeautifulSoup
import urllib3
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

# مرحله ۱: گرفتن لینک همه محصولات از صفحه لیست
def fetch_product_links():
    url = "https://naminet.co/list/llp-13/%DA%AF%D9%88%D8%B4%DB%8C-%D8%B3%D8%A7%D9%85%D8%B3%D9%88%D9%86%DA%AF"
    headers = {
        "User-Agent": "Mozilla/5.0 ..."
    }
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")
    links = []
    for box in soup.find_all("div", id=lambda x: x and x.startswith("NAMI-")):
        a_tag = box.find("a", href=True)
        if a_tag:
            link = a_tag["href"]
            if not link.startswith("http"):
                link = "https://naminet.co" + link
            links.append(link)
    return links

# مرحله ۲: اسکرپ اطلاعات کامل از صفحه داخلی هر محصول
def fetch_product_details(product_url):
    headers = {
        "User-Agent": "Mozilla/5.0 ..."
    }
    response = requests.get(product_url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")

    # عنوان مدل
    name_tag = soup.find("h1")
    product_name = name_tag.text.strip() if name_tag else ""

    # تصاویر گالری
    images = []
    for img in soup.find_all("img", class_="mx-auto"):
        img_url = img.get("src", "")
        if img_url and img_url not in images:
            images.append(img_url)

    # قیمت
    price_tag = soup.find("span", class_="price actual-price")
    price_p = price_tag.find("p") if price_tag else None
    price = price_p.text.strip() if price_p else ""
    price = price.replace("تومان", "").replace("از", "").replace("٬", "").replace(",", "").strip()

    # رنگ
    color = "نامشخص"
    color_section = soup.find("div", string=lambda t: t and "رنگ" in t)
    if color_section:
        color = color_section.find_next("p").text.strip()

    # ویژگی‌ها
    attributes = []
    for attr_box in soup.find_all("div", class_="rounded-lg font-normal mb-2 min-w-30 flex flex-col items-start gap-2 p-2"):
        attr_name = attr_box.find_all("p")[0].text.strip()
        attr_value = attr_box.find_all("p")[1].text.strip()
        attributes.append({"name": attr_name, "option": attr_value})

    return {
        "product": product_name,
        "price": price,
        "images": images,
        "color": color,
        "attributes": attributes
    }

# مرحله ۳: ارسال یا آپدیت محصول در ووکامرس
def create_or_update_product(product):
    sku = f"{product['product']}-{product['color']}".replace(" ", "-")
    price = process_model(product['price'])
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
        "name": f"{product['product']} - {product['color']}",
        "regular_price": str(price).replace(",", ""),
        "sku": sku,
        "manage_stock": False,
        "stock_status": "instock",
        "description": f"رنگ: {product['color']}",
        "images": [{"src": img} for img in product['images']],
        "attributes": product['attributes'] + [{"name": "رنگ", "option": product['color']}]
    }
    if products and isinstance(products, list) and len(products) > 0:
        product_id = products[0]['id']
        url = f"{WC_API_URL}/{product_id}"
        r = requests.put(url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
        print(f"آپدیت شد: {product['product']} - {product['color']}")
    else:
        r = requests.post(WC_API_URL, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
        print(f"ایجاد شد: {product['product']} - {product['color']}")
        print("POST status:", r.status_code)
        print("POST text:", r.text[:500])

def main():
    product_links = fetch_product_links()
    print(f"تعداد محصولات پیدا شده: {len(product_links)}")
    for url in product_links:
        print("در حال اسکرپ:", url)
        product = fetch_product_details(url)
        if not product or not product['product']:
            print("❌ داده‌ای برای ارسال وجود ندارد!")
            continue
        create_or_update_product(product)
        time.sleep(2)  # برای جلوگیری از بلاک شدن توسط سایت مقصد

if __name__ == "__main__":
    main()
