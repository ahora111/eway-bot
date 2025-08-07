import requests
from bs4 import BeautifulSoup
import re
import time

BASE_URL = "https://panel.eways.co"
CATEGORY_ID = 4286  # دسته گوشی موبایل
PRODUCT_LIST_URL = f"{BASE_URL}/Store/List/{CATEGORY_ID}/2/2/0/0/0/10000000000"

def login_eways(username, password):
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': BASE_URL + '/user/login',
        'X-Requested-With': 'XMLHttpRequest',
        'Accept-Language': 'fa',
        'Origin': BASE_URL
    })
    session.verify = False

    # مرحله 1: دریافت صفحه لاگین و استخراج توکن
    login_page = session.get(BASE_URL + "/user/login")
    soup = BeautifulSoup(login_page.text, 'lxml')
    token_input = soup.find('input', {'name': '__RequestVerificationToken'})
    token_value = token_input['value'] if token_input else ''
    if not token_value:
        print("❌ نتوانستم توکن آنتی‌فورجری را پیدا کنم!")
        return None

    time.sleep(0.5)  # تاخیر کوتاه

    # مرحله 2: ارسال لاگین با توکن
    login_url = BASE_URL + "/User/Login"
    payload = {
        "UserName": username,
        "Password": password,
        "RememberMe": "true",
        "__RequestVerificationToken": token_value
    }
    resp = session.post(login_url, data=payload, timeout=30)
    if resp.status_code == 200 and 'Aut' in session.cookies:
        print("✅ Login OK")
        return session
    if "کپچا" in resp.text or "captcha" in resp.text.lower():
        print("❌ کپچا فعال شده! لاگین با ربات ممکن نیست.")
    else:
        print("❌ Login failed")
    return None

def get_all_products(session):
    all_products = []
    page = 1
    while True:
        url = f"{PRODUCT_LIST_URL}?page={page}"
        print(f"⏳ Fetching page {page} ...")
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            print("❌ Error fetching page")
            break
        soup = BeautifulSoup(resp.text, 'lxml')
        product_blocks = soup.select(".goods-record")
        if not product_blocks:
            print("🚩 No more products found.")
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

if __name__ == "__main__":
    # اطلاعات لاگین خودت رو اینجا بذار
    username = "شماره موبایل یا یوزرنیم"
    password = "پسورد"
    session = login_eways(username, password)
    if not session:
        exit(1)
    products = get_all_products(session)
    print(f"\n✅ تعداد کل محصولات این دسته: {len(products)}\n")
    for i, p in enumerate(products, 1):
        print(f"{i:03d}. {p['name']} (ID: {p['id']})")
