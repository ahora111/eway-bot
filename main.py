import requests
import os
import re
import time
import json
import random
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from threading import Lock, Thread
from queue import Queue
import logging
from logging.handlers import RotatingFileHandler
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type
from collections import defaultdict
from urllib.parse import urljoin

# ==============================================================================
# --- توابع انتخاب منعطف با SELECTED_IDS_STRING ---
# ==============================================================================
def parse_selected_ids_string(selected_ids_string):
    result = []
    for part in selected_ids_string.split('|'):
        part = part.strip()
        if not part or ':' not in part:
            continue
        parent_id_str, children_str = part.split(':', 1)
        parent_id = int(parent_id_str.strip())
        selections = []
        for sel in children_str.split(','):
            sel = sel.strip()
            if not sel:
                continue
            if sel == 'all':
                selections.append({"id": parent_id, "type": "all_subcats"})
            elif sel == 'allz':
                selections.append({"id": parent_id, "type": "only_products"})
            elif sel == 'all-allz':
                selections.append({"id": parent_id, "type": "all_subcats_and_products"})
            elif re.match(r'^\d+-allz$', sel):
                sub_id = int(sel.split('-')[0])
                selections.append({"id": sub_id, "type": "only_products"})
            elif re.match(r'^\d+-all-allz$', sel):
                sub_id = int(sel.split('-')[0])
                selections.append({"id": sub_id, "type": "all_subcats_and_products"})
        result.append({"parent_id": parent_id, "selections": selections})
    return result

def get_direct_subcategories(parent_id, all_cats):
    return [cat['id'] for cat in all_cats if cat['parent_id'] == parent_id]

def get_all_subcategories(parent_id, all_cats):
    result = []
    direct = get_direct_subcategories(parent_id, all_cats)
    result.extend(direct)
    for sub_id in direct:
        result.extend(get_all_subcategories(sub_id, all_cats))
    return result

# خروجی: (لیست دسته‌های اسکرپ، لیست دسته‌های انتقال به ووکامرس با والدها)
def get_selected_categories_according_to_selection(parsed_selection, all_cats):
    selected_scrape = set()    # فقط دسته‌هایی که باید محصولات‌شان جمع شود
    selected_transfer = set()  # دسته‌هایی که باید در ووکامرس ساخته شوند (اسکرپ‌ها + تمام والدها)

    id_to_cat = {c['id']: c for c in all_cats}

    def add_ancestors(cid):
        while cid:
            if cid in selected_transfer:
                break
            selected_transfer.add(cid)
            cid = id_to_cat.get(cid, {}).get('parent_id')

    def add_all_subs(xid):
        # افزودن همه‌ی زیرشاخه‌ها (بازگشتی) به selected_scrape و والدها به selected_transfer
        for c in all_cats:
            if c['parent_id'] == xid:
                selected_scrape.add(c['id'])
                add_ancestors(c['id'])
                add_all_subs(c['id'])

    for block in parsed_selection:
        parent_id = block['parent_id']
        # والد را به‌صورت پیش‌فرض به لیست اسکرپ اضافه نمی‌کنیم
        for sel in block['selections']:
            typ, sid = sel['type'], sel['id']

            if typ == 'all_subcats' and sid == parent_id:
                # فقط زیرشاخه‌های مستقیم
                subs = get_direct_subcategories(parent_id, all_cats)
                for sc_id in subs:
                    selected_scrape.add(sc_id)
                    add_ancestors(sc_id)

            elif typ == 'only_products' and sid == parent_id:
                # خود والد
                selected_scrape.add(parent_id)
                add_ancestors(parent_id)

            elif typ == 'all_subcats_and_products' and sid == parent_id:
                # والد + همه زیرشاخه‌هایش
                selected_scrape.add(parent_id)
                add_ancestors(parent_id)
                # تمام زیرشاخه‌های عمیق
                for sub in get_all_subcategories(parent_id, all_cats):
                    selected_scrape.add(sub)
                    add_ancestors(sub)

            elif typ == 'only_products' and sid != parent_id:
                # فقط خود زیرشاخه انتخاب‌شده
                selected_scrape.add(sid)
                add_ancestors(sid)

            elif typ == 'all_subcats_and_products' and sid != parent_id:
                # زیرشاخه انتخاب‌شده + همه زیرشاخه‌هایش
                selected_scrape.add(sid)
                add_ancestors(sid)
                for sub in get_all_subcategories(sid, all_cats):
                    selected_scrape.add(sub)
                    add_ancestors(sub)

    scrape_categories = [cat for cat in all_cats if cat['id'] in selected_scrape]
    transfer_categories = [cat for cat in all_cats if cat['id'] in selected_transfer]
    return scrape_categories, transfer_categories

