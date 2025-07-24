def main():
    # 1. دریافت mega_menu از API منبع
    print("در حال دریافت دسته‌بندی‌ها از API منبع ...")
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Authorization': AUTH_TOKEN,
        'Referer': REFERER_URL
    }
    resp = requests.get(MEGA_MENU_API_URL, headers=headers, verify=False)
    if resp.status_code != 200:
        print("❌ خطا در دریافت mega_menu از API منبع:", resp.status_code)
        print(resp.text)
        return

    try:
        mega_menu_json = resp.json()
    except Exception as e:
        print("❌ خطا در تبدیل پاسخ به JSON:", e)
        print("پاسخ دریافتی:")
        print(resp.text)
        return

    mega_menu = mega_menu_json.get("mega_menu", [])
    if not mega_menu:
        print("❌ mega_menu خالی است یا کلید mega_menu وجود ندارد!")
        return

    # ادامه کد...
