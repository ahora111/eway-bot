import requests
import os
import re
import time
import json
import random
from tqdm import tqdm
from bs4 import BeautifulSoup
from threading import Lock, Thread
from queue import Queue
import logging
from logging.handlers import RotatingFileHandler
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type
from collections import defaultdict, Counter
from urllib.parse import urljoin

# ==============================================================================
# تنظیمات لاگینگ (UTF-8)
# ==============================================================================
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=1024*1024, backupCount=5, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# ==============================================================================
# ثابت‌ها و اطلاعات اتصال
# ==============================================================================
BASE_URL = "https://panel.eways.co"
SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"
PRODUCT_DETAIL_URL_TEMPLATE = f"{BASE_URL}/Store/Detail/{{cat_id}}/{{product_id}}"

WC_API_URL = os.environ.get("WC_API_URL") or "https://your-woocommerce-site.com/wp-json/wc/v3"
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY") or "ck_xxx"
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET") or "cs_xxx"

EWAYS_USERNAME = os.environ.get("EWAYS_USERNAME") or "شماره موبایل یا یوزرنیم"
EWAYS_PASSWORD = os.environ.get("EWAYS_PASSWORD") or "پسورد"

CACHE_FILE = 'products_cache.json'

# ==============================================================================
# ابزارهای دسته (ایندکس والد/عمق/نام)
# ==============================================================================
CATEGORY_PARENT = {}
CATEGORY_DEPTH = {}
CATEGORY_NAME = {}

def init_category_index_global(categories):
    global CATEGORY_PARENT, CATEGORY_DEPTH, CATEGORY_NAME
    CATEGORY_PARENT = {c['id']: c.get('parent_id') for c in categories}
    CATEGORY_NAME = {c['id']: (c.get('name') or '').strip() for c in categories}
    CATEGORY_DEPTH = {}
    def depth(cid):
        if cid in CATEGORY_DEPTH:
            return CATEGORY_DEPTH[cid]
        p = CATEGORY_PARENT.get(cid)
        CATEGORY_DEPTH[cid] = 0 if not p else 1 + depth(p)
        return CATEGORY_DEPTH[cid]
    for c in categories:
        depth(c['id'])

def pick_deepest(*cat_ids):
    # انتخاب عمیق‌ترین دسته از بین ورودی‌ها (نادیده گرفتن None)
    candidates = [c for c in cat_ids if c is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda c: CATEGORY_DEPTH.get(c, 0))

def abs_url(u):
    if not u:
        return u
    return u if str(u).startswith('http') else urljoin(BASE_URL, u)

def extract_ids_from_href(href):
    # استخراج cat_id و product_id از /Store/Detail/<cat>/<pid>
    m = re.search(r'/Store/Detail/(\d+)/(\d+)', href or '')
    if not m:
        return None, None
    return int(m.group(1)), m.group(2)

def cat_label(catid):
    if catid is None:
        return "None (نامشخص)"
    name = CATEGORY_NAME.get(catid)
    return f"{catid} ({name if name else 'نامشخص'})"

# ==============================================================================
# توابع انتخاب منعطف با SELECTED_IDS_STRING
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

# خروجی: (دسته‌های اسکرپ، دسته‌های انتقال) — والد فقط اگر خودت در رشته بیاوری
def get_selected_categories_according_to_selection(parsed_selection, all_cats):
    selected_scrape = set()
    selected_transfer = set()

    for block in parsed_selection:
        parent_id = block['parent_id']
        for sel in block['selections']:
            typ, sid = sel['type'], sel['id']

            if typ == 'all_subcats' and sid == parent_id:
                subs = get_direct_subcategories(parent_id, all_cats)
                for sc_id in subs:
                    selected_scrape.add(sc_id)
                    selected_transfer.add(sc_id)

            elif typ == 'only_products' and sid == parent_id:
                selected_scrape.add(parent_id)
                selected_transfer.add(parent_id)

            elif typ == 'all_subcats_and_products' and sid == parent_id:
                selected_scrape.add(parent_id)
                selected_transfer.add(parent_id)
                for sub in get_all_subcategories(parent_id, all_cats):
                    selected_scrape.add(sub)
                    selected_transfer.add(sub)

            elif typ == 'only_products' and sid != parent_id:
                selected_scrape.add(sid)
                selected_transfer.add(sid)

            elif typ == 'all_subcats_and_products' and sid != parent_id:
                selected_scrape.add(sid)
                selected_transfer.add(sid)
                for sub in get_all_subcategories(sid, all_cats):
                    selected_scrape.add(sub)
                    selected_transfer.add(sub)

    scrape_categories = [cat for cat in all_cats if cat['id'] in selected_scrape]
    transfer_categories = [cat for cat in all_cats if cat['id'] in selected_transfer]
    return scrape_categories, transfer_categories