# ==============================================================================
# --- ابزارهای دسته‌ها: والد/فرزند، عمق، برگ‌ها ---
# ==============================================================================
def build_category_index(categories):
    parent_of = {}
    children_of = defaultdict(list)
    ids = set()

    for c in categories:
        cid = c['id']
        pid = c.get('parent_id')
        ids.add(cid)
        parent_of[cid] = pid
        if pid:
            children_of[pid].append(cid)

    # عمق با memo
    depth_memo = {}
    def depth(cid):
        if cid in depth_memo:
            return depth_memo[cid]
        p = parent_of.get(cid)
        if not p:
            depth_memo[cid] = 0
        else:
            depth_memo[cid] = 1 + depth(p)
        return depth_memo[cid]

    for cid in ids:
        depth(cid)

    leaf_ids = {cid for cid in ids if len(children_of.get(cid, [])) == 0}
    return parent_of, children_of, depth_memo, leaf_ids

def condense_products_to_leaf(all_products_by_catkey, categories):
    # all_products_by_catkey: dict with key "pid|catid" and value product dict
    parent_of, children_of, depth_of, leaf_ids = build_category_index(categories)

    # گروهبندی برحسب pid
    occurrences = defaultdict(list)
    for key, p in all_products_by_catkey.items():
        pid = str(p['id'])
        occurrences[pid].append(p)

    canonical = {}
    for pid, plist in occurrences.items():
        # کاندیدهای برگ
        leaf_candidates = [p for p in plist if p.get('category_id') in leaf_ids]
        candidates = leaf_candidates if leaf_candidates else plist
        # انتخاب بر مبنای بیشترین عمق، سپس کمترین id دسته برای ثبات
        candidates.sort(key=lambda p: (depth_of.get(p.get('category_id'), 0), -int(p.get('category_id', 0))), reverse=True)
        chosen = candidates[0]
        canonical[pid] = chosen
    return canonical

def normalize_cache(cached_products, categories):
    # پشتیبانی از کش قدیمی (کلید id|cat) و تبدیل به کش جدید (کلید pid)
    if not cached_products:
        return {}
    # اگر کلیدها شامل '|' هستند یعنی فرمت قدیم
    if any('|' in k for k in cached_products.keys()):
        # تبدیل به ساختار مورد نیاز condense
        all_products_by_catkey = {}
        for key, p in cached_products.items():
            # اطمینان از وجود category_id داخل رکورد
            if 'category_id' not in p:
                try:
                    _, catid = key.split('|')
                    p['category_id'] = int(catid)
                except:
                    pass
            all_products_by_catkey[key] = p
        return condense_products_to_leaf(all_products_by_catkey, categories)
    else:
        # فرمت جدید: کلید pid است
        normalized = {}
        for pid, p in cached_products.items():
            if 'category_id' in p and isinstance(p['category_id'], str) and p['category_id'].isdigit():
                p['category_id'] = int(p['category_id'])
            normalized[str(pid)] = p
        return normalized

# ==============================================================================
# --- تنظیمات لاگینگ ---
# ==============================================================================
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=1024*1024, backupCount=5, encoding='utf-8')  # UTF-8
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# ==============================================================================
# --- اطلاعات ووکامرس و سایت مبدا ---
# ==============================================================================
BASE_URL = "https://panel.eways.co"
SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"
PRODUCT_LIST_URL_TEMPLATE = f"{BASE_URL}/Store/List/{{category_id}}/2/2/0/0/0/10000000000?page={{page}}"
PRODUCT_DETAIL_URL_TEMPLATE = f"{BASE_URL}/Store/Detail/{{cat_id}}/{{product_id}}"

WC_API_URL = os.environ.get("WC_API_URL") or "https://your-woocommerce-site.com/wp-json/wc/v3"
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY") or "ck_xxx"
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET") or "cs_xxx"

EWAYS_USERNAME = os.environ.get("EWAYS_USERNAME") or "شماره موبایل یا یوزرنیم"
EWAYS_PASSWORD = os.environ.get("EWAYS_PASSWORD") or "پسورد"

CACHE_FILE = 'products_cache.json'  # فایل کش

# ==============================================================================
# --- کمک‌تابع URL مطلق برای تصاویر ---
# ==============================================================================
def abs_url(u):
    if not u:
        return u
    return u if u.startswith('http') else urljoin(BASE_URL, u)

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
# --- توابع مربوط به سایت مبدا (eways) ---
# ==============================================================================
@retry(
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    stop=stop_after_attempt(5),
    wait=wait_random_exponential(multiplier=1, max=5),
    reraise=True
)
def get_product_details(session, cat_id, product_id):
    url = PRODUCT_DETAIL_URL_TEMPLATE.format(cat_id=cat_id, product_id=product_id)
    try:
        response = session.get(url, timeout=60)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'lxml')
        specs_table = soup.select_one('#link1 .table-responsive table')
        if not specs_table:
            specs_table = soup.select_one('.table-responsive table')
            if not specs_table:
                specs_table = soup.find('table', class_='table')
                if not specs_table:
                    logger.debug(f"      - هیچ جدولی پیدا نشد. HTML خام صفحه: {soup.prettify()[:1000]}...")
                    return {}
        specs = {}
        rows = specs_table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) == 2:
                key = cells[0].text.strip()
                value = cells[1].text.strip()
                if key and value:
                    specs[key] = value
        if not specs:
            logger.debug(f"      - هیچ ردیفی در جدول پیدا نشد. HTML خام جدول: {specs_table.prettify()}")
        logger.debug(f"      - مشخصات استخراج‌شده برای {product_id} (کامل): {json.dumps(specs, ensure_ascii=False, indent=4)}")
        return specs
    except requests.exceptions.RequestException as e:
        logger.warning(f"      - خطا در دریافت جزئیات محصول {product_id}: {e}. Retry...")
        raise
    except Exception as e:
        logger.warning(f"      - خطا در استخراج مشخصات محصول {product_id}: {e}")
        return {}

