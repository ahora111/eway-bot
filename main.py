import requests
import os
import re
import time
import json
import random
from tqdm import tqdm
from bs4 import BeautifulSoup
from threading import Lock, Thread, Semaphore
from queue import Queue
import logging
from logging.handlers import RotatingFileHandler
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type
from collections import defaultdict, Counter
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor
import html as html_lib
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# SSL helpers
import certifi
import urllib3

# ==============================================================================
# تنظیمات محیطی سرعت/لاگ/SSL
# ==============================================================================
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
WC_SENDER_WORKERS = int(os.environ.get("WC_SENDER_WORKERS", "6"))
SENDER_SLEEP_SEC = float(os.environ.get("SENDER_SLEEP_SEC", "0.05"))

ALT_SKU_LOOKUP = os.environ.get("ALT_SKU_LOOKUP", "false").lower() == "true"
FORCE_WC_QUERY_AUTH = os.environ.get("FORCE_WC_QUERY_AUTH", "true").lower() == "true"
WC_VERIFY_SSL = os.environ.get("WC_VERIFY_SSL", "true").lower() == "true"
DISABLE_TLS_WARNINGS = os.environ.get("DISABLE_TLS_WARNINGS", "true").lower() == "true"

OUTOFSTOCK_WORKERS = int(os.environ.get("OUTOFSTOCK_WORKERS", "2"))
OUTOFSTOCK_SLEEP_SEC = float(os.environ.get("OUTOFSTOCK_SLEEP_SEC", "0.2"))
OUTOFSTOCK_TIMEOUT = float(os.environ.get("OUTOFSTOCK_TIMEOUT", "45"))
BATCH_SIZE_OUTOFSTOCK = int(os.environ.get("BATCH_SIZE_OUTOFSTOCK", "30"))

# تنظیمات اتصال eways برای اجرا روی GitHub Actions
EWAYS_USE_ENV_PROXIES = os.environ.get("EWAYS_USE_ENV_PROXIES", "false").lower() == "true"  # اگر می‌خوای از env های HTTPS_PROXY/HTTP_PROXY استفاده کنی
EWAYS_PROXY = os.environ.get("EWAYS_PROXY")  # مثل: http://user:pass@host:port
EWAYS_USE_CLOUDSCRAPER = os.environ.get("EWAYS_USE_CLOUDSCRAPER", "false").lower() == "true"  # اختیاری

if DISABLE_TLS_WARNINGS:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# تنظیمات لاگینگ (UTF-8)
# ==============================================================================
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format='%(asctime)s - %(levelname)s - %(message)s')
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

WC_API_URL = os.environ.get("WC_API_URL") or "https://www.dev.pakhshemobile.ir//wp-json/wc/v3"
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY") or "ck_xxx"
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET") or "cs_xxx"

EWAYS_USERNAME = os.environ.get("EWAYS_USERNAME") or "شماره موبایل یا یوزرنیم"
EWAYS_PASSWORD = os.environ.get("EWAYS_PASSWORD") or "پسورد"

CACHE_FILE = 'products_cache.json'

# ==============================================================================
# تنظیمات ریت‌لیمیت جزئیات و سیاست نوسازی
# ==============================================================================
DETAILS_CONCURRENCY = int(os.environ.get("DETAILS_CONCURRENCY", "3"))
DETAILS_MIN_INTERVAL = float(os.environ.get("DETAILS_MIN_INTERVAL", "0.3"))
REFRESH_SPECS_DAYS = int(os.environ.get("REFRESH_SPECS_DAYS", "7"))
ALWAYS_DETAILS_FOR_NEW = os.environ.get("ALWAYS_DETAILS_FOR_NEW", "true").lower() == "true"
CREATE_WITHOUT_DETAILS = os.environ.get("CREATE_WITHOUT_DETAILS", "false").lower() == "true"

# ==============================================================================
# تنظیمات تصاویر: گالری چندعکسی + فیلتر عکس مرده + ضدتکرار کتابخانه رسانه
# ==============================================================================
MAX_IMAGES_PER_PRODUCT = int(os.environ.get("MAX_IMAGES_PER_PRODUCT", "5"))        # سقف عکس هر محصول؛ 0 = بدون محدودیت
DEAD_IMAGE_CHECK = os.environ.get("DEAD_IMAGE_CHECK", "true").lower() == "true"    # فیلتر عکس مرده قبل از ارسال
DEAD_IMAGE_TIMEOUT = float(os.environ.get("DEAD_IMAGE_TIMEOUT", "10"))
DEAD_IMAGE_WORKERS = int(os.environ.get("DEAD_IMAGE_WORKERS", "8"))
GALLERY_BACKFILL = os.environ.get("GALLERY_BACKFILL", "true").lower() == "true"    # تکمیل گالری محصولات موجود (پخش‌شده)
MAX_IMAGE_UPDATES_PER_RUN = int(os.environ.get("MAX_IMAGE_UPDATES_PER_RUN", "40")) # سقف آپدیت گالری در هر اجرا؛ 0 = بی‌نهایت
IMAGES_REPLACE_FOREIGN = os.environ.get("IMAGES_REPLACE_FOREIGN", "true").lower() == "true"  # جایگزینی عکس‌های دستی/خارجی با گالری منبع

# ==============================================================================
# تنظیمات ناموجود/حذف محصول
# ==============================================================================
UNAVAILABLE_MODE = os.environ.get("UNAVAILABLE_MODE", "delete")                     # delete = حذف کامل | outofstock = فقط ناموجود (رفتار قبلی)
UNAVAILABLE_CONFIRM_RUNS = int(os.environ.get("UNAVAILABLE_CONFIRM_RUNS", "2"))    # اجراهای متوالی تأیید قبل از حذف
DELETE_ABSENT_FROM_SOURCE = os.environ.get("DELETE_ABSENT_FROM_SOURCE", "true").lower() == "true"  # محصولاتی که کلاً از پنل حذف شده‌اند
WP_MEDIA_DELETE = os.environ.get("WP_MEDIA_DELETE", "true").lower() == "true"      # حذف قطعی فایل تصاویر از کتابخانه رسانه
CONFIRM_STATE_FILE = 'unavailable_confirm.json'

class SimpleRateLimiter:
    def __init__(self, min_interval):
        self.min_interval = float(min_interval)
        self._last = 0.0
        self._lock = Lock()
    def wait(self):
        with self._lock:
            now = time.monotonic()
            wait_time = self.min_interval - (now - self._last)
            if wait_time > 0:
                time.sleep(wait_time)
            self._last = time.monotonic()

DETAILS_GATE = Semaphore(DETAILS_CONCURRENCY)
DETAILS_RL = SimpleRateLimiter(DETAILS_MIN_INTERVAL)

# ==============================================================================
# تنظیمات SKU و پیشوندهای قابل قبول
# ==============================================================================
SKU_PREFIXES = [s.strip() for s in os.environ.get("SKU_PREFIXES", "EWAYS-,AHORA-").split(",") if s.strip()]
MIGRATE_REMOTE_SKU_TO_CANONICAL = os.environ.get("MIGRATE_REMOTE_SKU_TO_CANONICAL", "false").lower() == "true"

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

# ==============================================================================
# ابزارهای تصاویر: نام فایل، فیلتر عکس مرده، گالری، payload ضدتکرار
# ==============================================================================
# وردپرس برای فایل تکراری -1/-2 و برای تصاویر بزرگ -scaled اضافه می‌کند
WP_SUFFIX_PATTERN = re.compile(r'-(?:\d+|scaled)(\.(?:jpg|jpeg|png|webp|gif))$', re.IGNORECASE)

def img_basename(url):
    """نام فایل نرمال‌شده برای مقایسه ضدتکرار (بدون پسوندهای وردپرس)."""
    if not url:
        return ''
    name = str(url).split('?', 1)[0].split('#', 1)[0].rstrip('/')
    name = name.rsplit('/', 1)[-1].lower()
    name = WP_SUFFIX_PATTERN.sub(r'\1', name)
    return name

def img_basename_ordered(urls):
    return [img_basename(u) for u in (urls or [])]

image_check_session = requests.Session()

