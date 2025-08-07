import requests
import re
import time
import os
import logging
from logging.handlers import RotatingFileHandler
from bs4 import BeautifulSoup

# ==============================================================================
# --- تنظیمات لاگینگ ---
# ==============================================================================
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=1024*1024, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# ==============================================================================
# --- اطلاعات سایت مبدا ---
# ==============================================================================
BASE_URL = "https://panel.eways.co"
CATEGORY_ID = 4286  # دسته گوشی موبایل
PRODUCT_LIST_URL = f"{BASE_URL}/Store/List/{CATEGORY_ID}/2/2/0/0/0/10000000000"

EWAYS_USERNAME = os.environ.get("EWAYS_USERNAME") or "شماره موبایل یا یوزرنیم"
EWAYS_PASSWORD = os.environ.get("EWAYS_PASSWORD") or "پسورد"

# ==============================================================================
# --- تابع لاگین اتوماتیک به eways ---
# ==============================================================================
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
        if "کپچا" in resp.text or "captcha" in resp.text.lower():
            logger.error("❌ کوکی Aut دریافت نشد. کپچا فعال است.")
        elif "نام کاربری" in resp.text or "رمز عبور" in resp.text:
            logger.error("❌ کوکی Aut دریافت نشد. نام کاربری یا رمز عبور اشتباه است.")
        else:
            logger.error("❌ کوکی Aut دریافت نشد. لاگین ناموفق یا دلیل نامشخص.")
        return None

# ==============================================================================
# --- گرفتن محصولات یک دسته ---
# ==============================================================================
def get_all_products(session):
    all_products = []
    page = 1
    while True:
        url = f"{PRODUCT_LIST_URL}?page={page}"
        logger.info(f"⏳ در حال دریافت صفحه {page} ...")
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            logger.error("❌ خطا در دریافت صفحه محصولات")
            break
        soup = BeautifulSoup(resp.text, 'lxml')
        product_blocks = soup.select(".goods-record")
        if not product_blocks:
            logger.info("🚩 محصولی یافت نشد یا به انتهای صفحات رسیدیم.")
            break
        for block in product_blocks:
            a_tag = block.select_one("a")
            name_tag = block.select_one("span.goods-record-title")
            if a_tag and name_tag:
                product_id = None
                href = a_tag['href']
                match = re.search(r'/Store/Detail/\d+/(\d+)', href)
                if match:
                    product_id = match.group(1)
                name = name_tag.text.strip()
                all_products.append({'id': product_id, 'name': name})
        # اگر تعداد محصولات این صفحه کمتر از 96 شد، یعنی آخرین صفحه است
        if len(product_blocks) < 96:
            break
        page += 1
        time.sleep(1)
    return all_products

# ==============================================================================
# --- اجرای اصلی ---
# ==============================================================================
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