def get_and_parse_categories(session):
    logger.info(f"⏳ دریافت دسته‌بندی‌ها از: {SOURCE_CATS_API_URL}")
    try:
        response = session.get(SOURCE_CATS_API_URL, timeout=30)
        response.raise_for_status()
        # تلاش برای JSON با نگاشت صحیح parent_id به real_id
        try:
            data = response.json()
            logger.info("✅ پاسخ JSON است. در حال پردازش با نگاشت والد-فرزند...")
            # ساخت نگاشت id اصلی → real_id استخراج‌شده از URL
            id_map = {}
            for c in data:
                real_id_match = re.search(r'/Store/List/(\d+)', c.get('url', '') or '')
                real_id = int(real_id_match.group(1)) if real_id_match else c.get('id')
                if c.get('id') is not None:
                    id_map[c['id']] = real_id

            final_cats = []
            for c in data:
                real_id = id_map.get(c.get('id'))
                if real_id is None:
                    continue
                parent_src = c.get('parent_id')
                parent_real = id_map.get(parent_src) if parent_src is not None else None
                final_cats.append({
                    "id": real_id,
                    "name": (c.get('name') or '').strip(),
                    "parent_id": parent_real
                })
            logger.info(f"✅ تعداد {len(final_cats)} دسته‌بندی از JSON با والد صحیح استخراج شد.")
            return final_cats
        except json.JSONDecodeError:
            logger.warning("⚠️ پاسخ JSON نیست. تلاش برای پارس HTML...")

        # پارس HTML به‌عنوان جایگزین
        soup = BeautifulSoup(response.text, 'lxml')
        all_menu_items = soup.select("li[id^='menu-item-']")
        if not all_menu_items:
            logger.error("❌ هیچ آیتم دسته‌بندی در HTML پیدا نشد.")
            return []
        logger.info(f"🔎 تعداد {len(all_menu_items)} آیتم منو پیدا شد. در حال پردازش...")

        cats_map = {}
        for item in all_menu_items:
            cat_id_raw = item.get('id', '')
            match = re.search(r'(\d+)', cat_id_raw)
            if not match:
                continue
            cat_menu_id = int(match.group(1))
            a_tag = item.find('a', recursive=False) or item.select_one("a")
            if not a_tag or not a_tag.get('href'):
                continue
            name = a_tag.text.strip()
            real_id_match = re.search(r'/Store/List/(\d+)', a_tag['href'])
            real_id = int(real_id_match.group(1)) if real_id_match else None
            if name and real_id and name != "#":
                cats_map[cat_menu_id] = {"id": real_id, "name": name, "parent_id": None}
        for item in all_menu_items:
            cat_id_raw = item.get('id', '')
            match = re.search(r'(\d+)', cat_id_raw)
            if not match:
                continue
            cat_menu_id = int(match.group(1))
            parent_li = item.find_parent("li", class_="menu-item-has-children")
            if parent_li:
                parent_id_raw = parent_li.get('id', '')
                parent_match = re.search(r'(\d+)', parent_id_raw)
                if parent_match:
                    parent_menu_id = int(parent_match.group(1))
                    if cat_menu_id in cats_map and parent_menu_id in cats_map:
                        cats_map[cat_menu_id]['parent_id'] = cats_map[parent_menu_id]['id']
        final_cats = list(cats_map.values())
        logger.info(f"✅ تعداد {len(final_cats)} دسته‌بندی معتبر استخراج شد.")
        return final_cats
    except requests.RequestException as e:
        logger.error(f"❌ خطا در دریافت دسته‌بندی‌ها: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ خطای ناشناخته در پردازش دسته‌بندی‌ها: {e}")
        return None