def check_image_alive(url):
    """بررسی زنده بودن URL تصویر (HEAD و در صورت نیاز GET با Range)."""
    headers = {"User-Agent": "Mozilla/5.0 (eways-bot image check)"}
    try:
        r = image_check_session.head(url, timeout=DEAD_IMAGE_TIMEOUT, allow_redirects=True, headers=headers)
        if r.status_code == 200:
            ctype = (r.headers.get("Content-Type") or "").lower()
            return (not ctype) or ctype.startswith("image/")
    except requests.exceptions.RequestException:
        pass
    try:
        r = image_check_session.get(url, timeout=DEAD_IMAGE_TIMEOUT, allow_redirects=True,
                                    stream=True, headers={**headers, "Range": "bytes=0-0"})
        ok = r.status_code in (200, 206)
        ctype = (r.headers.get("Content-Type") or "").lower()
        r.close()
        return ok and ((not ctype) or ctype.startswith("image/"))
    except requests.exceptions.RequestException:
        return False

def filter_alive_images(urls):
    """بررسی موازی تصاویر؛ خروجی: (لیست زنده، لیست مرده) با حفظ ترتیب."""
    urls = [u for u in (urls or []) if u]
    if not DEAD_IMAGE_CHECK or not urls:
        return list(urls), []
    with ThreadPoolExecutor(max_workers=max(1, DEAD_IMAGE_WORKERS)) as pool:
        results = dict(zip(urls, pool.map(check_image_alive, urls)))
    alive = [u for u in urls if results.get(u)]
    dead = [u for u in urls if not results.get(u)]
    if dead:
        logger.info(f"🖼️ فیلتر تصاویر: {len(alive)} زنده | {len(dead)} مرده (از ارسال حذف شد)")
    return alive, dead

def extract_gallery_urls(soup):
    """استخراج گالری تصاویر از صفحه جزئیات ایویز (ShowPhotoGalleryDialog یا og:image)."""
    urls = []
    try:
        img = soup.select_one('div.goods-image img[onclick*="ShowPhotoGalleryDialog"]')
        if img:
            onclick = html_lib.unescape(img.get('onclick', '') or '')
            urls = re.findall(r'https?://[^"\'\s]+', onclick)
    except Exception as e:
        logger.debug(f"⚠️ خطا در پارس گالری onclick: {e}")
    if not urls:
        try:
            urls = [m.get('content') for m in soup.select('meta[property="og:image"]') if m.get('content')]
        except Exception:
            pass
    seen, unique = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique

def trim_gallery(urls):
    """محدودسازی تعداد عکس هر محصول (عکس اول = تصویر شاخص)."""
    urls = [u for u in (urls or []) if u]
    if MAX_IMAGES_PER_PRODUCT and len(urls) > MAX_IMAGES_PER_PRODUCT:
        return urls[:MAX_IMAGES_PER_PRODUCT]
    return urls

def wc_image_basenames(existing_images):
    """لیست ترتیبی نام فایل تصاویر فعلی محصول در ووکامرس."""
    out = []
    for img in (existing_images or []):
        if isinstance(img, dict) and img.get('src'):
            out.append(img_basename(img['src']))
    return out

def dedupe_by_basename(urls):
    """حذف URL های هم‌نام (فایل تکراری) با حفظ ترتیب."""
    seen, out = set(), []
    for u in (urls or []):
        b = img_basename(u)
        if b and b not in seen:
            seen.add(b)
            out.append(u)
    return out

def build_images_payload(desired_urls, existing_images):
    """
    ساخت آرایه images برای ووکامرس:
    - عکس‌های موجود با id (بدون دانلود مجدد → بدون تکرار در کتابخانه رسانه)
    - فقط URL های واقعاً جدید با src
    خروجی: (payload, orphan_ids) — یتیم‌ها بعد از موفقیت PUT حذف می‌شوند.
    """
    ex_map = {}
    for img in (existing_images or []):
        if isinstance(img, dict) and img.get('id') and img.get('src'):
            ex_map.setdefault(img_basename(img['src']), img['id'])
    payload, referenced = [], set()
    for u in (desired_urls or []):
        b = img_basename(u)
        if b in ex_map:
            payload.append({"id": ex_map[b]})
            referenced.add(ex_map[b])
        else:
            payload.append({"src": u})
    orphans = [img['id'] for img in (existing_images or [])
               if isinstance(img, dict) and img.get('id') and img['id'] not in referenced]
    return payload, orphans

def get_unavailable_products(session, category_id):
    """
    دریافت لیست محصولات ناموجود (تأییدشده) یک دسته از API لِیزی ایویز (Available=0).
    خروجی: (موفق بودن دریافت کامل؟, دیکشنری pid → نام)
    """
    unavailable = {}
    lazy_page = 1
    referer_url = f"{BASE_URL}/Store/List/{category_id}/2/2/0/0/0/10000000000"
    while lazy_page <= 50:  # محدودیت ایمنی
        data = {
            "ListViewType": 0,
            "CatId": category_id,
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
            "Referer": referer_url,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
        }
        try:
            resp = session.post(f"{BASE_URL}/Store/ListLazy", data=data, headers=headers, timeout=30)
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ خطای شبکه در دریافت ناموجودهای دسته {cat_label(category_id)}: {e}")
            return False, {}
        if resp.status_code != 200:
            logger.error(f"❌ دریافت ناموجودهای دسته {cat_label(category_id)} - status: {resp.status_code}")
            return False, {}
        try:
            result = resp.json()
        except Exception:
            logger.error(f"❌ JSON ناموجودهای دسته {cat_label(category_id)} نامعتبر است.")
            return False, {}
        if not isinstance(result, dict):
            # صفحه خارج از محدوده → پایان طبیعی لیست (null)؛ ولی صفحه اول null یعنی مشکل واقعی
            if lazy_page == 1:
                logger.warning(f"⚠️ پاسخ ناموجودهای دسته {cat_label(category_id)} نامعتبر است (صفحه اول).")
                return False, {}
            break
        goods = result.get("Goods") or []
        if not goods:
            break
        for g in goods:
            if g.get("Availability", True) is False:
                unavailable[str(g.get("Id"))] = g.get("Name", "")
        lazy_page += 1
        time.sleep(random.uniform(0.05, 0.2))
    return True, unavailable

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
                    selected_scrape.add(sc_id); selected_transfer.add(sc_id)
            elif typ == 'only_products' and sid == parent_id:
                selected_scrape.add(parent_id); selected_transfer.add(parent_id)
            elif typ == 'all_subcats_and_products' and sid == parent_id:
                selected_scrape.add(parent_id); selected_transfer.add(parent_id)
                for sub in get_all_subcategories(parent_id, all_cats):
                    selected_scrape.add(sub); selected_transfer.add(sub)
            elif typ == 'only_products' and sid != parent_id:
                selected_scrape.add(sid); selected_transfer.add(sid)
            elif typ == 'all_subcats_and_products' and sid != parent_id:
                selected_scrape.add(sid); selected_transfer.add(sid)
                for sub in get_all_subcategories(sid, all_cats):
                    selected_scrape.add(sub); selected_transfer.add(sub)
    scrape_categories = [cat for cat in all_cats if cat['id'] in selected_scrape]
    transfer_categories = [cat for cat in all_cats if cat['id'] in selected_transfer]
    return scrape_categories, transfer_categories

