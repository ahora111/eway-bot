import requests
import urllib3
import os
import re
import time
import sys
import random
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://panel.eways.co"
AUT_COOKIE_VALUE = os.environ.get("EWAYS_AUTH_TOKEN")
SOURCE_CATS_API_URL = f"{BASE_URL}/Store/GetCategories"
CATEGORY_PAGE_URL_TEMPLATE = f"{BASE_URL}/store/categorylist/{{cat_id}}/"
PRODUCT_LIST_URL_TEMPLATE = f"{BASE_URL}/Store/List/{{category_id}}/2/2/0/0/0/10000000000?page={{page}}"

def get_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f"{BASE_URL}/",
        'X-Requested-With': 'XMLHttpRequest'
    })
    if AUT_COOKIE_VALUE:
        session.cookies.set('Aut', AUT_COOKIE_VALUE, domain='panel.eways.co')
    session.verify = False
    return session

def extract_real_id_from_link(link):
    m = re.search(r'/store/(?:list|categorylist)/(\d+)', str(link), re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None

def extract_categories_from_html(html):
    soup = BeautifulSoup(html, 'lxml')
    root_ul = soup.find('ul', id='kanivmm-menu-id')
    if not root_ul:
        root_ul = soup.find('ul', class_='kanivmm-menu-class')
    if not root_ul:
        print("❌ منوی دسته‌بندی در HTML پیدا نشد!")
        return []

    flat_list = []
    for li in root_ul.find_all('li', recursive=False):
        a = li.find('a', recursive=False)
        if a:
            name = a.get_text(strip=True)
            link = a.get('href')
            real_id = extract_real_id_from_link(link)
            if not name or not link or not real_id:
                continue
            cat = {
                'id': real_id,
                'name': name,
                'link': link,
                'parent_id': None,
                'level': 0,
                'children': []
            }
            flat_list.append(cat)
    return flat_list

def extract_subcategories_from_html(html, parent_id=None, level=0):
    soup = BeautifulSoup(html, 'lxml')
    flat_list = []
    def recursive_extract(ul_tag, parent_id, level):
        categories = []
        for li in ul_tag.find_all('li', recursive=False):
            a = li.find('a', recursive=False)
            if a:
                name = a.get_text(strip=True)
                link = a.get('href')
                real_id = extract_real_id_from_link(link)
                if not name or not link or not real_id:
                    continue
                cat = {
                    'id': real_id,
                    'name': name,
                    'link': link,
                    'parent_id': parent_id,
                    'level': level,
                    'children': []
                }
                flat_list.append(cat)
                sub_ul = li.find('ul', class_='sub-menu')
                if sub_ul:
                    cat['children'] = recursive_extract(sub_ul, real_id, level+1)
                categories.append(cat)
        return categories

    root_ul = soup.find('ul', id='kanivmm-menu-id')
    if not root_ul:
        root_ul = soup.find('ul', class_='kanivmm-menu-class')
    if root_ul:
        recursive_extract(root_ul, parent_id, level)
    return flat_list

def get_all_categories_recursive(session, start_cat_ids):
    all_cats = {}
    visited = set()
    def fetch_and_extract(cat_id, parent_id=None, level=0):
        url = CATEGORY_PAGE_URL_TEMPLATE.format(cat_id=cat_id)
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code != 200:
                return []
            subcats = extract_subcategories_from_html(resp.text, parent_id, level)
            for cat in subcats:
                if cat['id'] not in all_cats:
                    all_cats[cat['id']] = cat
            for cat in subcats:
                if cat['id'] not in visited:
                    visited.add(cat['id'])
                    fetch_and_extract(cat['id'], cat['parent_id'], cat['level'])
            return subcats
        except Exception as e:
            return []

    for cat_id in start_cat_ids:
        if cat_id not in visited:
            visited.add(cat_id)
            fetch_and_extract(cat_id, None, 0)

    print(f"✅ تعداد کل دسته‌بندی (با زیرشاخه): {len(all_cats)}")
    return list(all_cats.values())

def get_products_from_category_page(session, category_id):
    all_products_in_category = []
    seen_product_ids = set()
    page_num = 1
    MAX_PAGES = 50

    while page_num <= MAX_PAGES:
        url = PRODUCT_LIST_URL_TEMPLATE.format(category_id=category_id, page=page_num)
        try:
            response = session.get(url, timeout=30)
            if response.status_code != 200: break
            soup = BeautifulSoup(response.text, 'lxml')
            product_blocks = soup.select(".goods_item.goods-record")
            if not product_blocks:
                break
            current_page_product_ids = []
            for block in product_blocks:
                try:
                    if 'noCount' in block.get('class', []): continue
                    id_tag = block.select_one("a[data-productid]")
                    product_id = id_tag['data-productid'] if id_tag else None
                    if not product_id or product_id in seen_product_ids: continue
                    seen_product_ids.add(product_id)
                    current_page_product_ids.append(product_id)
                    name = (block.select_one(".goods-record-title").text.strip() if block.select_one(".goods-record-title") else None)
                    price_tag = block.select_one(".goods-record-price")
                    price = "0"
                    if price_tag:
                        if price_tag.find('del'): price_tag.find('del').decompose()
                        price = re.sub(r'[^\d]', '', price_tag.text.strip()) or "0"
                    img_tag = block.select_one("img.goods-record-image")
                    image_url = (img_tag['data-src'] if img_tag and 'data-src' in img_tag.attrs else "")
                    if image_url and not image_url.startswith('http'):
                        image_url = "https://staticcontent.eways.co" + image_url
                    stock_tag = block.select_one(".goods-record-count span")
                    stock = int(stock_tag.text.strip()) if stock_tag else 1
                    if name and int(price) > 0:
                        all_products_in_category.append({
                            "id": product_id,
                            "name": name,
                            "price": price,
                            "stock": stock,
                            "image": image_url,
                            "category_id": category_id
                        })
                except Exception as e:
                    pass
            if not current_page_product_ids:
                break
            page_num += 1
            time.sleep(random.uniform(0.5, 1.5))
        except Exception as e:
            break
    return all_products_in_category

def main():
    print("⏳ دریافت دسته‌بندی‌ها از: " + SOURCE_CATS_API_URL)
    session = get_session()
    main_menu_html = session.get(SOURCE_CATS_API_URL).text
    main_cats = extract_categories_from_html(main_menu_html)
    if not main_cats:
        print("❌ هیچ دسته‌بندی اصلی پیدا نشد.")
        return

    print("\nدسته‌بندی‌های اصلی موجود:")
    for cat in main_cats:
        print(f"[{cat['id']}] {cat['name']}")

    # همه دسته‌ها و زیرشاخه‌ها را بازگشتی جمع کن
    all_cats = {}
    for cat in main_cats:
        all_cats[cat['id']] = cat
        subcats = get_all_categories_recursive(session, [cat['id']])
        for sub in subcats:
            all_cats[sub['id']] = sub

    print(f"\n✅ تعداد کل دسته‌بندی (با زیرشاخه): {len(all_cats)}")

    # بررسی همه دسته‌ها برای وجود محصول
    print("🕵️‍♂️ در حال جستجوی خودکار برای دسته‌هایی که محصول دارند...")
    found_any = False
    for cat in tqdm(list(all_cats.values()), desc="بررسی دسته‌بندی‌ها برای یافتن محصول موجود"):
        products = get_products_from_category_page(session, cat['id'])
        if products:
            found_any = True
            print(f"\n✅ دسته‌بندی [{cat['id']}] {cat['name']} دارای {len(products)} محصول موجود است.")
    if not found_any:
        print("❌ هیچ دسته‌بندی با محصول موجود پیدا نشد.")

if __name__ == "__main__":
    main()