# ==============================================================================
# --- گرفتن محصولات هر دسته (فقط موجودها و توقف هوشمند) ---
# ==============================================================================
@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)),
    stop=stop_after_attempt(4),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True
)
def get_products_from_category_page(session, category_id, max_pages=10, delay=0.5):
    all_products_in_category = []
    seen_product_ids = set()
    page = 1
    error_count = 0
    while page <= max_pages:
        # --- محصولات اولیه HTML (پارامتر text حذف شد) ---
        if page == 1:
            url = f"{BASE_URL}/Store/List/{category_id}/2/2/0/0/0/10000000000"
        else:
            url = f"{BASE_URL}/Store/List/{category_id}/2/2/{page-1}/0/0/10000000000?brands=&isMobile=false"
        logger.info(f"⏳ دریافت محصولات اولیه از HTML صفحه {page} ...")
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code != 200:
                logger.error(f"❌ خطا در دریافت HTML صفحه {page} - status: {resp.status_code} - url: {url}")
                break
            soup = BeautifulSoup(resp.text, 'lxml')
            product_blocks = soup.select(".goods-record")
            html_products = []
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
                    price_tag = block.select_one("span.goods-record-price")
                    price_text = price_tag.text.strip() if price_tag else ""
                    price = re.sub(r'[^\d]', '', price_text) if price_text else "0"
                    image_tag = block.select_one("img.goods-record-image")
                    image_url = ""
                    if image_tag:
                        image_url = image_tag.get('data-src', '') or image_tag.get('src', '')
                        image_url = abs_url(image_url)
                    if is_available and product_id and product_id not in seen_product_ids:
                        specs = get_product_details(session, category_id, product_id)
                        html_products.append({
                            'id': product_id,
                            'name': name,
                            'category_id': category_id,
                            'price': price,
                            'stock': 1,
                            'image': image_url,
                            'specs': specs,
                        })
                        seen_product_ids.add(product_id)
                        time.sleep(random.uniform(delay, delay + 0.2))
            logger.info(f"🟢 محصولات موجود اولیه (HTML) صفحه {page}: {len(html_products)}")

            # --- محصولات Lazy (فقط موجودها: Available=1) ---
            lazy_products = []
            lazy_page = 1
            referer_url = url
            while True:
                data = {
                    "ListViewType": 0,
                    "CatId": category_id,
                    "Order": 2,
                    "Sort": 2,
                    "LazyPageIndex": lazy_page,
                    "PageIndex": page - 1,
                    "PageSize": 24,
                    "Available": 1,  # فقط موجودها
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
                resp = session.post(f"{BASE_URL}/Store/ListLazy", data=data, headers=headers, timeout=30)
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
                    if g.get("Availability", True):
                        product_id = str(g["Id"])
                        if product_id not in seen_product_ids:
                            specs = get_product_details(session, category_id, product_id)
                            lazy_products.append({
                                "id": product_id,
                                "name": g["Name"],
                                "category_id": category_id,
                                "price": g.get("Price", "0"),
                                "stock": 1,
                                "image": abs_url(g.get("ImageUrl", "")),
                                "specs": specs,
                            })
                            seen_product_ids.add(product_id)
                            time.sleep(random.uniform(delay, delay + 0.2))
                logger.info(f"🟢 محصولات موجود این صفحه Lazy: {len([g for g in goods if g.get('Availability', True)])}")
                lazy_page += 1

            # جمع محصولات موجود این صفحه
            available_in_page = html_products + lazy_products
            if not available_in_page:
                logger.info(f"⛔️ هیچ محصول موجودی در صفحه {page} نبود. بررسی صفحات متوقف شد.")
                break
            all_products_in_category.extend(available_in_page)
            page += 1
            error_count = 0
        except Exception as e:
            error_count += 1
            logger.error(f"    - خطا در پردازش صفحه محصولات: {e} (تعداد خطا: {error_count})")
            if error_count >= 3:
                logger.critical(f"🚨 تعداد خطاهای متوالی در دسته {category_id} به {error_count} رسید! توقف پردازش این دسته.")
                break
            time.sleep(2)
    logger.info(f"    - تعداد کل محصولات موجود استخراج‌شده از دسته {category_id}: {len(all_products_in_category)}")
    return all_products_in_category

# ==============================================================================
# --- کش برای محصولات (کلید جدید: فقط id) ---
# ==============================================================================
def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
            logger.info(f"✅ کش بارگذاری شد. تعداد محصولات در کش: {len(cache)}")
            return cache
    logger.info("⚠️ کش پیدا نشد. استخراج کامل انجام می‌شود.")
    return {}

def save_cache(products):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(products, f, ensure_ascii=False, indent=4)
    logger.info(f"✅ کش ذخیره شد. تعداد محصولات: {len(products)}")

# ==============================================================================
# --- توابع ووکامرس ---
# ==============================================================================
def get_wc_categories():
    wc_cats, page = [], 1
    while True:
        try:
            res = requests.get(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), params={"per_page": 100, "page": page}, verify=False)
            res.raise_for_status()
            data = res.json()
            if not data:
                break
            wc_cats.extend(data)
            if len(data) < 100:
                break
            page += 1
        except Exception as e:
            logger.error(f"❌ خطا در دریافت دسته‌بندی‌های ووکامرس: {e}")
            break
    logger.info(f"✅ تعداد دسته‌بندی‌های ووکامرس بارگذاری‌شده: {len(wc_cats)}")
    return wc_cats