# ==============================================================================
# لاگین به eways (مقاوم برای GitHub Actions)
# ==============================================================================
def login_eways(username, password):
    # امکان عبور از کپچا/OTP با کوکی مرورگر (اختیاری)
    EWAYS_COOKIE = os.environ.get("EWAYS_COOKIE", "").strip()  # مثال: "Aut=...; other=..."

    # Session: ترجیحاً requests، در صورت نیاز cloudscraper (اختیاری)
    session = None
    if EWAYS_USE_CLOUDSCRAPER:
        try:
            import cloudscraper
            session = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})
            logger.info("🧩 cloudscraper فعال شد.")
        except Exception as e:
            logger.warning(f"⚠️ cloudscraper در دسترس نیست/خطا: {e}. از requests استفاده می‌کنیم.")
            session = requests.Session()
    else:
        session = requests.Session()

    # SSL سایت مشکل CA دارد
    session.verify = False

    # پروکسی‌ها
    session.trust_env = EWAYS_USE_ENV_PROXIES  # اگر می‌خوای از HTTPS_PROXY/HTTP_PROXY استفاده کنی، این رو true کن
    if EWAYS_PROXY:
        session.proxies.update({'http': EWAYS_PROXY, 'https': EWAYS_PROXY})

    # Retry شبکه برای قطع و وصل‌های موقت
    retry_conf = Retry(
        total=3, connect=3, read=3,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"])
    )
    adapter = HTTPAdapter(max_retries=retry_conf)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # هدرهای شبیه مرورگر معمولی (بدون X-Requested-With در لاگین)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7',
        'Referer': f"{BASE_URL}/User/Login",
        'Origin': BASE_URL,
    })

    def _add_cookie_header_to_jar(cookie_header, domain="panel.eways.co"):
        if not cookie_header:
            return
        for part in cookie_header.split(';'):
            part = part.strip()
            if '=' not in part:
                continue
            k, v = part.split('=', 1)
            session.cookies.set(k.strip(), v.strip(), domain=domain, path='/')

    try:
        # اگر کوکی آماده داری اول تست کن
        if EWAYS_COOKIE:
            logger.info("🔑 استفاده از کوکی محیطی برای عبور از لاگین ...")
            _add_cookie_header_to_jar(EWAYS_COOKIE)
            try:
                test = session.get(SOURCE_CATS_API_URL, timeout=20, allow_redirects=False)
                if test.status_code in (200, 302, 304):
                    logger.info("✅ کوکی معتبر است. لاگین را رد می‌کنیم.")
                    return session
                else:
                    logger.warning(f"⚠️ کوکی معتبر نبود (status={test.status_code}). ادامه با لاگین معمولی.")
            except Exception as e:
                logger.warning(f"⚠️ تست کوکی خطا داد: {e}. ادامه با لاگین.")

        logger.info("⏳ در حال لاگین به پنل eways ...")

        # Preflight
        try:
            session.get(f"{BASE_URL}/", timeout=15, allow_redirects=True)
        except Exception:
            pass
        r1 = session.get(f"{BASE_URL}/User/Login", timeout=20, allow_redirects=True)

        # تلاش برای گرفتن CSRF (اگر وجود داشته باشد)
        csrf_token = None
        try:
            soup = BeautifulSoup(r1.text or "", 'lxml')
            tok = soup.select_one('input[name="__RequestVerificationToken"]')
            if tok and tok.get('value'):
                csrf_token = tok['value']
                logger.debug("🔐 CSRF token یافت شد.")
        except Exception:
            pass

        form_data = {
            "UserName": username,
            "Password": password,
            "RememberMe": "true",  # در صورت نیاز "on" را هم می‌توان امتحان کرد
        }
        if csrf_token:
            form_data["__RequestVerificationToken"] = csrf_token

        # مهم: ریدایرکت را خودمان هندل کنیم تا در resolve_redirects تایم‌اوت نخوریم
        resp = session.post(f"{BASE_URL}/User/Login",
                            data=form_data,
                            timeout=(10, 40),  # 10s connect, 40s read
                            allow_redirects=False)

        logger.info(f"🔁 پاسخ لاگین: status={resp.status_code} | Location={resp.headers.get('Location', '')}")

        # دنبال‌کردن یک ریدایرکت (در صورت نیاز)
        if resp.status_code in (301, 302, 303, 307, 308):
            next_url = urljoin(BASE_URL, resp.headers.get('Location', '/'))
            r2 = session.get(next_url, timeout=40, allow_redirects=True)
            logger.info(f"➡️ دنبال‌کردن ریدایرکت به: {next_url} | status={r2.status_code}")

        # بررسی کوکی Aut
        if 'Aut' in session.cookies:
            logger.info("✅ لاگین موفق! کوکی Aut دریافت شد.")
            return session

        # گاهی لاگین موفق است ولی نام کوکی متفاوت/مسیر دیگر
        cats_check = session.get(SOURCE_CATS_API_URL, timeout=20, allow_redirects=False)
        if cats_check.status_code in (200, 302, 304):
            logger.info("✅ به‌نظر سشن معتبر است (چک دسته‌ها موفق).")
            return session

        # تشخیص احتمالی کپچا/چلنچ
        short = (resp.text or "")[:300].lower()
        if "captcha" in short or "turnstile" in short or "cf_chl" in short:
            logger.error("⚠️ احتمال کپچا/Cloudflare challenge. راهکار: EWAYS_COOKIE از مرورگر یا cloudscraper/پروکسی.")
        logger.error(f"❌ لاگین ناموفق. status={resp.status_code}, متن کوتاه: {(resp.text or '')[:200]}")
        return None

    except requests.exceptions.ReadTimeout as e:
        logger.error(f"⏱️ ReadTimeout در لاگین. احتمالاً WAF/سرعت پایین یا بلاک IP. جزئیات: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ خطای لاگین: {e}")
        return None

# ==============================================================================
# دسته‌ها از eways
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
# جزئیات محصول از eways
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
        with DETAILS_GATE:
            DETAILS_RL.wait()
            response = session.get(url, timeout=60)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'lxml')

        # دسته نهایی از breadcrumb
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

        # گالری تصاویر از صفحه جزئیات (عکس اول = شاخص)
        gallery_urls = extract_gallery_urls(soup)

        return specs, canonical_cat_id, gallery_urls
    except requests.exceptions.RequestException as e:
        logger.warning(f"      - خطا در دریافت جزئیات محصول {product_id}: {e}. Retry...")
        raise
    except Exception as e:
        logger.warning(f"      - خطا در استخراج مشخصات محصول {product_id}: {e}")
        return {}, None, []

# ==============================================================================
# استخراج محصولات دسته (HTML + Lazy) - Light
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
                        eff_cat_guess = pick_deepest(category_id, cat_from_link)
                        html_products.append({
                            'id': pid,
                            'name': name,
                            'category_id': eff_cat_guess,
                            'detail_hint_cat_id': cat_from_link or category_id,
                            'price': price,
                            'stock': 1,
                            'image': image_url,
                            'specs': {},  # فعلا نداریم
                        })
                        seen_product_ids.add(pid)
            logger.info(f"🟢 محصولات موجود (HTML) صفحه {page}: {len(html_products)}")

            # Lazy
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
                    # تلاش برای گرفتن cat از لینک
                    cat_from_link = None
                    for k in ("Url", "Link", "Href", "RelativeUrl"):
                        u = g.get(k)
                        if u and "/Store/Detail/" in u:
                            c, p2 = extract_ids_from_href(u)
                            if c:
                                cat_from_link = c
                            break
                    eff_cat_guess = pick_deepest(category_id, cat_from_link)
                    lazy_products.append({
                        "id": pid,
                        "name": g["Name"],
                        "category_id": eff_cat_guess,
                        "detail_hint_cat_id": cat_from_link or category_id,
                        "price": g.get("Price", "0"),
                        "stock": 1,
                        "image": abs_url(g.get("ImageUrl", "")),
                        "specs": {},
                    })
                    seen_product_ids.add(pid)
                logger.info(f"🟢 محصولات موجود (Lazy) این حلقه: {len([g for g in goods if g.get('Availability', True)])}")
                lazy_page += 1

            available_in_page = html_products + lazy_products
            if not available_in_page:
                logger.info(f"⛔️ هیچ محصول موجودی در صفحه {page} نبود. توقف این دسته.")
                break
            all_products_in_category.extend(available_in_page)
            page += 1
            error_count = 0
            time.sleep(random.uniform(delay, delay + 0.2))
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
# کش محصولات
# ==============================================================================
def load_cache():
    """
    بارگذاری امن کش محصولات.
    فایل خالی/خراب باعث توقف برنامه نمی‌شود.
    """
    if not os.path.exists(CACHE_FILE):
        logger.info("⚠️ فایل کش پیدا نشد؛ کش خالی استفاده می‌شود.")
        return {}

    try:
        if os.path.getsize(CACHE_FILE) == 0:
            logger.warning("⚠️ فایل کش خالی است؛ کش خالی استفاده می‌شود.")
            return {}

        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)

        if not isinstance(cache, dict):
            logger.warning("⚠️ ساختار products_cache.json معتبر نیست؛ کش خالی استفاده می‌شود.")
            return {}

        logger.info(f"✅ کش بارگذاری شد. تعداد: {len(cache)}")
        return cache

    except json.JSONDecodeError as e:
        logger.error(f"⚠️ products_cache.json خراب یا نامعتبر است: {e}")
        logger.warning("↩️ برنامه با کش خالی ادامه می‌دهد.")
        return {}

    except OSError as e:
        logger.error(f"⚠️ خطا در خواندن فایل کش: {e}")
        return {}


def save_cache(products):
    """
    ذخیره امن کش محصولات.
    ابتدا فایل موقت نوشته می‌شود و سپس جایگزین فایل اصلی می‌شود.
    """
    temp_file = CACHE_FILE + ".tmp"

    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(products, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_file, CACHE_FILE)
        logger.info(f"✅ کش ذخیره شد. تعداد: {len(products)}")

    except Exception as e:
        logger.error(f"❌ خطا در ذخیره کش: {e}")
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except Exception:
            pass

