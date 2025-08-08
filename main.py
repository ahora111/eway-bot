import requests
import logging
from logging.handlers import RotatingFileHandler
from bs4 import BeautifulSoup
import time
import re

# --- تنظیمات ---
BASE_URL = "https://panel.eways.co"
CATEGORY_ID = 4286  # دسته مورد نظر
LIST_LAZY_URL = f"{BASE_URL}/Store/ListLazy"
LIST_HTML_URL_TEMPLATE = f"{BASE_URL}/Store/List/{CATEGORY_ID}/2/2/0/0/0/10000000000?page={{page}}"
EWAYS_USERNAME = "شماره موبایل یا یوزرنیم"  # مقدار صحیح را وارد کن
EWAYS_PASSWORD = "پسورد"  # مقدار صحیح را وارد کن
MAX_PAGE = 5  # تعداد صفحات مورد بررسی

# --- لاگینگ ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=1024*1024, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# --- لاگین ---
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

# --- محصولات اولیه فقط صفحه اول (۲۴ تای اول) ---
def get_initial_products(session):
    url = LIST_HTML_URL_TEMPLATE.format(page=1)
    logger.info(f"⏳ دریافت محصولات اولیه از HTML صفحه 1 ...")
    resp = session.get(url, timeout=30)
    if resp.status_code != 200:
        logger.error(f"❌ خطا در دریافت HTML صفحه 1")
        return []
    soup = BeautifulSoup(resp.text, 'lxml')
    product_blocks = soup.select(".goods-record")
    products = []
    for block in product_blocks:
        a_tag = block.select_one("a")
        name_tag = block.select_one("span.goods-record-title")
        unavailable = block.select_one(".goods-record-unavailable")
        is_available = unavailable is None
        if a_tag and name_tag:
            product_id = None
            href = a_tag['href']
            match = re.search(r'/Store/Detail/\d+/(\d+)', href)
            if match:
                product_id = match.group(1)
            name = name_tag.text.strip()
            products.append({'id': product_id, 'name': name, 'available': is_available})
    logger.info(f"تعداد محصولات اولیه (HTML) صفحه 1: {len(products)}")
    return products

# --- محصولات Lazy همه صفحات ---
def get_lazy_products(session, page):
    all_products = []
    lazy_page = 1
    referer_url = LIST_HTML_URL_TEMPLATE.format(page=page)
    while True:
        data = {
            "ListViewType": 0,
            "CatId": CATEGORY_ID,
            "Order": 2,
            "Sort": 2,
            "LazyPageIndex": lazy_page,
            "PageIndex": page - 1,  # PageIndex از 0 شروع می‌شود
            "PageSize": 24,
            "Available": 0,
            "MinPrice": 0,
            "MaxPrice": 10000000000,
            "IsLazyLoading": "true"
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer_url
        }
        logger.info(f"⏳ در حال دریافت LazyPageIndex={lazy_page} صفحه {page} ...")
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
            logger.info(f"🚩 به انتهای محصولات Lazy صفحه {page} رسیدیم یا لیست خالی است.")
            break
        goods = result["Goods"]
        for g in goods:
            all_products.append({
                "id": g["Id"],
                "name": g["Name"],
                "available": g.get("Availability", True)
            })
        logger.info(f"تعداد محصولات این صفحه Lazy: {len(goods)}")
        lazy_page += 1
        time.sleep(0.5)
    return all_products

# --- اجرای اصلی ---
if __name__ == "__main__":
    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session:
        logger.error("برنامه به دلیل خطای لاگین متوقف شد.")
        exit(1)

    all_products = {}

    # فقط HTML صفحه اول
    initial_products = get_initial_products(session)
    for p in initial_products:
        all_products[p['id']] = p

    # Lazy همه صفحات
    for page in range(1, MAX_PAGE + 1):
        lazy_products = get_lazy_products(session, page)
        for p in lazy_products:
            all_products[p['id']] = p

    all_products = list(all_products.values())
    available = [p for p in all_products if p['available']]
    unavailable = [p for p in all_products if not p['available']]

    logger.info(f"\n✅ تعداد کل محصولات این دسته (در {MAX_PAGE} صفحه): {len(all_products)}")
    logger.info(f"🟢 محصولات موجود: {len(available)}")
    logger.info(f"🔴 محصولات ناموجود: {len(unavailable)}\n")

    print(f"\n🟢 محصولات موجود ({len(available)}):")
    for i, p in enumerate(available, 1):
        print(f"{i:03d}. {p['name']} (ID: {p['id']})")

    print(f"\n🔴 محصولات ناموجود ({len(unavailable)}):")
    for i, p in enumerate(unavailable, 1):
        print(f"{i:03d}. {p['name']} (ID: {p['id']})")
