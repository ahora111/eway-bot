import requests
import logging
from logging.handlers import RotatingFileHandler
import os
import time

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=1024*1024, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

BASE_URL = "https://panel.eways.co"
LIST_LAZY_URL = f"{BASE_URL}/Store/ListLazy"
CATEGORY_ID = 4286

EWAYS_USERNAME = os.environ.get("EWAYS_USERNAME") or "شماره موبایل یا یوزرنیم"
EWAYS_PASSWORD = os.environ.get("EWAYS_PASSWORD") or "پسورد"

def login_eways(username, password):
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f"{BASE_URL}/",
        'X-Requested-With': 'XMLHttpRequest',
        'Accept-Language': 'en-US,en;q=0.9,fa;q=0.8'
    })
    session.verify = False
    login_url = f"{BASE_URL}/User/Login"
    payload = {
        "UserName": username,
        "Password": password,
        "RememberMe": "true"
    }
    logger.info("⏳ در حال لاگین به پنل eways ...")
    resp = session.post(login_url, data=payload, timeout=30)
    if resp.status_code != 200:
        logger.error(f"❌ لاگین ناموفق! کد وضعیت: {resp.status_code} - متن پاسخ: {resp.text[:200]}")
        return None
    if 'Aut' in session.cookies:
        logger.info("✅ لاگین موفق! کوکی Aut دریافت شد.")
        return session
    else:
        logger.error("❌ کوکی Aut دریافت نشد. لاگین ناموفق یا دلیل نامشخص.")
        return None

def get_all_products(session):
    all_products = []
    lazy_page = 1
    while True:
        data = {
            "ListViewType": 0,
            "CatId": CATEGORY_ID,
            "Order": 2,
            "Sort": 2,
            "LazyPageIndex": lazy_page,
            "PageIndex": 0,
            "PageSize": 24,
            "Available": 0,
            "MinPrice": 0,
            "MaxPrice": 10000000000,
            "IsLazyLoading": "true"
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE_URL}/Store/List/{CATEGORY_ID}/2/2/0/0/0/10000000000"
        }
        logger.info(f"⏳ در حال دریافت LazyPageIndex={lazy_page} ...")
        resp = session.post(LIST_LAZY_URL, data=data, headers=headers, timeout=30)
        if resp.status_code != 200:
            logger.error(f"❌ خطا در دریافت محصولات (کد: {resp.status_code})")
            break
        try:
            result = resp.json()
        except Exception as e:
            logger.error(f"❌ خطا در تبدیل پاسخ به json: {e}")
            logger.error(f"متن پاسخ سرور:\n{resp.text[:500]}")
            break
        if not result or "Goods" not in result or not result["Goods"]:
            logger.info("🚩 به انتهای محصولات رسیدیم یا لیست خالی است.")
            break
        goods = result["Goods"]
        for g in goods:
            all_products.append({"id": g["Id"], "name": g["Name"]})
        logger.info(f"تعداد محصولات این صفحه: {len(goods)}")
        lazy_page += 1
        time.sleep(0.5)
    return all_products

if __name__ == "__main__":
    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session:
        logger.error("برنامه به دلیل خطای لاگین متوقف شد.")
        exit(1)
    products = get_all_products(session)
    logger.info(f"\n✅ تعداد کل محصولات این دسته: {len(products)}\n")
    for i, p in enumerate(products, 1):
        logger.info(f"{i:03d}. {p['name']} (ID: {p['id']})")
    print(f"\n✅ تعداد کل محصولات این دسته: {len(products)}")
