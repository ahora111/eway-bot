import requests
import urllib3

# غیرفعال کردن هشدار SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

WC_API_URL = "https://pakhshemobile.ir/wp-json/wc/v3/products"
WC_CONSUMER_KEY = "ck_b4666104bd0f31a9aeddde0f09f84081cb40b39a"
WC_CONSUMER_SECRET = "cs_0201b57511de7e4b146e67aac3d1c25465ebb26d"

params = {
    "consumer_key": WC_CONSUMER_KEY,
    "consumer_secret": WC_CONSUMER_SECRET
}

response = requests.get(WC_API_URL, params=params, verify=False)
print("Status code:", response.status_code)
print("Response text:", response.text[:500])  # فقط ۵۰۰ کاراکتر اول برای تست

try:
    data = response.json()
    print("تعداد محصولات:", len(data))
except Exception as e:
    print("خطا در دریافت json:", e)