def get_all_wc_products_with_prefix(prefix="EWAYS-"):
    products = []
    page = 1
    while True:
        try:
            res = requests.get(f"{WC_API_URL}/products", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), params={
                "per_page": 100, "page": page
            }, verify=False)
            res.raise_for_status()
            data = res.json()
            if not data:
                break
            filtered = [p for p in data if p.get('sku', '').startswith(prefix)]
            products.extend(filtered)
            logger.debug(f"      - صفحه {page}: {len(filtered)} محصول با پیشوند {prefix} پیدا شد.")
            if len(data) < 100:
                break
            page += 1
        except Exception as e:
            logger.error(f"❌ خطا در دریافت محصولات ووکامرس (صفحه {page}): {e}")
            break
    logger.info(f"✅ تعداد محصولات ووکامرس با پیشوند '{prefix}' بارگذاری‌شده: {len(products)}")
    return products

def check_existing_category(name, parent):
    try:
        res = requests.get(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), params={
            "search": name, "per_page": 1, "parent": parent
        }, verify=False)
        res.raise_for_status()
        data = res.json()
        for cat in data:
            if cat["name"].strip() == name and cat["parent"] == parent:
                return cat["id"]
        return None
    except Exception as e:
        logger.debug(f"⚠️ خطا در چک وجود دسته '{name}' (parent: {parent}): {e}")
        return None

def transfer_categories_to_wc(source_categories):
    logger.info("\n⏳ شروع انتقال دسته‌بندی‌ها به ووکامرس...")
    wc_cats = get_wc_categories()
    wc_cats_map = {}
    for cat in wc_cats:
        key = (cat["name"].strip(), cat.get("parent", 0))
        wc_cats_map[key] = cat["id"]

    # ترتیب والد قبل از فرزند
    sorted_cats = []
    id_to_cat = {cat['id']: cat for cat in source_categories}
    def add_with_parents(cat):
        if cat['parent_id'] and cat['parent_id'] in id_to_cat:
            parent_cat = id_to_cat[cat['parent_id']]
            if parent_cat not in sorted_cats:
                add_with_parents(parent_cat)
        if cat not in sorted_cats:
            sorted_cats.append(cat)
    for cat in source_categories:
        add_with_parents(cat)

    source_to_wc_id_map = {}
    transferred = 0
    for cat in tqdm(sorted_cats, desc="انتقال دسته‌ها"):
        name = cat["name"].strip()
        parent_id = cat.get("parent_id") or 0
        wc_parent = source_to_wc_id_map.get(parent_id, 0)
        lookup_key = (name, wc_parent)
        existing_id = check_existing_category(name, wc_parent)
        if existing_id:
            source_to_wc_id_map[cat["id"]] = existing_id
            logger.debug(f"✅ دسته '{name}' (parent: {wc_parent}) قبلاً وجود دارد (ID: {existing_id}). استفاده از موجود.")
            transferred += 1
            continue
        data = {"name": name, "parent": wc_parent}
        try:
            res = requests.post(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
            if res.status_code in [200, 201]:
                new_id = res.json()["id"]
                source_to_wc_id_map[cat["id"]] = new_id
                wc_cats_map[lookup_key] = new_id
                logger.debug(f"✅ دسته '{name}' (parent: {wc_parent}) ساخته شد (ID: {new_id}).")
                transferred += 1
            else:
                error_data = res.json()
                if error_data.get("code") == "term_exists" and "data" in error_data and "resource_id" in error_data["data"]:
                    existing_id = error_data["data"]["resource_id"]
                    source_to_wc_id_map[cat["id"]] = existing_id
                    wc_cats_map[lookup_key] = existing_id
                    logger.info(f"✅ دسته '{name}' (parent: {wc_parent}) وجود داشت (ID: {existing_id}). استفاده از resource_id موجود.")
                    transferred += 1
                else:
                    logger.error(f"❌ خطا در ساخت دسته‌بندی '{name}' (parent: {wc_parent}): {res.text}")
        except Exception as e:
            logger.error(f"❌ خطای شبکه در ساخت دسته‌بندی '{name}': {e}")
    logger.info(f"✅ انتقال دسته‌بندی‌ها کامل شد. تعداد منتقل‌شده: {transferred}/{len(source_categories)}")
    return source_to_wc_id_map

def process_price(price_value):
    try:
        price_value = float(re.sub(r'[^\d.]', '', str(price_value)))
        price_value /= 10
    except (ValueError, TypeError):
        return "0"
    if price_value <= 1:
        return "0"
    elif price_value <= 7000000:
        new_price = price_value + 260000
    elif price_value <= 10000000:
        new_price = price_value * 1.035
    elif price_value <= 20000000:
        new_price = price_value * 1.025
    elif price_value <= 30000000:
        new_price = price_value * 1.02
    else:
        new_price = price_value * 1.015
    return str(int(round(new_price, -4)))

@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True
)
def _send_to_woocommerce(sku, data, stats):
    try:
        auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
        logger.debug(f"   - چک SKU {sku}...")
        check_url = f"{WC_API_URL}/products?sku={sku}"
        r_check = requests.get(check_url, auth=auth, verify=False, timeout=20)
        r_check.raise_for_status()
        existing = r_check.json()
        if existing:
            product_id = existing[0]['id']
            update_data = {
                "regular_price": data["regular_price"],
                "stock_quantity": data["stock_quantity"],
                "stock_status": data["stock_status"],
                "attributes": data["attributes"],
                "tags": data.get("tags", []),
                "categories": data.get("categories", []),  # همگام‌سازی دسته‌ها (فقط leaf)
            }
            logger.debug(f"   - آپدیت محصول {product_id} با {len(update_data['attributes'])} مشخصه فنی و دسته leaf...")
            res = requests.put(f"{WC_API_URL}/products/{product_id}", auth=auth, json=update_data, verify=False, timeout=20)
            res.raise_for_status()
            with stats['lock']:
                stats['updated'] += 1
        else:
            logger.debug(f"   - ایجاد محصول جدید با {sku} و {len(data['attributes'])} مشخصه فنی...")
            res = requests.post(f"{WC_API_URL}/products", auth=auth, json=data, verify=False, timeout=20)
            res.raise_for_status()
            with stats['lock']:
                stats['created'] += 1
    except requests.exceptions.HTTPError as e:
        logger.error(f"   ❌ HTTP خطا برای SKU {sku}: {e.response.status_code} - Response: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"   ❌ خطای کلی در ارتباط با ووکامرس برای SKU {sku}: {e}")
        raise

@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True
)
def update_to_outofstock(product_id, stats):
    try:
        auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
        update_data = {
            "stock_quantity": 0,
            "stock_status": "outofstock",
            "manage_stock": True
        }
        res = requests.put(f"{WC_API_URL}/products/{product_id}", auth=auth, json=update_data, verify=False, timeout=20)
        res.raise_for_status()
        logger.info(f"   ✅ محصول {product_id} به ناموجود آپدیت شد.")
        with stats['lock']:
            stats['outofstock_updated'] += 1
    except Exception as e:
        logger.error(f"   ❌ خطا در آپدیت محصول {product_id} به ناموجود: {e}")
        with stats['lock']:
            stats['failed'] += 1