# ==============================================================================
# لاگین
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
    logger.info("⏳ در حال لاگین به پنل eways ...")
    resp = session.post(f"{BASE_URL}/User/Login", data={"UserName": username, "Password": password, "RememberMe": "true"}, timeout=30)
    if resp.status_code != 200:
        logger.error(f"❌ لاگین ناموفق! کد وضعیت: {resp.status_code} - متن پاسخ: {resp.text[:200]}")
        return None
    if 'Aut' in session.cookies:
        logger.info("✅ لاگین موفق! کوکی Aut دریافت شد.")
        return session
    logger.error("❌ کوکی Aut دریافت نشد.")
    return None

# ==============================================================================
# دسته‌ها
# ==============================================================================
def get_and_parse_categories(session):
    logger.info(f"⏳ دریافت دسته‌بندی‌ها از: {SOURCE_CATS_API_URL}")
    try:
        response = session.get(SOURCE_CATS_API_URL, timeout=30)
        response.raise_for_status()
        try:
            data = response.json()
            logger.info("✅ پاسخ JSON است. در حال پردازش با نگاشت والد-فرزند...")
            id_map = {}
            for c in data:
                real_id_match = re.search(r'/Store/List/(\d+)', (c.get('url') or ''))
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
                final_cats.append({"id": real_id, "name": (c.get('name') or '').strip(), "parent_id": parent_real})
            logger.info(f"✅ {len(final_cats)} دسته‌بندی با والد صحیح.")
            return final_cats
        except json.JSONDecodeError:
            logger.warning("⚠️ پاسخ JSON نیست. تلاش برای پارس HTML...")

        soup = BeautifulSoup(response.text, 'lxml')
        all_menu_items = soup.select("li[id^='menu-item-']")
        if not all_menu_items:
            logger.error("❌ هیچ آیتم دسته‌بندی در HTML پیدا نشد.")
            return []
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
        logger.info(f"✅ {len(final_cats)} دسته‌بندی معتبر استخراج شد.")
        return final_cats
    except requests.RequestException as e:
        logger.error(f"❌ خطا در دریافت دسته‌بندی‌ها: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ خطای ناشناخته در پردازش دسته‌بندی‌ها: {e}")
        return None

# ==============================================================================
# جزئیات محصول (specs + دسته نهایی از breadcrumb)
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

        # دسته نهایی از breadcrumb (آخرین لینک List/)
        canonical_cat_id = None
        try:
            selectors = [
                'nav[aria-label="breadcrumb"] a[href*="/Store/List/"]',
                'ul.breadcrumb a[href*="/Store/List/"]',
                'ol.breadcrumb a[href*="/Store/List/"]',
                '.breadcrumb a[href*="/Store/List/"]',
                'a[href*="/Store/List/"]'
            ]
            found = []
            for sel in selectors:
                for a in soup.select(sel):
                    href = a.get('href', '')
                    m = re.search(r'/Store/List/(\d+)', href)
                    if m:
                        found.append(int(m.group(1)))
                if found:
                    break
            if found:
                canonical_cat_id = found[-1]
        except Exception:
            pass

        # جدول مشخصات
        specs_table = soup.select_one('#link1 .table-responsive table') \
                      or soup.select_one('.table-responsive table') \
                      or soup.find('table', class_='table')
        specs = {}
        if specs_table:
            for row in specs_table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) == 2:
                    key = cells[0].text.strip()
                    value = cells[1].text.strip()
                    if key and value:
                        specs[key] = value
        else:
            logger.debug(f"      - هیچ جدولی در صفحه محصول {product_id} پیدا نشد.")

        return specs, canonical_cat_id
    except requests.exceptions.RequestException as e:
        logger.warning(f"      - خطا در دریافت جزئیات محصول {product_id}: {e}. Retry...")
        raise
    except Exception as e:
        logger.warning(f"      - خطا در استخراج مشخصات محصول {product_id}: {e}")
        return {}, None

