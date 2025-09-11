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

WC_API_URL = os.environ.get("WC_API_URL") or "https://www.pakhshemobile.ir/wp-json/wc/v3"
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
    candidates = [c for c in cat_ids if c is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda c: CATEGORY_DEPTH.get(c, 0))

def abs_url(u):
    if not u:
        return u
    return u if str(u).startswith('http') else urljoin(BASE_URL, u)

def extract_ids_from_href(href):
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
# Parser فقط-کاما برای SELECTED_IDS_STRING + لاگ دیباگ
# ==============================================================================
def parse_selected_ids_string(selected_ids_string):
    """
    فقط کاما به‌عنوان جداکننده پشتیبانی می‌شود (هم بیرون و هم داخل پرانتز).
    - pid:all | pid:allz | pid:all-allz
    - pid:sub-allz,sub-all-allz
    - pid:sub1(child1-allz,child2-all-allz),sub2(child3-allz,...)
    - آیدی تنها → all_subcats_and_products
    - بالانس خودکار پرانتز
    """

    def normalize_selection_string(s):
        # نرمال‌سازی علائم فارسی/غیراستاندارد به ASCII
        replacements = {
            '؛': ',',  # سمی‌کالن فارسی → کاما
            '،': ',',  # کاما فارسی → کاما
            ';': ',',  # اگر اشتباها ; باشد → کاما
            'ـ': '-',  # کشیده → خط تیره
            '–': '-', '—': '-', '−': '-',  # انواع dash
            '\u200c': '', '\u200f': '', '\ufeff': ''  # ZWNJ, RLM, BOM حذف
        }
        for a, b in replacements.items():
            s = s.replace(a, b)
        # حذف کوتیشن انتهایی اشتباهی
        s = s.strip().strip('"').strip("'").strip()
        return s

    def normalize_token(t):
        return (t or '').strip().strip('"').strip("'").strip()

    def balance_parens(text):
        open_c = text.count('(')
        close_c = text.count(')')
        if close_c < open_c:
            text = text + (')' * (open_c - close_c))
        return text

    def split_blocks_resilient(s):
        # بلاک‌ها را با | یا ¦ جدا می‌کنیم؛ فقط خارج از پرانتز
        parts, buf, depth = [], [], 0
        for ch in s:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth = max(0, depth - 1)
            if (ch in ('|', '¦')) and depth == 0:
                part = ''.join(buf).strip()
                if part:
                    parts.append(part)
                buf = []
            else:
                buf.append(ch)
        tail = ''.join(buf).strip()
        if tail:
            parts.append(tail)
        return parts

    def split_top_level_commas(s):
        # فقط کاما، با درنظرگرفتن عمق پرانتز
        parts, buf, depth = [], [], 0
        for ch in s:
            if ch == '(':
                depth += 1
                buf.append(ch)
            elif ch == ')':
                depth = max(0, depth - 1)
                buf.append(ch)
            elif ch == ',' and depth == 0:
                part = ''.join(buf).strip()
                if part:
                    parts.append(part)
                buf = []
            else:
                buf.append(ch)
        tail = ''.join(buf).strip()
        if tail:
            parts.append(tail)
        return parts

    def parse_token(token, default_parent_id):
        out = []
        tok = normalize_token(token)
        if not tok:
            return out

        # گروه با پرانتز کامل یا ناقص: 2389(...), 2390(...
        m = re.match(r'^(\d+)KATEX_INLINE_OPEN(.*)KATEX_INLINE_CLOSE$', tok)
        if not m:
            m = re.match(r'^(\d+)KATEX_INLINE_OPEN(.*)$', tok)
        if m:
            group_id = int(m.group(1))
            inner = m.group(2).strip()
            inner_tokens = split_top_level_commas(inner)
            for it in inner_tokens:
                out.extend(parse_token(it, default_parent_id=group_id))
            return out

        key = tok.lower()
        if key == 'all':
            out.append({"id": int(default_parent_id), "type": "all_subcats"})
            return out
        if key == 'allz':
            out.append({"id": int(default_parent_id), "type": "only_products"})
            return out
        if key in ('all-allz', 'all+allz', 'all_allz'):
            out.append({"id": int(default_parent_id), "type": "all_subcats_and_products"})
            return out

        m2 = re.match(r'^(\d+)-(all|allz|all-allz)$', key)
        if m2:
            sid = int(m2.group(1))
            typ_key = m2.group(2)
            typ = 'all_subcats' if typ_key == 'all' else ('only_products' if typ_key == 'allz' else 'all_subcats_and_products')
            out.append({"id": sid, "type": typ})
            return out

        if tok.isdigit():
            out.append({"id": int(tok), "type": "all_subcats_and_products"})
            return out

        return out

    s = normalize_selection_string(selected_ids_string or "")
    if not s:
        return []

    # دیباگ: لاگ رشته خام پس از نرمال‌سازی
    logger.info(f"🎯 SELECTED_IDS_STRING = {s}")

    result = []
    blocks = split_blocks_resilient(s)  # بلاک‌ها با | جدا
    logger.info(f"🧱 Blocks detected: {blocks}")

    for block in blocks:
        block = normalize_token(block)
        if not block or ':' not in block:
            continue
        parent_id_str, children_str = block.split(':', 1)
        children_str = balance_parens(children_str)

        try:
            parent_id = int(re.sub(r'\D', '', parent_id_str))
        except:
            continue

        top_tokens = split_top_level_commas(children_str)
        logger.info(f"🔹 Block {parent_id}: tokens = {top_tokens}")

        selections = []
        for tok in top_tokens:
            selections.extend(parse_token(tok, default_parent_id=parent_id))

        if selections:
            result.append({"parent_id": parent_id, "selections": selections})
        else:
            logger.warning(f"⚠️ هیچ انتخاب معتبری برای بلاک '{block}' پیدا نشد.")

    # خلاصه برای دیباگ
    try:
        debug_simple = []
        for blk in result:
            debug_simple.append({
                "parent": blk['parent_id'],
                "sel": [(s['id'], s['type']) for s in blk.get('selections', [])]
            })
        logger.info(f"🧩 Parsed selection: {debug_simple}")
    except Exception:
        pass

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
    """
    - انواع: 'all_subcats' | 'only_products' | 'all_subcats_and_products'
    - والدهای تمام دسته‌های انتخاب‌شده به transfer اضافه می‌شوند تا سلسله‌مراتب کامل باشد.
    """
    selected_scrape = set()
    selected_transfer = set()

    def add_direct_subs(cid):
        for sub in get_direct_subcategories(cid, all_cats):
            selected_scrape.add(sub)
            selected_transfer.add(sub)

    def add_all_subs(cid):
        selected_scrape.add(cid); selected_transfer.add(cid)
        for sub in get_all_subcategories(cid, all_cats):
            selected_scrape.add(sub); selected_transfer.add(sub)

    for block in parsed_selection:
        parent_id = block['parent_id']
        for sel in block.get('selections', []):
            typ, sid = sel['type'], sel['id']

            if sid == parent_id:
                if typ == 'all_subcats':
                    add_direct_subs(parent_id)
                elif typ == 'only_products':
                    selected_scrape.add(parent_id); selected_transfer.add(parent_id)
                elif typ == 'all_subcats_and_products':
                    add_all_subs(parent_id)
                continue

            if typ == 'only_products':
                selected_scrape.add(sid); selected_transfer.add(sid)
            elif typ == 'all_subcats_and_products':
                add_all_subs(sid)
            elif typ == 'all_subcats':
                add_direct_subs(sid)

    # افزودن تمام والدها تا ریشه برای transfer
    parent_map = {c['id']: c.get('parent_id') for c in all_cats}
    for cid in list(selected_transfer):
        p = parent_map.get(cid)
        while p:
            selected_transfer.add(p)
            p = parent_map.get(p)

    scrape_categories = [cat for cat in all_cats if cat['id'] in selected_scrape]
    transfer_categories = [cat for cat in all_cats if cat['id'] in selected_transfer]
    return scrape_categories, transfer_categories