# ==============================================================================
# --- برچسب‌گذاری هوشمند سئو محور ---
# ==============================================================================
def smart_tags_for_product(product, cat_map):
    tags = set()
    name = product.get('name', '')
    specs = product.get('specs', {})
    cat_id = product.get('category_id')
    cat_name = cat_map.get(cat_id, '').strip()
    price = int(product.get('price', 0))

    name_parts = [w for w in re.split(r'\s+', name) if w and len(w) > 2]
    common_words = {'گوشی', 'موبایل', 'تبلت', 'لپتاپ', 'لپ‌تاپ', 'مدل', 'محصول', 'کالا', 'جدید'}
    for part in name_parts[:2]:
        if part not in common_words:
            tags.add(part)

    if cat_name and cat_name not in common_words:
        tags.add(cat_name)

    important_keys = ['رنگ', 'Color', 'حافظه', 'ظرفیت', 'اندازه', 'سایز', 'Size', 'مدل', 'برند']
    for key, value in specs.items():
        if any(imp in key for imp in important_keys):
            val = value.strip()
            if 2 < len(val) < 30 and val not in common_words:
                tags.add(val)

    if price > 0:
        if price < 5000000:
            tags.add('اقتصادی')
        elif price > 20000000:
            tags.add('لوکس')

    tags.add('خرید آنلاین')
    tags.add('گارانتی دار')

    tags = {t for t in tags if t and len(t) <= 30 and t.lower() not in ['test', 'spam', 'محصول', 'کالا']}

    return [{"name": t} for t in sorted(tags)]

# ==============================================================================
# --- ارسال محصول به ووکامرس با برچسب هوشمند ---
# ==============================================================================
def process_product_wrapper(args):
    product, stats, category_mapping, cat_map = args
    try:
        wc_cat_id = category_mapping.get(product.get('category_id'))
        if not wc_cat_id:
            logger.warning(f"   ⚠️ دسته برای محصول {product.get('id')} پیدا نشد. رد کردن...")
            with stats['lock']:
                stats['no_category'] = stats.get('no_category', 0) + 1
            return
        specs = product.get('specs', {})
        if not specs:
            logger.warning(f"   ⚠️ مشخصات برای محصول {product.get('id')} خالی است. ارسال بدون attributes.")
        attributes = []
        position = 0
        for key, value in specs.items():
            attributes.append({
                "name": key,
                "options": [value],
                "position": position,
                "visible": True,
                "variation": False
            })
            position += 1

        tags = smart_tags_for_product(product, cat_map)

        wc_data = {
            "name": product.get('name', 'بدون نام'),
            "type": "simple",
            "sku": f"EWAYS-{product.get('id')}",
            "regular_price": process_price(product.get('price', 0)),
            "categories": [{"id": wc_cat_id}],  # فقط برگ (leaf)
            "images": [{"src": abs_url(product.get("image"))}] if product.get("image") else [],
            "stock_quantity": product.get('stock', 0),
            "manage_stock": True,
            "stock_status": "instock" if product.get('stock', 0) > 0 else "outofstock",
            "attributes": attributes,
            "tags": tags,
            "status": "publish"
        }
        _send_to_woocommerce(wc_data['sku'], wc_data, stats)
        time.sleep(random.uniform(0.5, 1.5))
    except Exception as e:
        logger.error(f"   ❌ خطای جدی در پردازش محصول {product.get('id', '')}: {e}")
        with stats['lock']:
            stats['failed'] += 1