# ==============================================================================
# استخراج محصولات دسته (HTML + Lazy) با تعیین دقیق دسته leaf
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
        # HTML
        if page == 1:
            url = f"{BASE_URL}/Store/List/{category_id}/2/2/0/0/0/10000000000"
        else:
            url = f"{BASE_URL}/Store/List/{category_id}/2/2/{page-1}/0/0/10000000000?brands=&isMobile=false"
        logger.info(f"⏳ دریافت HTML صفحه {page} برای دسته {cat_label(category_id)} ...")
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
                    link_href = a_tag.get('href', '')
                    cat_from_link, pid = extract_ids_from_href(link_href)
                    if not pid:
                        m = re.search(r'/Store/Detail/\d+/(\d+)', link_href or '')
                        pid = m.group(1) if m else None
                    if not pid:
                        continue
                    name = name_tag.text.strip()
                    price_tag = block.select_one("span.goods-record-price")
                    price_text = price_tag.text.strip() if price_tag else ""
                    price = re.sub(r'[^\d]', '', price_text) if price_text else "0"
                    image_tag = block.select_one("img.goods-record-image")
                    image_url = ""
                    if image_tag:
                        image_url = image_tag.get('data-src', '') or image_tag.get('src', '')
                        image_url = abs_url(image_url)

                    if is_available and pid not in seen_product_ids:
                        # خواندن جزئیات + دسته نهایی
                        specs, canonical_id = get_product_details(session, cat_from_link or category_id, pid)
                        eff_cat = pick_deepest(category_id, cat_from_link, canonical_id)
                        if canonical_id and eff_cat != category_id:
                            logger.debug(f"      - نگاشت دستهٔ محصول {pid}: {cat_label(category_id)} → {cat_label(eff_cat)} (breadcrumb)")
                        html_products.append({
                            'id': pid,
                            'name': name,
                            'category_id': eff_cat,
                            'price': price,
                            'stock': 1,
                            'image': image_url,
                            'specs': specs,
                        })
                        seen_product_ids.add(pid)
                        time.sleep(random.uniform(delay, delay + 0.2))
            logger.info(f"🟢 محصولات موجود (HTML) صفحه {page}: {len(html_products)}")

            # Lazy (فقط موجودها)
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
                    "Available": 1,
                    "MinPrice": 0,
                    "MaxPrice": 10000000000,
                    "IsLazyLoading": "true"
                }
                headers = {
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": referer_url
                }
                logger.info(f"⏳ LazyPageIndex={lazy_page} صفحه {page} برای دسته {cat_label(category_id)} ...")
                resp = session.post(f"{BASE_URL}/Store/ListLazy", data=data, headers=headers, timeout=30)
                if resp.status_code != 200:
                    logger.error(f"❌ خطا در Lazy (کد: {resp.status_code})")
                    break
                try:
                    result = resp.json()
                except Exception as e:
                    logger.error(f"❌ JSON Lazy نامعتبر: {e}")
                    logger.error(f"متن:\n{resp.text[:500]}")
                    break
                if not result or "Goods" not in result or not result["Goods"]:
                    logger.info(f"🚩 انتهای Lazy صفحه {page}.")
                    break
                goods = result["Goods"]
                for g in goods:
                    if not g.get("Availability", True):
                        continue
                    pid = str(g["Id"])
                    if pid in seen_product_ids:
                        continue
                    # تلاش برای گرفتن cat از لینک در خود JSON (اگر باشد)
                    cat_from_link = None
                    for k in ("Url", "Link", "Href", "RelativeUrl"):
                        u = g.get(k)
                        if u and "/Store/Detail/" in u:
                            c, p2 = extract_ids_from_href(u)
                            if c:
                                cat_from_link = c
                            break
                    specs, canonical_id = get_product_details(session, cat_from_link or category_id, pid)
                    eff_cat = pick_deepest(category_id, cat_from_link, canonical_id)
                    lazy_products.append({
                        "id": pid,
                        "name": g["Name"],
                        "category_id": eff_cat,
                        "price": g.get("Price", "0"),
                        "stock": 1,
                        "image": abs_url(g.get("ImageUrl", "")),
                        "specs": specs,
                    })
                    seen_product_ids.add(pid)
                    time.sleep(random.uniform(delay, delay + 0.2))
                logger.info(f"🟢 محصولات موجود (Lazy) این حلقه: {len([g for g in goods if g.get('Availability', True)])}")
                lazy_page += 1

            available_in_page = html_products + lazy_products
            if not available_in_page:
                logger.info(f"⛔️ هیچ محصول موجودی در صفحه {page} نبود. توقف این دسته.")
                break
            all_products_in_category.extend(available_in_page)
            page += 1
            error_count = 0
        except Exception as e:
            error_count += 1
            logger.error(f"    - خطا در پردازش صفحه محصولات: {e} (تعداد خطا: {error_count})")
            if error_count >= 3:
                logger.critical(f"🚨 خطاهای متوالی زیاد در دسته {cat_label(category_id)}. توقف.")
                break
            time.sleep(2)
    logger.info(f"    - کل محصولات موجود استخراج‌شده از دسته {cat_label(category_id)}: {len(all_products_in_category)}")
    return all_products_in_category