# ==============================================================================
# لاگین به eways
# ==============================================================================
def login_eways(username, password):
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f"{BASE_URL}/",
        'X-Requested-With': 'XMLHttpRequest',
        'Accept-Language': 'en-US,en;q=0.9,fa;q=0.8'
    })
    # eways SSL مشکل CA دارد؛ همین را نگه می‌داریم
    session.verify = False
    logger.info("⏳ در حال لاگین به پنل eways ...")
    resp = session.post(f"{BASE_URL}/User/Login",
                        data={"UserName": username, "Password": password, "RememberMe": "true"},
                        timeout=30)
    if resp.status_code != 200:
        logger.error(f"❌ لاگین ناموفق! کد وضعیت: {resp.status_code} - متن پاسخ: {resp.text[:200]}")
        return None
    if 'Aut' in session.cookies:
        logger.info("✅ لاگین موفق! کوکی Aut دریافت شد.")
        return session
    logger.error("❌ کوکی Aut دریافت نشد.")
    return None

# ==============================================================================
# دریافت دسته‌ها از eways (مقاوم: تلاش JSON، سپس HTML)
# ==============================================================================
def get_and_parse_categories(session):
    logger.info(f"⏳ دریافت دسته‌بندی‌ها از: {SOURCE_CATS_API_URL}")
    try:
        response = session.get(SOURCE_CATS_API_URL, timeout=30)
        response.raise_for_status()

        def try_json_anyway(resp):
            t = (resp.text or "").strip().lstrip('\ufeff')
            try:
                return resp.json()
            except Exception:
                pass
            try:
                return json.loads(t)
            except Exception:
                pass
            start_candidates = [t.find('['), t.find('{')]
            start_candidates = [i for i in start_candidates if i != -1]
            start = min(start_candidates) if start_candidates else -1
            end = max(t.rfind(']'), t.rfind('}'))
            if start != -1 and end != -1 and end > start:
                snippet = t[start:end+1]
                try:
                    return json.loads(snippet)
                except Exception:
                    return None
            return None

        data = try_json_anyway(response)
        if isinstance(data, list) and data:
            logger.info("✅ پاسخ JSON (یا شبه-JSON) دریافت شد. در حال نگاشت والد-فرزند...")
            id_map = {}
            for c in data:
                real_id_match = re.search(r'/Store/List/(\d+)', (c.get('url') or ''))
                real_id = int(real_id_match.group(1)) if real_id_match else c.get('id')
                if c.get('id') is not None and real_id is not None:
                    id_map[c['id']] = real_id
            final_cats = []
            for c in data:
                src_id = c.get('id')
                real_id = id_map.get(src_id, None if src_id is None else src_id)
                if real_id is None:
                    continue
                parent_src = c.get('parent_id')
                parent_real = id_map.get(parent_src) if parent_src is not None else None
                final_cats.append({"id": int(real_id), "name": (c.get('name') or '').strip(), "parent_id": parent_real})
            logger.info(f"✅ {len(final_cats)} دسته‌بندی از JSON.")
            return final_cats

        logger.warning("⚠️ پاسخ JSON قابل‌استفاده نبود. تلاش برای پارس HTML منو...")
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
        logger.info(f"✅ {len(final_cats)} دسته‌بندی معتبر از HTML منو.")
        return final_cats
    except requests.RequestException as e:
        logger.error(f"❌ خطا در دریافت دسته‌بندی‌ها: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ خطای ناشناخته در پردازش دسته‌بندی‌ها: {e}")
        return None