# ==============================================================================
# --- پرینت محصولات به صورت شاخه‌ای و مرتب (نسخهٔ جدید با کلید pid) ---
# ==============================================================================
def print_products_tree_by_leaf(products_by_pid, categories):
    cat_map = {cat['id']: cat['name'] for cat in categories}
    tree = defaultdict(list)
    for pid, p in products_by_pid.items():
        catid = p.get('category_id')
        tree[catid].append(p)
    for catid in sorted(tree, key=lambda x: (0 if x is None else int(x))):
        logger.info(f"دسته [{catid}] {cat_map.get(int(catid), 'نامشخص') if catid else 'نامشخص'}:")
        for p in sorted(tree[catid], key=lambda x: int(x['id'])):
            logger.info(f"   - {p['name']} (ID: {p['id']})")

# ==============================================================================
# --- تابع اصلی ---
# ==============================================================================
def main():
    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session:
        logger.error("❌ لاگین به پنل eways انجام نشد. برنامه خاتمه می‌یابد.")
        return

    all_cats = get_and_parse_categories(session)
    if not all_cats:
        logger.error("❌ دسته‌بندی‌ها بارگذاری نشد.")
        return
    logger.info(f"✅ مرحله 1: بارگذاری دسته‌بندی‌ها کامل شد. تعداد: {len(all_cats)}")

    SELECTED_IDS_STRING = "16777:all-allz"
    parsed_selection = parse_selected_ids_string(SELECTED_IDS_STRING)
    logger.info(f"✅ انتخاب‌های دلخواه: {parsed_selection}")

    # انتخاب دسته‌های اسکرپ و انتقال (والدها برای ساختار ووکامرس)
    scrape_categories, transfer_categories = get_selected_categories_according_to_selection(parsed_selection, all_cats)
    logger.info(f"✅ دسته‌های اسکرپ: {[cat['name'] for cat in scrape_categories]}")
    logger.info(f"✅ دسته‌های انتقال (با والدها): {[cat['name'] for cat in transfer_categories]}")

    category_mapping = transfer_categories_to_wc(transfer_categories)
    if not category_mapping:
        logger.error("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد. برنامه خاتمه می‌یابد.")
        return
    logger.info(f"✅ مرحله 5: انتقال دسته‌بندی‌ها کامل شد. تعداد نگاشت‌شده: {len(category_mapping)}")

    # کش را بارگذاری و به فرمت جدید تبدیل کن
    cached_products_raw = load_cache()
    cached_products = normalize_cache(cached_products_raw, transfer_categories)

    # ==============================================================================
    # --- جمع‌آوری محصولات با throttle تطبیقی واقعی (صف دسته + تاخیر مشترک) ---
    # ==============================================================================
    selected_ids = [cat['id'] for cat in scrape_categories]
    all_products = {}
    all_lock = Lock()
    cat_queue = Queue()
    for cat_id in selected_ids:
        cat_queue.put(cat_id)

    # throttle
    shared = {'delay': 0.5}
    delay_lock = Lock()
    min_delay = 0.2
    max_delay = 2.0
    num_cat_workers = 3

    logger.info(f"\n⏳ شروع فرآیند جمع‌آوری تمام محصولات از همه دسته‌بندی‌های انتخابی...")
    pbar = tqdm(total=len(selected_ids), desc="دریافت محصولات دسته‌ها")
    pbar_lock = Lock()

    def cat_worker():
        while True:
            try:
                cat_id = cat_queue.get_nowait()
            except Exception:
                break
            with delay_lock:
                d = shared['delay']
            try:
                products_in_cat = get_products_from_category_page(session, cat_id, 10, d)
                with all_lock:
                    for product in products_in_cat:
                        key = f"{product['id']}|{cat_id}"
                        all_products[key] = product
                with delay_lock:
                    if len(products_in_cat) > 0:
                        shared['delay'] = max(min_delay, shared['delay'] - 0.05)
                    else:
                        shared['delay'] = min(max_delay, shared['delay'] + 0.1)
            except Exception as e:
                logger.warning(f"⚠️ خطا در دریافت محصولات دسته {cat_id}: {e}")
                with delay_lock:
                    shared['delay'] = min(max_delay, shared['delay'] + 0.2)
                logger.warning(f"🚦 سرعت کم شد: delay={shared['delay']:.2f}")
            finally:
                with pbar_lock:
                    pbar.update(1)
                cat_queue.task_done()

    threads = []
    for _ in range(num_cat_workers):
        t = Thread(target=cat_worker, daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    pbar.close()

    logger.info(f"✅ مرحله 6: استخراج محصولات کامل شد. (کل id|cat: {len(all_products)})")

    # تبدیل محصولات به نگاشت بر اساس pid با انتخاب عمیق‌ترین زیرشاخه (leaf)
    canonical_products = condense_products_to_leaf(all_products, transfer_categories)
    logger.info(f"🧭 محصولات پس از نگاشت به عمیق‌ترین زیرشاخه: {len(canonical_products)}")
    print_products_tree_by_leaf(canonical_products, transfer_categories)

    # ==============================================================================
    # --- ادغام با کش و انتخاب فقط محصولات تغییرکرده/جدید (تشخیص تغییر دسته هم انجام می‌شود) ---
    # ==============================================================================
    updated_cache = {}
    changed_items = {}
    new_products_by_category = {}

    for pid, p in canonical_products.items():
        old = cached_products.get(pid)
        # تشخیص تغییر: قیمت، موجودی، مشخصات یا دسته
        if (not old or
            old.get('price') != p.get('price') or
            old.get('stock') != p.get('stock') or
            old.get('specs') != p.get('specs') or
            old.get('category_id') != p.get('category_id')):
            changed_items[pid] = p
            updated_cache[pid] = p
            cat_id = p['category_id']
            new_products_by_category[cat_id] = new_products_by_category.get(cat_id, 0) + 1
        else:
            updated_cache[pid] = old

    changed_count = len(changed_items)
    logger.info(f"✅ مرحله 7: ادغام با کش کامل شد. تعداد محصولات تغییرشده/جدید برای ارسال: {changed_count}")

    logger.info("📊 آمار محصولات جدید/تغییر یافته بر اساس دسته‌بندی (leaf):")
    cat_map = {cat['id']: cat['name'] for cat in transfer_categories}
    for cat_id, count in sorted(new_products_by_category.items(), key=lambda x: -x[1]):
        logger.info(f"  - {cat_map.get(cat_id, str(cat_id))}: {count} محصول")

    save_cache(updated_cache)

    # ==============================================================================
    # --- مدیریت محصولات ناموجود (بدون تکرار) ---
    # ==============================================================================
    logger.info("\n⏳ شروع مدیریت محصولات ناموجود...")
    wc_products = get_all_wc_products_with_prefix("EWAYS-")
    extracted_skus = {f"EWAYS-{pid}" for pid in canonical_products.keys()}
    to_oos_ids = set()

    # با تکیه بر کش قبلی (نرمال شده) + ووکامرس
    for pid in cached_products.keys():
        sku = f"EWAYS-{pid}"
        if sku not in extracted_skus:
            for wc_p in wc_products:
                if wc_p['sku'] == sku and wc_p.get('stock_status') != "outofstock":
                    to_oos_ids.add(wc_p['id'])

    for wc_p in wc_products:
        if wc_p['sku'] not in extracted_skus and wc_p.get('stock_status') != "outofstock":
            to_oos_ids.add(wc_p['id'])

    outofstock_queue = Queue()
    for pid in to_oos_ids:
        outofstock_queue.put(pid)

    outofstock_count = len(to_oos_ids)
    logger.info(f"🔍 تعداد محصولات برای آپدیت به ناموجود: {outofstock_count}")

    # ==============================================================================
    # --- ارسال فقط محصولات تغییرکرده/جدید به ووکامرس (با دسته leaf) ---
    # ==============================================================================
    stats = {'created': 0, 'updated': 0, 'failed': 0, 'no_category': 0, 'outofstock_updated': 0, 'lock': Lock()}
    logger.info(f"\n🚀 شروع پردازش و ارسال {changed_count} محصول (تغییرشده/جدید) به ووکامرس...")

    product_queue = Queue()
    for p in changed_items.values():
        product_queue.put(p)

    def worker():
        while True:
            try:
                product = product_queue.get_nowait()
            except Exception:
                break
            process_product_wrapper((product, stats, category_mapping, cat_map))
            product_queue.task_done()

    num_workers = 3
    threads = []
    for _ in range(num_workers):
        t = Thread(target=worker)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    logger.info(f"\n🚧 شروع آپدیت {outofstock_count} محصول ناموجود در ووکامرس...")

    def outofstock_worker():
        while True:
            try:
                product_id = outofstock_queue.get_nowait()
            except Exception:
                break
            update_to_outofstock(product_id, stats)
            time.sleep(random.uniform(0.5, 1.5))
            outofstock_queue.task_done()

    outofstock_threads = []
    for _ in range(num_workers):
        t = Thread(target=outofstock_worker)
        t.start()
        outofstock_threads.append(t)
    for t in outofstock_threads:
        t.join()

    logger.info("\n===============================")
    logger.info(f"📦 محصولات پردازش شده (موجود): {changed_count}")
    logger.info(f"🟢 ایجاد شده: {stats['created']}")
    logger.info(f"🔵 آپدیت شده: {stats['updated']}")
    logger.info(f"🟠 آپدیت به ناموجود: {stats['outofstock_updated']}")
    logger.info(f"🔴 شکست‌خورده: {stats['failed']}")
    logger.info(f"🟡 بدون دسته: {stats.get('no_category', 0)}")
    logger.info("===============================\nتمام!")

if __name__ == "__main__":
    main()
