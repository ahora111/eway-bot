import requests
import urllib3

# غیرفعال کردن هشدار SSL (برای سایت با گواهی منقضی)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# اطلاعات ووکامرس سایت شما
WC_API_URL = "https://pakhshemobile.ir/wp-json/wc/v3/products"
WC_CONSUMER_KEY = "ck_b4666104bd0f31a9aeddde0f09f84081cb40b39a"
WC_CONSUMER_SECRET = "cs_0201b57511de7e4b146e67aac3d1c25465ebb26d"

# گرفتن محصولات از API نامی‌نت
def fetch_products_json():
    url = "https://panel.naminet.co/api/catalog/productGroupsAttrNew?term="
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJuYmYiOiIxNzUyMjUyMTE2IiwiZXhwIjoiMTc2MDAzMTcxNiIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL2VtYWlsYWRkcmVzcyI6IjA5MzcxMTExNTU4QGhtdGVtYWlsLm5leHQiLCJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6ImE3OGRkZjViLTVhMjMtNDVkZC04MDBlLTczNTc3YjBkMzQzOSIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL25hbWUiOiIwOTM3MTExMTU1OCIsIkN1c3RvbWVySWQiOiIxMDA4NCJ9.kXoXA0atw0M64b6m084Gt4hH9MoC9IFFDFwuHOEdazA"
    }
    response = requests.get(url, headers=headers, verify=False)
    data = response.json()
    return data

# استخراج محصولات از JSON
def extract_products(data):
    products = []
    for parent in data.get("ParentCategories", []):
        for category in parent.get("Data", []):
            category_name = category.get("Name", "")
            for item in category.get("Data", []):
                product_name = item.get("ProductName", "")
                color = item.get("Name", "")
                price = item.get("final_price_value", 0)
                price = f"{int(price):,}"
                products.append({
                    "category": category_name,
                    "product": product_name,
                    "color": color,
                    "price": price
                })
    return products

# منطق افزایش قیمت
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

# ارسال یا آپدیت محصول در ووکامرس
def create_or_update_product(product_name, color, price, sku):
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
    data = fetch_products_json()
    products = extract_products(data)
    if not products:
        print("❌ داده‌ای برای ارسال وجود ندارد!")
        return
    for p in products:
        sku = f"{p['product']}-{p['color']}".replace(" ", "-")
        price = process_model(p['price'])
        create_or_update_product(p['product'], p['color'], price, sku)

if __name__ == "__main__":
    main()