# ==============================================================================
# تکمیل دسته‌های گمشده با خواندن breadcrumb صفحه هر دسته
# ==============================================================================
def fetch_category_breadcrumb(session, category_id):
    def try_fetch(url):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.text
        except Exception:
            return None

    cid = int(category_id)
    html = try_fetch(f"{BASE_URL}/Store/List/{cid}")
    if not html:
        html = try_fetch(f"{BASE_URL}/Store/List/{cid}/2/2/0/0/0/10000000000")
    if not html:
        logger.debug(f"⚠️ نتوانستم صفحه دسته {cid} را بگیرم.")
        return [(cid, f"CAT-{cid}")]

    soup = BeautifulSoup(html, 'lxml')
    anchors = []
    selectors = [
        'nav[aria-label="breadcrumb"] a[href*="/Store/List/"]',
        'ul.breadcrumb a[href*="/Store/List/"]',
        'ol.breadcrumb a[href*="/Store/List/"]',
        '.breadcrumb a[href*="/Store/List/"]'
    ]
    for sel in selectors:
        anchors = soup.select(sel)
        if anchors:
            break
    crumbs = []
    seen = set()
    for a in anchors:
        href = a.get('href', '')
        m = re.search(r'/Store/List/(\d+)', href)
        if m:
            ccid = int(m.group(1))
            if ccid in seen:
                continue
            seen.add(ccid)
            name = a.get_text(strip=True)
            crumbs.append((ccid, name))
    if not any(ccid == cid for ccid, _ in crumbs):
        last_label = None
        last_sel = [
            'nav[aria-label="breadcrumb"] li.active',
            '.breadcrumb li.active',
            '.breadcrumb .active'
        ]
        for sel in last_sel:
            node = soup.select_one(sel)
            if node:
                last_label = node.get_text(strip=True)
                break
        crumbs.append((cid, last_label or f"CAT-{cid}"))
    return crumbs

