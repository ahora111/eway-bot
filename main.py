import requests
import urllib3

# غیرفعال کردن هشدار SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

WC_API_URL = "https://pakhshemobile.ir/wp-json/wc/v3/products"
WC_CONSUMER_KEY = "ck_251fa0545dc395c3d01788a5d9be814aab7575c8"
WC_CONSUMER_SECRET = "cs_b2b0dca5807d49e8e10ef2a9edcc00bd08c82af3"

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