# ==============================================================================
# کش محصولات (کلید جدید: فقط id)
# ==============================================================================
def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
            logger.info(f"✅ کش بارگذاری شد. تعداد: {len(cache)}")
            return cache
    logger.info("⚠️ کش پیدا نشد.")
    return {}

def save_cache(products):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(products, f, ensure_ascii=False, indent=4)
    logger.info(f"✅ کش ذخیره شد. تعداد: {len(products)}")

# ==============================================================================
# ووکامرس
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
    logger.info(f"✅ دسته‌های ووکامرس: {len(wc_cats)}")
    return wc_cats

def get_all_wc_products_with_prefix(prefix="EWAYS-"):
    products = []
    page = 1
    while True:
        try:
            res = requests.get(
                f"{WC_API_URL}/products",
                auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET),
                params={"per_page": 100, "page": page},
                verify=False
            )
            res.raise_for_status()
            data = res.json()
            if not data:
                break
            # فقط محصولاتی که SKU با پیشوند مشخص شروع می‌شود
            products.extend([p for p in data if (p.get('sku') or '').startswith(prefix)])
            if len(data) < 100:
                break
            page += 1
        except Exception as e:
            logger.error(f"❌ خطا در دریافت محصولات ووکامرس (صفحه {page}): {e}")
            break
    logger.info(f"✅ محصولات ووکامرس با پیشوند '{prefix}': {len(products)}")
    return products

def check_existing_category(name, parent):
    try:
        res = requests.get(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), params={"search": name, "per_page": 1, "parent": parent}, verify=False)
        res.raise_for_status()
        data = res.json()
        for cat in data:
            if cat["name"].strip() == name and cat["parent"] == parent:
                return cat["id"]
        return None
    except Exception as e:
        logger.debug(f"⚠️ چک وجود دسته '{name}' (parent: {parent}) خطا: {e}")
        return None

def transfer_categories_to_wc(source_categories):
    logger.info("\n⏳ شروع انتقال دسته‌بندی‌ها به ووکامرس...")
    # والدها خودکار اضافه نمی‌شوند؛ فقط ورودی‌ها ساخته می‌شوند
    # ترتیب: والد قبل از فرزند اگر هر دو در ورودی باشند
    sorted_cats = []
    id_to_cat = {cat['id']: cat for cat in source_categories}
    def add_with_parents_if_present(cat):
        pid = cat.get('parent_id')
        if pid and pid in id_to_cat:
            parent_cat = id_to_cat[pid]
            if parent_cat not in sorted_cats:
                add_with_parents_if_present(parent_cat)
        if cat not in sorted_cats:
            sorted_cats.append(cat)
    for cat in source_categories:
        add_with_parents_if_present(cat)

    source_to_wc_id_map = {}
    transferred = 0
    for cat in tqdm(sorted_cats, desc="انتقال دسته‌ها"):
        name = cat["name"].strip()
        parent_id = cat.get("parent_id") or 0
        wc_parent = source_to_wc_id_map.get(parent_id, 0)
        existing_id = check_existing_category(name, wc_parent)
        if existing_id:
            source_to_wc_id_map[cat["id"]] = existing_id
            transferred += 1
            continue
        data = {"name": name, "parent": wc_parent}
        try:
            res = requests.post(f"{WC_API_URL}/products/categories", auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), json=data, verify=False)
            if res.status_code in [200, 201]:
                new_id = res.json()["id"]
                source_to_wc_id_map[cat["id"]] = new_id
                transferred += 1
            else:
                error_data = res.json()
                if error_data.get("code") == "term_exists" and error_data.get("data", {}).get("resource_id"):
                    existing_id = error_data["data"]["resource_id"]
                    source_to_wc_id_map[cat["id"]] = existing_id
                    transferred += 1
                else:
                    logger.error(f"❌ خطا ساخت دسته '{name}' (parent_wc: {wc_parent}): {res.text}")
        except Exception as e:
            logger.error(f"❌ خطای شبکه در ساخت دسته '{name}': {e}")
    logger.info(f"✅ انتقال دسته‌بندی‌ها کامل شد: {transferred}/{len(source_categories)}")
    return source_to_wc_id_map