# ==============================================================================
# وضعیت تأیید حذف محصولات ناموجود (شمارنده اجراهای متوالی)
# ==============================================================================
def load_confirm_state():
    """بارگذاری وضعیت تأیید حذف؛ فایل خراب برنامه را متوقف نمی‌کند."""
    try:
        if os.path.exists(CONFIRM_STATE_FILE) and os.path.getsize(CONFIRM_STATE_FILE) > 0:
            with open(CONFIRM_STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f"⚠️ فایل وضعیت تأیید خوانده نشد ({e})؛ خالی استفاده می‌شود.")
    return {}

def save_confirm_state(state):
    """ذخیره امن وضعیت تأیید حذف (نوشتن اتمی با فایل موقت)."""
    temp_file = CONFIRM_STATE_FILE + ".tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, CONFIRM_STATE_FILE)
    except Exception as e:
        logger.error(f"❌ خطا در ذخیره وضعیت تأیید: {e}")

# ==============================================================================
# رَپر ووکامرس (Query Auth + Session + SSL verify)
# ==============================================================================
wc_session = requests.Session()
wc_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36'
})
# SSL verify (بهتر است فعال باشد)
wc_session.verify = certifi.where() if WC_VERIFY_SSL else False

def wc_request(method, path, params=None, json=None, timeout=30, allow_redirects=True):
    url = f"{WC_API_URL}{path}"
    params = dict(params or {})
    auth = None
    if FORCE_WC_QUERY_AUTH:
        params.update({"consumer_key": WC_CONSUMER_KEY, "consumer_secret": WC_CONSUMER_SECRET})
    else:
        auth = (WC_CONSUMER_KEY, WC_CONSUMER_SECRET)
    res = wc_session.request(method=method.upper(), url=url, params=params, json=json,
                             auth=auth, timeout=timeout, allow_redirects=allow_redirects)
    return res

# ==============================================================================
# حذف فایل‌های رسانه (wp/v2) برای آزادسازی فضای هاست
# ==============================================================================
def _wp_v2_base_url():
    if "/wc/v3" in WC_API_URL:
        return WC_API_URL.split("/wc/v3")[0].rstrip('/') + "/wp/v2"
    return None

WP_API_URL = _wp_v2_base_url()
_wp_media_auth_warned = False

def wp_request(method, path, params=None, timeout=30):
    """درخواست به REST وردپرس (wp/v2) با همان کلیدهای ووکامرس."""
    if not WP_API_URL:
        return None
    url = f"{WP_API_URL}{path}"
    params = dict(params or {})
    params.update({"consumer_key": WC_CONSUMER_KEY, "consumer_secret": WC_CONSUMER_SECRET})
    try:
        return wc_session.request(method=method.upper(), url=url, params=params,
                                  auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), timeout=timeout)
    except Exception as e:
        logger.error(f"❌ خطای شبکه wp/v2 ({path}): {e}")
        return None

def delete_media_attachments(media_ids):
    """حذف قطعی پیوست‌های رسانه برای آزادسازی فضا. خروجی: تعداد حذف‌شده."""
    global _wp_media_auth_warned
    media_ids = [int(m) for m in (media_ids or []) if m]
    if not WP_MEDIA_DELETE or not media_ids:
        return 0
    deleted = 0
    for mid in media_ids:
        res = wp_request("delete", f"/media/{mid}", params={"force": "true"}, timeout=40)
        if res is None:
            break
        if res.status_code in (200, 201, 404):  # 404 = قبلاً حذف شده
            deleted += 1
            continue
        if res.status_code in (401, 403):
            if not _wp_media_auth_warned:
                logger.warning(
                    "⚠️ حذف فایل رسانه با کلیدهای ووکامرس مجاز نشد (401/403 روی wp/v2). "
                    "فایل‌های تصویر در هاست باقی می‌مانند. برای پاک‌سازی خودکار، یک Application Password در وردپرس بسازید."
                )
                _wp_media_auth_warned = True
            break
        logger.error(f"❌ حذف رسانه {mid} ناموفق: {res.status_code} - {res.text[:150]}")
    return deleted

# ==============================================================================
# ووکامرس: دسته‌ها و محصولات
# ==============================================================================
def get_wc_categories():
    wc_cats, page = [], 1
    while True:
        try:
            res = wc_request("get", "/products/categories", params={"per_page": 100, "page": page}, timeout=30)
            if res.status_code == 401:
                logger.error(f"❌ احراز هویت دسته‌ها 401: {res.text[:200]}")
                break
            res.raise_for_status()
            data = res.json()
            if not data:
                break
            wc_cats.extend(data)
            total_pages = int(res.headers.get("X-WP-TotalPages", "1"))
            if page >= total_pages:
                break
            page += 1
        except Exception as e:
            logger.error(f"❌ خطا در دریافت دسته‌بندی‌های ووکامرس: {e}")
            break
    logger.info(f"✅ دسته‌های ووکامرس: {len(wc_cats)}")
    return wc_cats

def get_all_wc_products_with_prefixes(prefixes=None):
    prefixes = prefixes or SKU_PREFIXES
    products = []
    page = 1
    while True:
        try:
            res = wc_request("get", "/products", params={"per_page": 100, "page": page, "status": "any"}, timeout=40)
            if res.status_code == 401:
                logger.error(f"❌ احراز هویت محصولات 401: {res.text[:200]}")
                break
            res.raise_for_status()
            data = res.json()
            if not data:
                break
            for p in data:
                sku = (p.get('sku') or '')
                if any(sku.startswith(pref) for pref in prefixes):
                    products.append(p)
            total_pages = int(res.headers.get("X-WP-TotalPages", "1"))
            if page >= total_pages:
                break
            page += 1
        except Exception as e:
            logger.error(f"❌ خطا در دریافت محصولات ووکامرس (صفحه {page}): {e}")
            break
    logger.info(f"✅ محصولات ووکامرس با پیشوندهای {prefixes}: {len(products)}")
    return products

def find_wc_product_id_by_sku(sku):
    try:
        res = wc_request("get", "/products", params={"sku": sku, "status": "any", "per_page": 100}, timeout=20)
        res.raise_for_status()
        items = res.json()
        if items:
            return items[0].get("id")
        return None
    except Exception as e:
        logger.debug(f"⚠️ جستجوی SKU در ووکامرس خطا داد ({sku}): {e}")
        return None

def find_wc_product_id_by_possible_skus(pid):
    for prefix in SKU_PREFIXES:
        sku_try = f"{prefix}{pid}"
        pid_found = find_wc_product_id_by_sku(sku_try)
        if pid_found:
            return pid_found, sku_try
    return None, None

def check_existing_category(name, parent):
    try:
        res = wc_request("get", "/products/categories", params={"search": name, "per_page": 1, "parent": parent}, timeout=20)
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
            res = wc_request("post", "/products/categories", json=data, timeout=40)
            if res.status_code in [200, 201]:
                new_id = res.json()["id"]
                source_to_wc_id_map[cat["id"]] = new_id
                transferred += 1
            else:
                # term_exists
                try:
                    error_data = res.json()
                except Exception:
                    error_data = {}
                if error_data.get("code") == "term_exists" and error_data.get("data", {}).get("resource_id"):
                    existing_id = error_data["data"]["resource_id"]
                    source_to_wc_id_map[cat["id"]] = existing_id
                    transferred += 1
                else:
                    logger.error(f"❌ خطا ساخت دسته '{name}' (parent_wc: {wc_parent}): {res.text[:200]}")
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

# ==============================================================================
# ارسال/آپدیت ووکامرس (تصاویر جداگانه تا خطای تصویر، کل آپدیت را خراب نکند)
# خروجی: (ok, images_ok) — images_ok: None=تصویری در کار نبود، True/False=وضعیت تصاویر
# ==============================================================================
def _update_payload(data, include_images=True):
    payload = {
        "regular_price": data["regular_price"],
        "stock_quantity": data["stock_quantity"],
        "stock_status": data["stock_status"],
        "categories": data.get("categories", []),
    }
    if data.get("attributes") is not None:
        payload["attributes"] = data["attributes"]
    if data.get("tags") is not None:
        payload["tags"] = data["tags"]
    if include_images and data.get("images"):
        payload["images"] = data["images"]
    if MIGRATE_REMOTE_SKU_TO_CANONICAL:
        payload["sku"] = data["sku"]
    return payload