def collect_selected_ids(parsed_selection):
    ids = set()
    for block in parsed_selection:
        for sel in block.get('selections', []):
            try:
                ids.add(int(sel['id']))
            except:
                pass
    return ids

def ensure_categories_present(session, all_cats, desired_ids):
    by_id = {c['id']: dict(c) for c in (all_cats or [])}
    known = set(by_id.keys())
    missing = [cid for cid in desired_ids if cid not in known]
    if missing:
        logger.info(f"🔎 {len(missing)} دسته انتخاب‌شده در لیست اولیه نبود: {missing} → تلاش برای تکمیل با breadcrumb ...")
    for cid in missing:
        crumbs = fetch_category_breadcrumb(session, cid)
        prev = None
        for (ccid, cname) in crumbs:
            parent_id = prev[0] if prev else None
            if ccid not in by_id:
                by_id[ccid] = {"id": ccid, "name": cname or f"CAT-{ccid}", "parent_id": parent_id}
            else:
                if not by_id[ccid].get('name') and cname:
                    by_id[ccid]['name'] = cname
                if by_id[ccid].get('parent_id') is None and parent_id is not None:
                    by_id[ccid]['parent_id'] = parent_id
            prev = (ccid, cname)
    if missing:
        logger.info(f"✅ دسته‌های گمشده اضافه شد. کل دسته‌ها: {len(by_id)}")
    return list(by_id.values())

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

        return specs, canonical_cat_id
    except requests.exceptions.RequestException as e:
        logger.warning(f"      - خطا در دریافت جزئیات محصول {product_id}: {e}. Retry...")
        raise
    except Exception as e:
        logger.warning(f"      - خطا در استخراج مشخصات محصول {product_id}: {e}")
        return {}, None

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
                            c, _ = extract_ids_from_href(u)
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
# رَپر ووکامرس (Query Auth + Session + SSL verify)
# ==============================================================================
wc_session = requests.Session()
wc_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36'
})
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
    elif price_value <= 500000: new_price = price_value + 230000
    elif price_value <= 7000000: new_price = price_value + 260000
    elif price_value <= 10000000: new_price = price_value * 1.035
    elif price_value <= 20000000: new_price = price_value * 1.025
    elif price_value <= 30000000: new_price = price_value * 1.02
    else: new_price = price_value * 1.015
    return str(int(round(new_price, -4)))