def process_price(price_value):
    try:
        price_value = float(re.sub(r'[^\d.]', '', str(price_value)))
        price_value /= 10
    except (ValueError, TypeError):
        return "0"
    if price_value <= 1: return "0"
    elif price_value <= 7000000: new_price = price_value + 260000
    elif price_value <= 10000000: new_price = price_value * 1.035
    elif price_value <= 20000000: new_price = price_value * 1.025
    elif price_value <= 30000000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price, -4)))

@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True
)
def _send_to_woocommerce(sku, data, stats, existing_product_id=None):
    try:
        auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
        if existing_product_id:
            update_data = {
                "regular_price": data["regular_price"],
                "stock_quantity": data["stock_quantity"],
                "stock_status": data["stock_status"],
                "attributes": data["attributes"],
                "tags": data.get("tags", []),
                "categories": data.get("categories", []),  # فقط leaf
            }
            res = requests.put(f"{WC_API_URL}/products/{existing_product_id}", auth=auth, json=update_data, verify=False, timeout=20)
            res.raise_for_status()
            with stats['lock']: stats['updated'] += 1
        else:
            res = requests.post(f"{WC_API_URL}/products", auth=auth, json=data, verify=False, timeout=20)
            res.raise_for_status()
            with stats['lock']: stats['created'] += 1
    except requests.exceptions.HTTPError as e:
        logger.error(f"   ❌ HTTP خطا برای {sku}: {e.response.status_code} - {e.response.text[:300]}")
        raise
    except Exception as e:
        logger.error(f"   ❌ خطای ووکامرس برای {sku}: {e}")
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
        update_data = {"stock_quantity": 0, "stock_status": "outofstock", "manage_stock": True}
        res = requests.put(f"{WC_API_URL}/products/{product_id}", auth=auth, json=update_data, verify=False, timeout=20)
        res.raise_for_status()
        logger.info(f"   ✅ محصول {product_id} ناموجود شد.")
        with stats['lock']: stats['outofstock_updated'] += 1
    except Exception as e:
        logger.error(f"   ❌ خطا در ناموجود کردن {product_id}: {e}")
        with stats['lock']: stats['failed'] += 1

# ==============================================================================
# برچسب‌گذاری
# ==============================================================================
def smart_tags_for_product(product, cat_map):
    tags = set()
    name = product.get('name', '')
    specs = product.get('specs', {})
    cat_id = product.get('category_id')
    cat_name = (cat_map.get(cat_id) or '').strip()
    try:
        price = int(product.get('price', 0))
    except:
        price = 0

    name_parts = [w for w in re.split(r'\s+', name) if w and len(w) > 2]
    common_words = {'گوشی','موبایل','تبلت','لپتاپ','لپ‌تاپ','مدل','محصول','کالا','جدید'}
    for part in name_parts[:2]:
        if part not in common_words: tags.add(part)
    if cat_name and cat_name not in common_words: tags.add(cat_name)

    important_keys = ['رنگ','Color','حافظه','ظرفیت','اندازه','سایز','Size','مدل','برند']
    for key, value in specs.items():
        if any(imp in key for imp in important_keys):
            val = value.strip()
            if 2 < len(val) < 30 and val not in common_words:
                tags.add(val)

    if price > 0:
        if price < 5000000: tags.add('اقتصادی')
        elif price > 20000000: tags.add('لوکس')

    tags.update({'خرید آنلاین','گارانتی دار'})
    tags = {t for t in tags if t and len(t) <= 30 and t.lower() not in ['test','spam','محصول','کالا']}
    return [{"name": t} for t in sorted(tags)]