def _put_images_safely(wc_id, data, sku, stats):
    """PUT فقط تصاویر روی محصول موجود. خروجی: True/False."""
    try:
        res = wc_request("put", f"/products/{wc_id}", json={"images": data["images"]}, timeout=60)
        res.raise_for_status()
        return True
    except requests.exceptions.HTTPError as e:
        logger.error(f"   ⚠️ ارسال تصاویر برای {sku} ناموفق بود (محصول بدون تغییر ماند): {str(e)[:200]}")
        return False
    except Exception as e:
        logger.error(f"   ⚠️ خطای ارسال تصاویر برای {sku}: {e}")
        return False

@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True
)
def _send_to_woocommerce(sku, data, stats, existing_product_id=None):
    try:
        if existing_product_id:
            res = wc_request("put", f"/products/{existing_product_id}",
                             json=_update_payload(data, include_images=True), timeout=40)
            res.raise_for_status()
            with stats['lock']: stats['updated'] += 1
            return (True, True if data.get("images") else None)
        else:
            # ساخت: ترجیحاً با جزئیات؛ اگر نداریم و اجازه false است، رد
            if (data.get("attributes") is None) and (not CREATE_WITHOUT_DETAILS):
                logger.warning(f"   ⚠️ ساخت {sku} رد شد؛ جزئیات نداریم و CREATE_WITHOUT_DETAILS=false است.")
                with stats['lock']: stats['failed'] += 1
                return (False, None)
            # ساخت همیشه بدون تصویر تا خطای تصویر باعث fail کل نشود
            create_data = {k: v for k, v in data.items() if k != "images"}
            try:
                res = wc_request("post", "/products", json=create_data, timeout=40)
                res.raise_for_status()
                with stats['lock']: stats['created'] += 1
                new_id = None
                try:
                    new_id = res.json().get("id")
                except Exception:
                    pass
                images_ok = None
                if data.get("images") and new_id:
                    images_ok = _put_images_safely(new_id, data, sku, stats)
                return (True, images_ok)
            except requests.exceptions.HTTPError as e:
                try:
                    payload = e.response.json()
                except Exception:
                    payload = {}
                code = (payload or {}).get("code")
                resource_id = (payload or {}).get("data", {}).get("resource_id")
                if code in ("product_invalid_sku", "woocommerce_product_sku_already_exists") and resource_id:
                    logger.info(f"   🔄 SKU تکراری برای {sku}؛ آپدیت روی resource_id={resource_id}")
                    res2 = wc_request("put", f"/products/{resource_id}",
                                      json=_update_payload(data, include_images=True), timeout=40)
                    res2.raise_for_status()
                    with stats['lock']: stats['updated'] += 1
                    return (True, True if data.get("images") else None)
                else:
                    logger.error(f"   ❌ HTTP خطا برای {sku}: {e.response.status_code} - {e.response.text[:300]}")
                    raise
    except requests.exceptions.HTTPError as e:
        # خطای مخصوص تصویر: بدون تصویر دوباره تلاش می‌کنیم تا قیمت/موجودی حتماً ذخیره شود
        code = None
        try:
            code = e.response.json().get("code")
        except Exception:
            pass
        if code == "woocommerce_product_image_upload_error" and existing_product_id:
            logger.warning(f"   ⚠️ خطای آپلود تصویر برای {sku}؛ آپدیت بدون تصویر انجام می‌شود و تصاویر در اجرای بعدی تلاش می‌شوند.")
            res = wc_request("put", f"/products/{existing_product_id}",
                             json=_update_payload(data, include_images=False), timeout=40)
            res.raise_for_status()
            with stats['lock']: stats['updated'] += 1
            return (True, False)
        logger.error(f"   ❌ HTTP خطا برای {sku}: {e.response.status_code} - {e.response.text[:300]}")
        raise
    except Exception as e:
        logger.error(f"   ❌ خطای ووکامرس برای {sku}: {e}")
        raise

# ==============================================================================
# ناموجود کردن: Batch + Fallback تکی با retry
# ==============================================================================
def chunked(iterable, size):
    iterable = list(iterable)
    for i in range(0, len(iterable), size):
        yield iterable[i:i+size]

def mark_outofstock_batch(ids):
    if not ids:
        return True, []
    payload = {"update": [
        {"id": int(pid), "manage_stock": True, "stock_quantity": 0, "stock_status": "outofstock"}
        for pid in ids
    ]}
    res = wc_request("post", "/products/batch", json=payload, timeout=max(60, OUTOFSTOCK_TIMEOUT))
    if res.status_code >= 400:
        return False, ids
    try:
        data = res.json()
    except Exception:
        return False, ids
    # سعی می‌کنیم بفهمیم کدام‌ها موفق شدند
    succeeded = set()
    failed = set()
    for item in (data.get("update") or []):
        pid = item.get("id")
        if pid:
            succeeded.add(int(pid))
    # اگر Woo به‌خاطر خطاهایی برخی را برگرداند، باقی را failed می‌گذاریم
    for pid in ids:
        if int(pid) not in succeeded:
            failed.add(int(pid))
    return True, list(failed)

@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError, requests.exceptions.Timeout)),
    stop=stop_after_attempt(5),
    wait=wait_random_exponential(multiplier=1, max=15),
    reraise=True
)
def update_to_outofstock_single(product_id):
    update_data = {"stock_quantity": 0, "stock_status": "outofstock", "manage_stock": True}
    res = wc_request("put", f"/products/{product_id}", json=update_data, timeout=OUTOFSTOCK_TIMEOUT)
    res.raise_for_status()

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
# ارسال محصول به ووکامرس (تصاویر مشروط)
# ==============================================================================
def process_product_wrapper(args):
    product, stats, category_mapping, cat_map, wc_by_sku, wc_missing_image_skus = args
    try:
        wc_cat_id = category_mapping.get(product.get('category_id'))
        if not wc_cat_id:
            logger.warning(f"   ⚠️ دسته برای محصول {product.get('id')} پیدا نشد. رد شد.")
            with stats['lock']: stats['no_category'] = stats.get('no_category', 0) + 1
            return

        specs = product.get('specs') or {}
        has_details = bool(specs)

        attributes = None
        if has_details:
            attributes = []
            for idx, (key, value) in enumerate(specs.items()):
                attributes.append({"name": key, "options": [value], "position": idx, "visible": True, "variation": False})

        pid_str = str(product.get('id'))
        canonical_sku = f"EWAYS-{pid_str}"
        sku = canonical_sku

        # وجود در WC (بدون GET اضافه)
        existing_wc_id = None
        existing_wcp = None
        candidate_skus = [f"{pref}{pid_str}" for pref in SKU_PREFIXES]
        for s in candidate_skus:
            wcp = wc_by_sku.get(s)
            if wcp:
                existing_wc_id = wcp.get('id')
                existing_wcp = wcp
                break

        # جست‌وجوی alt SKU (اختیاری برای سرعت)
        if not existing_wc_id and ALT_SKU_LOOKUP:
            alt_id, alt_sku = find_wc_product_id_by_possible_skus(pid_str)
            if alt_id:
                logger.info(f"🔎 محصول یافت شد با SKU جایگزین: {alt_sku} → ID={alt_id} (آپدیت به‌جای ساخت)")
                existing_wc_id = alt_id

        # ==================================================================
        # تصاویر: گالری چندعکسی + ضدتکرار (مقایسه نام فایل) + فیلتر عکس مرده
        # ==================================================================
        existing_images = (existing_wcp or {}).get('images') or []
        existing_basenames = wc_image_basenames(existing_images)
        ex_basename_set = set(existing_basenames)

        src_urls = [abs_url(u) for u in (product.get('images_all') or []) if u]
        if not src_urls and product.get("image"):
            src_urls = [abs_url(product.get("image"))]
        desired_urls = trim_gallery(src_urls)

        images_data = None
        orphan_ids = []
        if desired_urls:
            dedup_urls = dedupe_by_basename(desired_urls)
            final_basenames = img_basename_ordered(dedup_urls)
            if final_basenames == existing_basenames:
                logger.debug(f"   🖼️ تصاویر {sku} بدون تغییر؛ هیچ درخواستی زده نشد (ضدتکرار).")
            elif (not IMAGES_REPLACE_FOREIGN) and existing_basenames \
                    and (not set(existing_basenames).issubset(set(final_basenames))):
                logger.info(f"   🖼️ تصاویر {sku} دستی/خارجی است؛ دست نخورد (IMAGES_REPLACE_FOREIGN=false).")
            else:
                # فقط عکس‌های واقعاً جدید چک می‌شوند؛ عکس‌های موجود روی محصول بدون چک حفظ می‌شوند
                new_urls = [u for u in dedup_urls if img_basename(u) not in ex_basename_set]
                if new_urls:
                    alive_urls, dead_urls = filter_alive_images(new_urls)
                    if dead_urls:
                        with stats['lock']: stats['dead_images'] += len(dead_urls)
                    final_urls = [u for u in dedup_urls if img_basename(u) in ex_basename_set or u in alive_urls]
                else:
                    final_urls = dedup_urls  # فقط ترتیب/ترکیب عکس‌های موجود عوض شده (بازچینش با id)
                if final_urls and img_basename_ordered(final_urls) != existing_basenames:
                    images_data, orphan_ids = build_images_payload(final_urls, existing_images)
        elif existing_wc_id is not None and existing_basenames:
            # منبع عکسی ندارد ولی محصول در WC عکس دارد؛ دست نمی‌زنیم
            pass

        wc_data = {
            "name": product.get('name', 'بدون نام'),
            "type": "simple",
            "sku": sku,
            "regular_price": process_price(product.get('price', 0)),
            "categories": [{"id": wc_cat_id}],
            "stock_quantity": product.get('stock', 0),
            "manage_stock": True,
            "stock_status": "instock" if product.get('stock', 0) > 0 else "outofstock",
            "attributes": attributes,
            "tags": smart_tags_for_product(product, cat_map) if has_details else None,
            "status": "publish"
        }
        if images_data:
            wc_data["images"] = images_data

        ok_sent, images_ok = _send_to_woocommerce(wc_data['sku'], wc_data, stats, existing_product_id=existing_wc_id)
        # پاک‌سازی پیوست‌های یتیم (فقط وقتی تصاویر واقعاً با موفقیت اعمال شدند)
        if ok_sent and images_ok and orphan_ids:
            n_media = delete_media_attachments(orphan_ids)
            if n_media:
                with stats['lock']: stats['media_deleted'] += n_media
        elif ok_sent and images_ok is False:
            with stats['lock']: stats['images_failed'] += 1
        if SENDER_SLEEP_SEC > 0:
            time.sleep(random.uniform(0, SENDER_SLEEP_SEC))
    except Exception as e:
        logger.error(f"   ❌ خطا در پردازش محصول {product.get('id','')}: {e}")
        with stats['lock']: stats['failed'] += 1