# ==============================================================================
# ارسال/آپدیت ووکامرس با هندلینگ SKU تکراری و تصاویر مشروط
# ==============================================================================
@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.HTTPError)),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True
)
def _send_to_woocommerce(sku, data, stats, existing_product_id=None):
    try:
        if existing_product_id:
            update_data = {
                "regular_price": data["regular_price"],
                "stock_quantity": data["stock_quantity"],
                "stock_status": data["stock_status"],
                "categories": data.get("categories", []),
            }
            if data.get("attributes") is not None:
                update_data["attributes"] = data["attributes"]
            if data.get("tags") is not None:
                update_data["tags"] = data["tags"]
            # تصاویر فقط اگر عمداً گذاشته شده باشد
            if data.get("images"):
                update_data["images"] = data["images"]
            if MIGRATE_REMOTE_SKU_TO_CANONICAL:
                update_data["sku"] = data["sku"]

            res = wc_request("put", f"/products/{existing_product_id}", json=update_data, timeout=40)
            res.raise_for_status()
            with stats['lock']: stats['updated'] += 1
        else:
            # ساخت: ترجیحاً با جزئیات؛ اگر نداریم و اجازه false است، رد
            if (data.get("attributes") is None) and (not CREATE_WITHOUT_DETAILS):
                logger.warning(f"   ⚠️ ساخت {sku} رد شد؛ جزئیات نداریم و CREATE_WITHOUT_DETAILS=false است.")
                with stats['lock']: stats['failed'] += 1
                return
            try:
                res = wc_request("post", "/products", json=data, timeout=40)
                res.raise_for_status()
                with stats['lock']: stats['created'] += 1
            except requests.exceptions.HTTPError as e:
                try:
                    payload = e.response.json()
                except Exception:
                    payload = {}
                code = (payload or {}).get("code")
                resource_id = (payload or {}).get("data", {}).get("resource_id")
                if code in ("product_invalid_sku", "woocommerce_product_sku_already_exists") and resource_id:
                    logger.info(f"   🔄 SKU تکراری برای {sku}؛ آپدیت روی resource_id={resource_id}")
                    update_data = {
                        "regular_price": data["regular_price"],
                        "stock_quantity": data["stock_quantity"],
                        "stock_status": data["stock_status"],
                        "categories": data.get("categories", []),
                    }
                    if data.get("attributes") is not None:
                        update_data["attributes"] = data["attributes"]
                    if data.get("tags") is not None:
                        update_data["tags"] = data["tags"]
                    if data.get("images"):
                        update_data["images"] = data["images"]
                    if MIGRATE_REMOTE_SKU_TO_CANONICAL:
                        update_data["sku"] = data["sku"]
                    res2 = wc_request("put", f"/products/{resource_id}", json=update_data, timeout=40)
                    res2.raise_for_status()
                    with stats['lock']: stats['updated'] += 1
                else:
                    logger.error(f"   ❌ HTTP خطا برای {sku}: {e.response.status_code} - {e.response.text[:300]}")
                    raise
    except requests.exceptions.HTTPError as e:
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
    succeeded = set()
    for item in (data.get("update") or []):
        pid = item.get("id")
        if pid:
            succeeded.add(int(pid))
    failed = [int(pid) for pid in ids if int(pid) not in succeeded]
    return True, failed

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

        existing_wc_id = None
        candidate_skus = [f"{pref}{pid_str}" for pref in SKU_PREFIXES]
        for s in candidate_skus:
            wcp = wc_by_sku.get(s)
            if wcp:
                existing_wc_id = wcp.get('id')
                break

        if not existing_wc_id and ALT_SKU_LOOKUP:
            alt_id, alt_sku = find_wc_product_id_by_possible_skus(pid_str)
            if alt_id:
                logger.info(f"🔎 محصول یافت شد با SKU جایگزین: {alt_sku} → ID={alt_id} (آپدیت به‌جای ساخت)")
                existing_wc_id = alt_id

        include_images = (existing_wc_id is None) or any(s in wc_missing_image_skus for s in candidate_skus)

        images_data = None
        if include_images and product.get("image"):
            images_data = [{"src": abs_url(product.get("image"))}]

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

        _send_to_woocommerce(wc_data['sku'], wc_data, stats, existing_product_id=existing_wc_id)
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
                specs, canonical_id = get_product_details(session, cat_for_detail, pid)
                if canonical_id:
                    p['category_id'] = pick_deepest(p.get('category_id'), p.get('detail_hint_cat_id'), canonical_id)
                p['specs'] = specs or {}
                p['details_ts'] = int(time.time())
                with lock:
                    stats['ok'] += 1
            except Exception as e:
                logger.warning(f"   ⚠️ جزئیات محصول {pid} خطا: {e}")
                with lock:
                    stats['fail'] += 1
            finally:
                q.task_done()
                time.sleep(random.uniform(0.05, 0.2))

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

    # خواندن رشته انتخاب (فقط کاما). برای دور زدن مشکلات شل، می‌توانید از فایل استفاده کنید.
    selected_ids_file = os.environ.get("SELECTED_IDS_FILE")
    if selected_ids_file and os.path.exists(selected_ids_file):
        with open(selected_ids_file, 'r', encoding='utf-8') as f:
            SELECTED_IDS_STRING = f.read().strip()
    else:
        SELECTED_IDS_STRING = os.environ.get("SELECTED_IDS_STRING") or \
            "1582:14548-allz,1584-all-allz|16777:all-allz|1583:17893-allz|4882:all-allz|16778:22570-all-allz|1593:2389(13203-allz,12896-allz),2390(16711-allz,16712-allz,16710-allz)"

    parsed_selection = parse_selected_ids_string(SELECTED_IDS_STRING)

    # اطمینان از حضور دسته‌های انتخابی (اگر در all_cats نبودند، از breadcrumb تزریق کن)
    desired_ids = collect_selected_ids(parsed_selection)
    logger.info(f"🎯 Desired IDs (from selection): {sorted(desired_ids)}")
    all_cats = ensure_categories_present(session, all_cats, desired_ids)
    init_category_index_global(all_cats)

    # انتخاب دسته‌ها
    scrape_categories, transfer_categories = get_selected_categories_according_to_selection(parsed_selection, all_cats)

    # اطمینان اضافی: والدهای بلاک‌ها هم در transfer باشند
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

    # SKUهایی که تصویر ندارند
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
            process_product_wrapper((product, stats, category_mapping, cat_map, wc_by_sku, wc_missing_image_skus))
            product_queue.task_done()

    threads = []
    for _ in range(WC_SENDER_WORKERS):
        t = Thread(target=worker_sender)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    # ——— ناموجودها ———
    logger.info("\n🚧 آپدیت ناموجودها ...")
    extracted_skus = set()
    for pid in canonical_products.keys():
        extracted_skus.update(sku_candidates_for_pid(pid))

    to_oos_ids = set()
    # از کش قبلی
    for pid in cached_products.keys():
        pid_str = str(pid)
        if not any(f"{pref}{pid_str}" in extracted_skus for pref in SKU_PREFIXES):
            found_id = None
            for s in sku_candidates_for_pid(pid_str):
                wcp = wc_by_sku.get(s)
                if wcp and wcp.get('stock_status') != "outofstock":
                    found_id = wcp['id']; break
            if found_id:
                to_oos_ids.add(found_id)

    # از ووکامرس
    for wcp in wc_products:
        sku = wcp.get('sku')
        if sku not in extracted_skus and wcp.get('stock_status') != "outofstock":
            to_oos_ids.add(wcp['id'])

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