# ==============================================================================
# ارسال محصول به ووکامرس
# ==============================================================================
def process_product_wrapper(args):
    product, stats, category_mapping, cat_map, wc_by_sku = args
    try:
        wc_cat_id = category_mapping.get(product.get('category_id'))
        if not wc_cat_id:
            logger.warning(f"   ⚠️ دسته برای محصول {product.get('id')} پیدا نشد. رد شد.")
            with stats['lock']: stats['no_category'] = stats.get('no_category', 0) + 1
            return
        specs = product.get('specs', {})
        attributes = []
        for idx, (key, value) in enumerate(specs.items()):
            attributes.append({"name": key, "options": [value], "position": idx, "visible": True, "variation": False})

        sku = f"EWAYS-{product.get('id')}"
        existing_wc_id = None
        wcp = wc_by_sku.get(sku)
        if wcp:
            existing_wc_id = wcp.get('id')

        wc_data = {
            "name": product.get('name', 'بدون نام'),
            "type": "simple",
            "sku": sku,
            "regular_price": process_price(product.get('price', 0)),
            "categories": [{"id": wc_cat_id}],  # فقط leaf
            "images": [{"src": abs_url(product.get("image"))}] if product.get("image") else [],
            "stock_quantity": product.get('stock', 0),
            "manage_stock": True,
            "stock_status": "instock" if product.get('stock', 0) > 0 else "outofstock",
            "attributes": attributes,
            "tags": smart_tags_for_product(product, cat_map),
            "status": "publish"
        }
        _send_to_woocommerce(wc_data['sku'], wc_data, stats, existing_product_id=existing_wc_id)
        time.sleep(random.uniform(0.5, 1.5))
    except Exception as e:
        logger.error(f"   ❌ خطا در پردازش محصول {product.get('id','')}: {e}")
        with stats['lock']: stats['failed'] += 1

# ==============================================================================
# ابزارهای تجمیع محصول به leaf و کش
# ==============================================================================
def condense_products_to_leaf(all_products_by_catkey, categories):
    # اگر یک محصول در چند دسته دیده شد، عمیق‌ترین را انتخاب می‌کنیم
    occurrences = defaultdict(list)
    for key, p in all_products_by_catkey.items():
        occurrences[str(p['id'])].append(p)
    canonical = {}
    for pid, plist in occurrences.items():
        best = max(plist, key=lambda p: CATEGORY_DEPTH.get(p.get('category_id'), 0))
        canonical[pid] = best
    return canonical

def normalize_cache(cached_products, categories):
    if not cached_products:
        return {}
    if any('|' in k for k in cached_products.keys()):
        # کش قدیم → تجمیع روی leaf
        all_products_by_catkey = {}
        for key, p in cached_products.items():
            if 'category_id' not in p:
                try:
                    _, catid = key.split('|')
                    p['category_id'] = int(catid)
                except:
                    pass
            all_products_by_catkey[key] = p
        return condense_products_to_leaf(all_products_by_catkey, categories)
    else:
        # کش جدید
        normalized = {}
        for pid, p in cached_products.items():
            if 'category_id' in p and isinstance(p['category_id'], str) and p['category_id'].isdigit():
                p['category_id'] = int(p['category_id'])
            normalized[str(pid)] = p
        return normalized

def print_products_tree_by_leaf(products_by_pid, categories):
    cat_map = {cat['id']: cat['name'] for cat in categories}
    tree = defaultdict(list)
    for pid, p in products_by_pid.items():
        tree[p.get('category_id')].append(p)
    for catid in sorted(tree, key=lambda x: (0 if x is None else int(x))):
        logger.info(f"دسته [{catid}] {cat_map.get(int(catid), 'نامشخص') if catid else 'نامشخص'}:")
        for p in sorted(tree[catid], key=lambda x: int(x['id'])):
            logger.info(f"   - {p['name']} (ID: {p['id']})")

