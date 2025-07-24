import os
import requests
from bs4 import BeautifulSoup
import csv
import time

# --- اطلاعات ووکامرس ---
WC_API_URL = os.environ.get("WC_API_URL")
WC_CONSUMER_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET")

# ---------- استخراج دسته‌بندی‌ها از HTML ----------
def extract_categories(ul_tag, parent_id=None, level=0, flat_list=None, id_counter=None):
    if flat_list is None:
        flat_list = []
    if id_counter is None:
        id_counter = [1]
    categories = []
    for li in ul_tag.find_all('li', recursive=False):
        a = li.find('a', recursive=False)
        if a:
            name = a.get_text(strip=True)
            link = a.get('href')
            cat_id = id_counter[0]
            id_counter[0] += 1
            cat = {
                'id': cat_id,
                'name': name,
                'link': link,
                'parent_id': parent_id,
                'level': level,
                'children': []
            }
            flat_list.append(cat)
            # اگر زیرمنو دارد، بازگشتی برو داخلش
            sub_ul = li.find('ul', class_='sub-menu')
            if sub_ul:
                cat['children'] = extract_categories(sub_ul, parent_id=cat_id, level=level+1, flat_list=flat_list, id_counter=id_counter)
            categories.append(cat)
    return categories

def print_tree(categories, flat_list, indent=0):
    for cat in categories:
        print('  ' * cat['level'] + f"- {cat['name']} ({cat['link']})")
        if cat['children']:
            print_tree(cat['children'], flat_list, indent+1)

# ---------- ووکامرس: دریافت و ساخت دسته‌بندی ----------
def get_wc_categories():
    wc_cats = []
    page = 1
    while True:
        url = f"{WC_API_URL}/products/categories"
        params = {"per_page": 100, "page": page}
        res = requests.get(url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), params=params, verify=False)
        if res.status_code != 200:
            break
        data = res.json()
        if not data:
            break
        wc_cats.extend(data)
        if len(data) < 100:
            break
        page += 1
    return wc_cats

def build_wc_cats_map(wc_cats):
    return {cat["name"].strip(): cat["id"] for cat in wc_cats}

def create_wc_category(cat, wc_cats_map, source_to_wc_id_map):
    data = {
        "name": cat['name'].strip(),
        "slug": cat['name'].strip().replace(' ', '-'),
        "description": "",
    }
    if cat['parent_id']:
        parent_cat = next((c for c in flat_list if c['id'] == cat['parent_id']), None)
        if parent_cat and parent_cat['name'] in wc_cats_map:
            data["parent"] = wc_cats_map[parent_cat['name']]
    res = requests.post(
        f"{WC_API_URL}/products/categories",
        auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET),
        json=data,
        verify=False
    )
    if res.status_code in [200, 201]:
        new_id = res.json()["id"]
        print(f"✅ دسته‌بندی '{cat['name']}' ساخته شد. (id={new_id})")
        return new_id
    else:
        print(f"❌ خطا در ساخت دسته‌بندی '{cat['name']}': {res.text}")
        return None

def sort_cats_for_creation(flat_cats):
    sorted_cats = []
    id_to_cat = {cat["id"]: cat for cat in flat_cats}
    visited = set()
    def visit(cat):
        if cat["id"] in visited:
            return
        parent_id = cat.get("parent_id")
        if parent_id and parent_id in id_to_cat:
            visit(id_to_cat[parent_id])
        sorted_cats.append(cat)
        visited.add(cat["id"])
    for cat in flat_cats:
        visit(cat)
    return sorted_cats

# ---------- اجرای اصلی ----------
if __name__ == "__main__":
    # 1. خواندن HTML منو
    with open('storemenu.html', 'r', encoding='utf-8') as f:
        html_content = f.read()
    soup = BeautifulSoup(html_content, 'html.parser')

    # 2. پیدا کردن ریشه دسته‌بندی‌ها
    root_ul = soup.find('ul', id='kanivmm-menu-id')
    if not root_ul:
        root_ul = soup.find('ul', class_='kanivmm-menu-class')

    all_categories = []
    flat_list = []
    id_counter = [1]
    for li in root_ul.find_all('li', recursive=False):
        sub_ul = li.find('ul', class_='sub-menu')
        if sub_ul:
            all_categories.extend(extract_categories(sub_ul, parent_id=None, level=0, flat_list=flat_list, id_counter=id_counter))

    print(f"\n🔹 تعداد کل دسته‌بندی‌های یکتا (همه سطوح): {len(flat_list)}\n")
    print("ساختار درختی دسته‌بندی‌ها:\n")
    print_tree(all_categories, flat_list)

    # 3. ذخیره خروجی تخت به CSV
    with open('categories_flat.csv', 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['id', 'name', 'link', 'parent_id', 'level']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for cat in flat_list:
            writer.writerow({k: cat[k] for k in fieldnames})
    print("\nخروجی CSV با نام categories_flat.csv ذخیره شد.")

    # 4. انتقال به ووکامرس (ساخت دسته‌بندی‌ها)
    print("\n⏳ دریافت دسته‌بندی‌های ووکامرس ...")
    wc_cats = get_wc_categories()
    wc_cats_map = build_wc_cats_map(wc_cats)
    print(f"تعداد کل دسته‌بندی ووکامرس: {len(wc_cats)}")

    sorted_cats = sort_cats_for_creation(flat_list)
    source_to_wc_id_map = {}

    for cat in sorted_cats:
        name = cat["name"].strip()
        if name in wc_cats_map:
            wc_id = wc_cats_map[name]
            print(f"⏩ دسته‌بندی '{name}' قبلاً وجود دارد. (id={wc_id})")
            source_to_wc_id_map[cat["id"]] = wc_id
        else:
            new_id = create_wc_category(cat, wc_cats_map, source_to_wc_id_map)
            if new_id:
                wc_cats_map[name] = new_id
                source_to_wc_id_map[cat["id"]] = new_id
        time.sleep(0.5)  # برای جلوگیری از بلاک شدن

    print("\n✅ انتقال دسته‌بندی‌ها کامل شد.")
    print(f"🔸 تعداد کل دسته‌بندی‌های ساخته شده یا نگاشت شده: {len(source_to_wc_id_map)}")