# ==============================================================================
# ابزارهای تجمیع محصول به leaf و کش و جزئیات Selective
# ==============================================================================
def condense_products_to_leaf(all_products_by_catkey, categories):
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

def light_changed(old, new):
    return (
        not old or
        str(old.get('price')) != str(new.get('price')) or
        int(old.get('stock', 0)) != int(new.get('stock', 0)) or
        old.get('category_id') != new.get('category_id')
    )

def full_changed(old, new):
    if light_changed(old, new):
        return True
    return (old or {}).get('specs') != (new or {}).get('specs')

def is_specs_stale(old):
    if not old: return True
    ts = old.get('details_ts')
    if not ts: return True
    try:
        return (time.time() - float(ts)) > REFRESH_SPECS_DAYS * 86400
    except:
        return True

def merge_specs_from_cache(products_by_pid, cached):
    for pid, p in products_by_pid.items():
        old = cached.get(pid)
        if (not p.get('specs')) and old and old.get('specs'):
            p['specs'] = old['specs']
            if old.get('details_ts'):
                p['details_ts'] = old['details_ts']

def enrich_products_with_details(session, products_by_pid, pids_to_enrich):
    q = Queue()
    for pid in pids_to_enrich:
        if pid in products_by_pid:
            q.put(pid)

    stats = {'ok': 0, 'fail': 0}
    lock = Lock()

    def worker():
        while True:
            try:
                pid = q.get_nowait()
            except Exception:
                break
            try:
                p = products_by_pid[pid]
                cat_for_detail = p.get('detail_hint_cat_id') or p.get('category_id')
                specs, canonical_id, gallery = get_product_details(session, cat_for_detail, pid)
                if canonical_id:
                    p['category_id'] = pick_deepest(p.get('category_id'), p.get('detail_hint_cat_id'), canonical_id)
                p['specs'] = specs or {}
                if gallery:
                    p['images_all'] = gallery
                elif (not p.get('images_all')) and p.get('image'):
                    p['images_all'] = [p.get('image')]
                p['images_ts'] = int(time.time())
                p['details_ts'] = int(time.time())
                with lock:
                    stats['ok'] += 1
            except Exception as e:
                logger.warning(f"   ⚠️ جزئیات محصول {pid} خطا: {e}")
                with lock:
                    stats['fail'] += 1
            finally:
                q.task_done()
                time.sleep(random.uniform(0.05, 0.2))  # کمی تنفس بین کارها

    threads = []
    for _ in range(max(1, DETAILS_CONCURRENCY)):
        t = Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    logger.info(f"✅ جزئیات تکمیلی: موفق={stats['ok']} | ناموفق={stats['fail']}")