# ==============================================================================
# تابع اصلی
# ==============================================================================
def main():
    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session:
        logger.error("❌ لاگین انجام نشد. پایان.")
        return

    all_cats = get_and_parse_categories(session)
    if not all_cats:
        logger.error("❌ دسته‌بندی‌ها بارگذاری نشد.")
        return
    init_category_index_global(all_cats)

    SELECTED_IDS_STRING = os.environ.get("SELECTED_IDS_STRING") or "1582:14548-allz,1584-all-allz|16777:all-allz|4882:all-allz|16778:22570-all-allz"
    parsed_selection = parse_selected_ids_string(SELECTED_IDS_STRING)

    # انتخاب‌ها (بدون افزودن خودکار والد)
    scrape_categories, transfer_categories = get_selected_categories_according_to_selection(parsed_selection, all_cats)
    scrape_list = [f"{c['id']} ({c['name']})" for c in scrape_categories]
    transfer_list = [f"{c['id']} ({c['name']})" for c in transfer_categories]
    logger.info(f"✅ دسته‌های اسکرپ: {scrape_list}")
    logger.info(f"✅ دسته‌های انتقال: {transfer_list}")

    # ساخت فقط همان دسته‌هایی که خودت خواستی
    category_mapping = transfer_categories_to_wc(transfer_categories)
    if not category_mapping:
        logger.error("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد.")
        return

    # کش
    cached_products_raw = load_cache()
    cached_products = normalize_cache(cached_products_raw, all_cats)

    # جمع‌آوری محصولات با throttle تطبیقی (صف دسته + تاخیر مشترک)
    selected_ids = [cat['id'] for cat in scrape_categories]
    all_products = {}
    all_lock = Lock()
    cat_queue = Queue()
    for cid in selected_ids:
        cat_queue.put(cid)

    shared = {'delay': 0.5}
    delay_lock = Lock()
    min_delay, max_delay = 0.2, 2.0
    num_cat_workers = 3

    logger.info("\n⏳ شروع جمع‌آوری محصولات...")
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
                        key = f"{product['id']}|{product['category_id']}"
                        all_products[key] = product
                with delay_lock:
                    shared['delay'] = max(min_delay, shared['delay'] - 0.05) if len(products_in_cat) > 0 else min(max_delay, shared['delay'] + 0.1)
            except Exception as e:
                logger.warning(f"⚠️ خطا در دسته {cat_label(cat_id)}: {e}")
                with delay_lock:
                    shared['delay'] = min(max_delay, shared['delay'] + 0.2)
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

    logger.info(f"✅ استخراج محصولات تمام شد. (کل کلیدهای id|leaf: {len(all_products)})")

    # انتخاب leaf نهایی برای هر محصول
    canonical_products = condense_products_to_leaf(all_products, all_cats)
    logger.info(f"🧭 محصولات پس از نگاشت به عمیق‌ترین زیرشاخه: {len(canonical_products)}")
    print_products_tree_by_leaf(canonical_products, transfer_categories or all_cats)

    # ——— آمار تعداد محصولات هر دسته (leaf) ———
    cat_counts = Counter(p.get('category_id') for p in canonical_products.values())
    logger.info("📊 آمار تعداد محصولات به تفکیک دسته (leaf):")
    for cid, cnt in sorted(cat_counts.items(), key=lambda kv: (-kv[1], CATEGORY_NAME.get(kv[0], '') or '')):
        logger.info(f"   - {cat_label(cid)}: {cnt}")

    # ادغام با کش و تشخیص تغییر (قیمت/موجودی/مشخصات/دسته)
    updated_cache = {}
    changed_items = {}
    new_products_by_category = {}
    for pid, p in canonical_products.items():
        old = cached_products.get(pid)
        if (not old or
            old.get('price') != p.get('price') or
            old.get('stock') != p.get('stock') or
            old.get('specs') != p.get('specs') or
            old.get('category_id') != p.get('category_id')):
            changed_items[pid] = p
            updated_cache[pid] = p
            cid = p['category_id']
            new_products_by_category[cid] = new_products_by_category.get(cid, 0) + 1
        else:
            updated_cache[pid] = old

    changed_count = len(changed_items)
    logger.info(f"✅ ادغام با کش تمام شد. تعداد تغییرکرده/جدید: {changed_count}")
    save_cache(updated_cache)

    # ============================
    # گپ‌گیری همگام‌سازی با ووکامرس: افزودن موارد مفقود + دسته نامنطبق
    # ============================
    logger.info("\n⛽️ بررسی گپ همگام‌سازی با ووکامرس (افزودن موارد مفقود و دسته‌های نامنطبق)...")
    wc_products = get_all_wc_products_with_prefix("EWAYS-")
    wc_by_sku = {p.get('sku'): p for p in wc_products}
    wc_skus = set(wc_by_sku.keys())

    to_send_items = dict(changed_items)

    # 1) موارد مفقود در ووکامرس
    missing_in_wc = {pid: p for pid, p in canonical_products.items() if f"EWAYS-{pid}" not in wc_skus}
    for pid, p in missing_in_wc.items():
        to_send_items[pid] = p
    logger.info(f"🧩 موارد مفقود در ووکامرس که به ارسال اضافه شدند: {len(missing_in_wc)}")

    # 2) دسته نامنطبق در ووکامرس
    mismatch_count = 0
    for pid, p in canonical_products.items():
        sku = f"EWAYS-{pid}"
        wcp = wc_by_sku.get(sku)
        if not wcp:
            continue
        expected_wc_cat = category_mapping.get(p['category_id'])
        wc_cat_ids = {c.get('id') for c in wcp.get('categories', []) if isinstance(c, dict)}
        if expected_wc_cat and expected_wc_cat not in wc_cat_ids:
            to_send_items[pid] = p
            mismatch_count += 1
    logger.info(f"🧭 موارد با دسته نامنطبق که به ارسال اضافه شدند: {mismatch_count}")

    # ——— آمار اقلامی که قرار است به ووکامرس ارسال شوند ———
    send_counts = Counter(p['category_id'] for p in to_send_items.values())
    logger.info("🛰️ اقلام ارسالی به ووکامرس به تفکیک دسته:")
    for cid, cnt in sorted(send_counts.items(), key=lambda kv: (-kv[1], CATEGORY_NAME.get(kv[0], '') or '')):
        logger.info(f"   - {cat_label(cid)}: {cnt}")

    send_count = len(to_send_items)
    logger.info(f"\n🚀 شروع پردازش و ارسال {send_count} قلم به ووکامرس...")

    # مدیریت ناموجودها (بدون تکرار) با استفاده از همان wc_products
    logger.info("\n⏳ مدیریت محصولات ناموجود...")
    extracted_skus = {f"EWAYS-{pid}" for pid in canonical_products.keys()}
    to_oos_ids = set()
    # از کش قبلی
    for pid in cached_products.keys():
        sku = f"EWAYS-{pid}"
        if sku not in extracted_skus:
            wcp = wc_by_sku.get(sku)
            if wcp and wcp.get('stock_status') != "outofstock":
                to_oos_ids.add(wcp['id'])
    # از ووکامرس
    for wcp in wc_products:
        if wcp['sku'] not in extracted_skus and wcp.get('stock_status') != "outofstock":
            to_oos_ids.add(wcp['id'])

    # صف ارسال
    stats = {'created': 0, 'updated': 0, 'failed': 0, 'no_category': 0, 'outofstock_updated': 0, 'lock': Lock()}

    product_queue = Queue()
    for p in to_send_items.values():
        product_queue.put(p)

    def worker_sender():
        cat_map = {c['id']: c['name'] for c in (transfer_categories or all_cats)}
        while True:
            try:
                product = product_queue.get_nowait()
            except Exception:
                break
            process_product_wrapper((product, stats, category_mapping, cat_map, wc_by_sku))
            product_queue.task_done()

    num_workers = 3
    threads = []
    for _ in range(num_workers):
        t = Thread(target=worker_sender)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    # صف ناموجودها
    outofstock_queue = Queue()
    for pid in to_oos_ids:
        outofstock_queue.put(pid)
    outofstock_count = len(to_oos_ids)
    logger.info(f"\n🚧 آپدیت ناموجودها ({outofstock_count}) ...")

    def outofstock_worker():
        while True:
            try:
                product_id = outofstock_queue.get_nowait()
            except Exception:
                break
            update_to_outofstock(product_id, stats)
            time.sleep(random.uniform(0.5, 1.5))
            outofstock_queue.task_done()

    out_threads = []
    for _ in range(num_workers):
        t = Thread(target=outofstock_worker)
        t.start()
        out_threads.append(t)
    for t in out_threads:
        t.join()

    logger.info("\n===============================")
    logger.info(f"📦 موجود (ارسال‌شده): {send_count}")
    logger.info(f"🟢 ایجاد شده: {stats['created']}")
    logger.info(f"🔵 آپدیت شده: {stats['updated']}")
    logger.info(f"🟠 به ناموجود: {stats['outofstock_updated']}")
    logger.info(f"🔴 شکست: {stats['failed']}")
    logger.info(f"🟡 بدون دسته: {stats.get('no_category', 0)}")
    logger.info("===============================\nتمام!")

if __name__ == "__main__":
    main()