# ==============================================================================
# تابع اصلی
# ==============================================================================
def main():
    # یادآوری: WC_API_URL باید روی https + www باشد
    if "www." not in WC_API_URL:
        logger.warning(f"⚠️ پیشنهاد: WC_API_URL را با www تنظیم کنید. مقدار فعلی: {WC_API_URL}")

    session = login_eways(EWAYS_USERNAME, EWAYS_PASSWORD)
    if not session:
        logger.error("❌ لاگین انجام نشد. پایان.")
        return

    all_cats = get_and_parse_categories(session)
    if not all_cats:
        logger.error("❌ دسته‌بندی‌ها بارگذاری نشد.")
        return
    init_category_index_global(all_cats)

    SELECTED_IDS_STRING = os.environ.get("SELECTED_IDS_STRING") or "1582:14548-allz,1584-all-allz|16777:all-allz|1583:17893-allz|4882:all-allz|16778:22570-all-allz"
    parsed_selection = parse_selected_ids_string(SELECTED_IDS_STRING)

    # انتخاب‌ها
    scrape_categories, transfer_categories = get_selected_categories_according_to_selection(parsed_selection, all_cats)

    # اطمینان از حضور والدها در انتقال
    parent_ids = [block['parent_id'] for block in parsed_selection]
    parent_cats = [cat for cat in all_cats if cat['id'] in parent_ids]
    transfer_by_id = {c['id']: c for c in transfer_categories}
    for pc in parent_cats:
        transfer_by_id.setdefault(pc['id'], pc)
    transfer_categories = list(transfer_by_id.values())

    # لاگ دسته‌ها
    scrape_list = [f"{c['id']} ({c['name']})" for c in scrape_categories]
    transfer_list = [f"{c['id']} ({c['name']})" for c in transfer_categories]
    logger.info(f"✅ دسته‌های اسکرپ: {scrape_list}")
    logger.info(f"✅ دسته‌های انتقال (با والدها): {transfer_list}")

    # ساخت دسته‌ها در ووکامرس
    category_mapping = transfer_categories_to_wc(transfer_categories)
    if not category_mapping:
        logger.error("❌ نگاشت دسته‌بندی ووکامرس ساخته نشد.")
        return

    # کش
    cached_products_raw = load_cache()
    cached_products = normalize_cache(cached_products_raw, all_cats)

    # جمع‌آوری محصولات Light
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

    logger.info("\n⏳ شروع جمع‌آوری محصولات (Light)...")
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

    # انتخاب leaf نهایی
    canonical_products = condense_products_to_leaf(all_products, all_cats)
    logger.info(f"🧭 محصولات (Light) پس از نگاشت به عمیق‌ترین زیرشاخه: {len(canonical_products)}")
    print_products_tree_by_leaf(canonical_products, transfer_categories or all_cats)

    # آمار دسته‌ای
    cat_counts = Counter(p.get('category_id') for p in canonical_products.values())
    logger.info("📊 آمار تعداد محصولات به تفکیک دسته (leaf):")
    for cid, cnt in sorted(cat_counts.items(), key=lambda kv: (-kv[1], CATEGORY_NAME.get(kv[0], '') or '')):
        logger.info(f"   - {cat_label(cid)}: {cnt}")

    # ادغام specs از کش
    merge_specs_from_cache(canonical_products, cached_products)

    # ============================
    # بررسی گپ همگام‌سازی و جزئیات
    # ============================
    logger.info("\n⛽️ بررسی گپ همگام‌سازی با ووکامرس (Light)...")
    wc_products = get_all_wc_products_with_prefixes(SKU_PREFIXES)
    wc_by_sku = {p.get('sku'): p for p in wc_products}
    wc_skus = set(wc_by_sku.keys())

    # SKUهایی که تصویر ندارند (بدون GET اضافی)
    wc_missing_image_skus = set()
    for p in wc_products:
        sku = p.get('sku') or ''
        imgs = p.get('images') or []
        if sku and len(imgs) == 0:
            wc_missing_image_skus.add(sku)

    # تغییرات سبک
    changed_light = {}
    for pid, p in canonical_products.items():
        old = cached_products.get(pid)
        if light_changed(old, p):
            changed_light[pid] = p

    def sku_candidates_for_pid(pid):
        return [f"{pref}{pid}" for pref in SKU_PREFIXES]

    # مفقود در ووکامرس
    missing_in_wc = {pid: p for pid, p in canonical_products.items() if not any(s in wc_skus for s in sku_candidates_for_pid(pid))}

    # دسته نامنطبق
    mismatch = {}
    for pid, p in canonical_products.items():
        wcp = None
        for s in sku_candidates_for_pid(pid):
            wcp = wc_by_sku.get(s)
            if wcp:
                break
        if not wcp:
            continue
        expected_wc_cat = category_mapping.get(p['category_id'])
        wc_cat_ids = {c.get('id') for c in wcp.get('categories', []) if isinstance(c, dict)}
        if expected_wc_cat and expected_wc_cat not in wc_cat_ids:
            mismatch[pid] = p
    logger.info(f"🧭 موارد با دسته نامنطبق (Light): {len(mismatch)}")

    # تعیین اقلام نیازمند جزئیات
    need_details = set(changed_light.keys()) | set(missing_in_wc.keys()) | set(mismatch.keys())
    for pid, p in canonical_products.items():
        old = cached_products.get(pid)
        if ALWAYS_DETAILS_FOR_NEW and not old:
            need_details.add(pid)
        elif not (old and old.get('specs')):
            need_details.add(pid)
        elif is_specs_stale(old):
            need_details.add(pid)

    logger.info(f"🔎 اقلام نیازمند دریافت جزئیات: {len(need_details)}")
    if need_details:
        enrich_products_with_details(session, canonical_products, need_details)

    # تکمیل تدریجی گالری در کش برای محصولات موجودی که هنوز گالری ندارند (پخش‌شده)
    if GALLERY_BACKFILL:
        wc_pids = set()
        for wcp in wc_products:
            s = (wcp.get('sku') or '')
            for pref in SKU_PREFIXES:
                if s.startswith(pref):
                    wc_pids.add(s[len(pref):])
                    break
        missing_gallery = [pid for pid in canonical_products.keys()
                           if pid in wc_pids and pid not in need_details
                           and not canonical_products[pid].get('images_all')]
        if MAX_IMAGE_UPDATES_PER_RUN:
            missing_gallery = missing_gallery[:MAX_IMAGE_UPDATES_PER_RUN]
        if missing_gallery:
            logger.info(f"🖼️ تکمیل گالری در کش برای {len(missing_gallery)} محصول (پخش‌شده در چند اجرا)...")
            enrich_products_with_details(session, canonical_products, set(missing_gallery))
        elif wc_pids:
            logger.info("🖼️ گالری همه محصولات موجود در کش تکمیل شده است.")

    # ذخیره کش به‌روز
    updated_cache = {}
    for pid, p in canonical_products.items():
        base = dict(p)
        old = cached_products.get(pid)
        if not base.get('specs') and old and old.get('specs'):
            base['specs'] = old['specs']
            if old.get('details_ts'):
                base['details_ts'] = old['details_ts']
        updated_cache[pid] = base
    save_cache(updated_cache)

    # ============================
    # اقلام ارسالی به ووکامرس
    # ============================
    to_send_items = {}
    for pid, p in canonical_products.items():
        old = cached_products.get(pid)
        if full_changed(old, p):
            to_send_items[pid] = p
            continue
        if not any(s in wc_skus for s in sku_candidates_for_pid(pid)):
            to_send_items[pid] = p
            continue
        wcp = None
        for s in sku_candidates_for_pid(pid):
            wcp = wc_by_sku.get(s)
            if wcp:
                break
        if wcp:
            expected_wc_cat = category_mapping.get(p['category_id'])
            wc_cat_ids = {c.get('id') for c in wcp.get('categories', []) if isinstance(c, dict)}
            if expected_wc_cat and expected_wc_cat not in wc_cat_ids:
                to_send_items[pid] = p

    send_counts = Counter(p['category_id'] for p in to_send_items.values())
    logger.info("🛰️ اقلام ارسالی به ووکامرس به تفکیک دسته:")
    for cid, cnt in sorted(send_counts.items(), key=lambda kv: (-kv[1], CATEGORY_NAME.get(kv[0], '') or '')):
        logger.info(f"   - {cat_label(cid)}: {cnt}")

    send_count = len(to_send_items)
    logger.info(f"\n🚀 شروع پردازش و ارسال {send_count} قلم به ووکامرس...")

    # ——— ارسال ———
    stats = {'created': 0, 'updated': 0, 'failed': 0, 'no_category': 0, 'outofstock_updated': 0,
             'deleted': 0, 'media_deleted': 0, 'dead_images': 0, 'images_failed': 0, 'lock': Lock()}

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
            process_product_wrapper((product, stats, category_mapping, cat_map, wc_by_sku, wc_missing_image_skus))
            product_queue.task_done()

    threads = []
    for _ in range(WC_SENDER_WORKERS):
        t = Thread(target=worker_sender)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    # ——— به‌روزرسانی تدریجی گالری محصولات بدون تغییر (پخش‌شده) ———
    if GALLERY_BACKFILL:
        logger.info("\n🖼️ بررسی گالری محصولات بدون تغییر ...")
        backfill_updated = 0
        backfill_cap = MAX_IMAGE_UPDATES_PER_RUN
        for pid, p in canonical_products.items():
            if pid in to_send_items:
                continue
            if backfill_cap and backfill_updated >= backfill_cap:
                logger.info(f"⏸️ سقف {backfill_cap} به‌روزرسانی گالری در این اجرا پر شد؛ بقیه در اجراهای بعدی.")
                break
            urls = trim_gallery(p.get('images_all') or [])
            if not urls:
                continue
            wcp = None
            for s in sku_candidates_for_pid(pid):
                wcp = wc_by_sku.get(s)
                if wcp:
                    break
            if not wcp:
                continue
            existing_images = wcp.get('images') or []
            existing_basenames = wc_image_basenames(existing_images)
            ex_set = set(existing_basenames)
            dedup_urls = dedupe_by_basename(urls)
            if img_basename_ordered(dedup_urls) == existing_basenames:
                continue  # بدون تغییر — هیچ درخواست شبکه‌ای
            # فقط عکس‌های جدید چک می‌شوند؛ عکس‌های موجود روی محصول بدون چک حفظ می‌شوند
            new_urls = [u for u in dedup_urls if img_basename(u) not in ex_set]
            if new_urls:
                alive_urls, dead_urls = filter_alive_images(new_urls)
                if dead_urls:
                    with stats['lock']: stats['dead_images'] += len(dead_urls)
                final_urls = [u for u in dedup_urls if img_basename(u) in ex_set or u in alive_urls]
            else:
                final_urls = dedup_urls
            if not final_urls:
                continue
            final_basenames = img_basename_ordered(final_urls)
            if final_basenames == existing_basenames:
                continue
            if (not IMAGES_REPLACE_FOREIGN) and existing_basenames \
                    and (not set(existing_basenames).issubset(set(final_basenames))):
                continue
            payload, orphans = build_images_payload(final_urls, existing_images)
            if not payload:
                continue
            try:
                res = wc_request("put", f"/products/{wcp['id']}", json={"images": payload}, timeout=60)
                res.raise_for_status()
                backfill_updated += 1
                if orphans:
                    n_media = delete_media_attachments(orphans)
                    if n_media:
                        with stats['lock']: stats['media_deleted'] += n_media
                logger.info(f"   🖼️ گالری {pid} به‌روز شد ({len(payload)} عکس).")
            except Exception as e:
                logger.warning(f"   ⚠️ به‌روزرسانی گالری {pid} ناموفق: {e}")
        logger.info(f"✅ گالری به‌روزرسانی‌شده: {backfill_updated}")

    # ——— ناموجودها و حذف ———
    logger.info("\n🚧 بررسی ناموجودها و حذف ...")
    extracted_skus = set()
    extracted_pids = set(canonical_products.keys())
    for pid in canonical_products.keys():
        extracted_skus.update(sku_candidates_for_pid(pid))

    # لیست ناموجودهای تأییدشده از منبع (Available=0) برای همه دسته‌های انتخابی
    unavailable_by_cat = {}
    selected_cat_ids = [cat['id'] for cat in scrape_categories]
    for cid in selected_cat_ids:
        ok_unav, unav_map = get_unavailable_products(session, cid)
        if ok_unav:
            unavailable_by_cat[cid] = unav_map
    total_unav = sum(len(v) for v in unavailable_by_cat.values())
    logger.info(f"✅ ناموجودهای تأییدشده منبع: {total_unav} قلم در {len(unavailable_by_cat)}/{len(selected_cat_ids)} دسته")

    confirm_state = load_confirm_state()
    to_oos_ids = set()
    delete_queue = []

    for wcp in wc_products:
        sku = (wcp.get('sku') or '')
        pid_str = None
        for pref in SKU_PREFIXES:
            if sku.startswith(pref):
                pid_str = sku[len(pref):]
                break
        if not pid_str:
            continue
        if pid_str in extracted_pids:
            # محصول در منبع موجود است؛ شمارنده تأیید پاک می‌شود
            if pid_str in confirm_state:
                del confirm_state[pid_str]
            continue
        cached = cached_products.get(pid_str) or {}
        cat_id = cached.get('category_id')
        unav_map = unavailable_by_cat.get(cat_id)
        confirmed = False
        if cat_id is not None and cat_id in unavailable_by_cat:
            if str(pid_str) in unav_map:
                confirmed = True  # ناموجود تأییدشده از منبع
            elif DELETE_ABSENT_FROM_SOURCE:
                confirmed = True  # نه موجود نه ناموجود → از پنل ایویز حذف شده
        if UNAVAILABLE_MODE == "delete" and confirmed:
            entry = confirm_state.get(pid_str) or {
                "count": 0, "category_id": cat_id,
                "name": cached.get('name', ''), "wc_id": wcp.get('id'),
            }
            entry["count"] = int(entry.get("count", 0)) + 1
            entry["wc_id"] = wcp.get('id')
            confirm_state[pid_str] = entry
            if entry["count"] >= UNAVAILABLE_CONFIRM_RUNS:
                delete_queue.append((wcp, pid_str))
            else:
                logger.info(f"⏳ {pid_str} ناموجود/حذف‌شده تأیید شد ({entry['count']}/{UNAVAILABLE_CONFIRM_RUNS})؛ حذف در اجرای بعدی")
                # در همین اجرا ناموجود شود تا فروش متوقف بماند
                if wcp.get('stock_status') != 'outofstock':
                    to_oos_ids.add(wcp['id'])
        else:
            # رفتار قبلی: فقط ناموجود کردن (خطای شبکه منبع، تغییر انتخاب دسته‌ها یا حالت outofstock)
            if wcp.get('stock_status') != 'outofstock':
                to_oos_ids.add(wcp['id'])

    # پاک‌سازی کش محصولات تأییدشده‌ای که در ووکامرس هم نیستند
    for pid_str in list(confirm_state.keys()):
        if pid_str not in extracted_pids and pid_str not in [d[1] for d in delete_queue]:
            cached = cached_products.get(pid_str)
            if cached is not None:
                cat_id = cached.get('category_id')
                unav_map = unavailable_by_cat.get(cat_id)
                if cat_id is not None and cat_id in unavailable_by_cat \
                        and (str(pid_str) in unav_map or DELETE_ABSENT_FROM_SOURCE):
                    cached_products.pop(pid_str, None)
                    updated_cache.pop(pid_str, None)
                    confirm_state.pop(pid_str, None)

    # حذف قطعی محصولات تأییدشده (عکس‌ها + محصول) با چند نخ
    if delete_queue:
        logger.info(f"🗑️ حذف {len(delete_queue)} محصول تأییدشده از سایت (با پاک‌سازی عکس‌ها) ...")
        delete_lock = Lock()

        def delete_worker():
            while True:
                try:
                    wcp, pid_str = delete_queue_q.get_nowait()
                except Exception:
                    break
                try:
                    media_ids = [img.get('id') for img in (wcp.get('images') or []) if img.get('id')]
                    n_media = delete_media_attachments(media_ids)
                    res = wc_request("delete", f"/products/{wcp['id']}", params={"force": "true"}, timeout=40)
                    if res.status_code in (200, 201, 404):
                        with delete_lock:
                            stats['deleted'] += 1
                            stats['media_deleted'] += n_media
                            cached_products.pop(pid_str, None)
                            updated_cache.pop(pid_str, None)
                            confirm_state.pop(pid_str, None)
                        logger.info(f"   🗑️ محصول {pid_str} (WC ID {wcp['id']}) حذف شد (+{n_media} فایل تصویر پاک شد).")
                    else:
                        logger.error(f"   ❌ حذف {pid_str} ناموفق: {res.status_code} - {res.text[:150]}")
                except Exception as e:
                    logger.error(f"   ❌ خطا در حذف {pid_str}: {e}")
                if OUTOFSTOCK_SLEEP_SEC > 0:
                    time.sleep(random.uniform(0, OUTOFSTOCK_SLEEP_SEC))
                delete_queue_q.task_done()

        delete_queue_q = Queue()
        for item in delete_queue:
            delete_queue_q.put(item)
        del_threads = []
        for _ in range(max(1, OUTOFSTOCK_WORKERS)):
            t = Thread(target=delete_worker)
            t.start()
            del_threads.append(t)
        for t in del_threads:
            t.join()

    save_confirm_state(confirm_state)

    # Batch اول
    failed_ids_after_batch = set()
    if to_oos_ids:
        logger.info(f"🚧 ناموجود کردن Batch: {len(to_oos_ids)} قلم در بچ‌های {BATCH_SIZE_OUTOFSTOCK}تایی ...")
        for group in chunked(sorted(to_oos_ids), BATCH_SIZE_OUTOFSTOCK):
            try:
                ok, failed_list = mark_outofstock_batch(group)
                if not ok:
                    failed_ids_after_batch.update(group)
                else:
                    failed_ids_after_batch.update(failed_list)
                # تنفس کوچک بین بچ‌ها
                if OUTOFSTOCK_SLEEP_SEC > 0:
                    time.sleep(random.uniform(0, OUTOFSTOCK_SLEEP_SEC))
            except Exception as e:
                logger.error(f"   ❌ خطا در Batch ناموجودها برای گروه {group[:3]}... : {e}")
                failed_ids_after_batch.update(group)

    # Fallback تکی با retry
    if failed_ids_after_batch:
        logger.info(f"🔁 تلاش تکی برای {len(failed_ids_after_batch)} قلم که در Batch ناموفق بودند...")
        outofstock_queue = Queue()
        for pid in failed_ids_after_batch:
            outofstock_queue.put(pid)

        def outofstock_worker():
            while True:
                try:
                    product_id = outofstock_queue.get_nowait()
                except Exception:
                    break
                try:
                    update_to_outofstock_single(product_id)
                    logger.info(f"   ✅ محصول {product_id} ناموجود شد.")
                    with stats['lock']: stats['outofstock_updated'] += 1
                except Exception as e:
                    logger.error(f"   ❌ بعد از چند تلاش، ناموجود کردن {product_id} ناموفق: {e}")
                    with stats['lock']: stats['failed'] += 1
                if OUTOFSTOCK_SLEEP_SEC > 0:
                    time.sleep(random.uniform(0, OUTOFSTOCK_SLEEP_SEC))
                outofstock_queue.task_done()

        out_threads = []
        for _ in range(OUTOFSTOCK_WORKERS):
            t = Thread(target=outofstock_worker)
            t.start()
            out_threads.append(t)
        for t in out_threads:
            t.join()

    # ذخیره نهایی کش و وضعیت تأیید (بعد از حذف‌ها)
    save_cache(updated_cache)

    logger.info("\n===============================")
    logger.info(f"📦 موجود (ارسال‌شده): {send_count}")
    logger.info(f"🟢 ایجاد شده: {stats['created']}")
    logger.info(f"🔵 آپدیت شده: {stats['updated']}")
    logger.info(f"🟠 به ناموجود: {stats['outofstock_updated']}")
    logger.info(f"🗑️ حذف شده از سایت: {stats['deleted']}")
    logger.info(f"🧹 فایل رسانه حذف‌شده: {stats['media_deleted']}")
    logger.info(f"🖼️ عکس مرده فیلترشده: {stats['dead_images']}")
    logger.info(f"⚠️ شکست ارسال تصویر: {stats['images_failed']}")
    logger.info(f"🔴 شکست: {stats['failed']}")
    logger.info(f"🟡 بدون دسته: {stats.get('no_category', 0)}")
    logger.info("===============================\nتمام!")

if __name__ == "__main__":
    main()
