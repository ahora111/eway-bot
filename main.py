import os
import time
import requests
import logging
import json
import base64
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from persiantools.jdatetime import JalaliDate
from pytz import timezone
from datetime import datetime

# غیرفعال کردن هشدار SSL (برای سایت با گواهی منقضی)
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = 'Sheet1'
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def fetch_products_json():
    url = "https://panel.naminet.co/api/catalog/productGroupsAttrNew?term="
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJuYmYiOiIxNzUyMjUyMTE2IiwiZXhwIjoiMTc2MDAzMTcxNiIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL2VtYWlsYWRkcmVzcyI6IjA5MzcxMTExNTU4QGhtdGVtYWlsLm5leHQiLCJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6ImE3OGRkZjViLTVhMjMtNDVkZC04MDBlLTczNTc3YjBkMzQzOSIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL25hbWUiOiIwOTM3MTExMTU1OCIsIkN1c3RvbWVySWQiOiIxMDA4NCJ9.kXoXA0atw0M64b6m084Gt4hH9MoC9IFFDFwuHOEdazA"
    }
    response = requests.get(url, headers=headers, verify=False)
    print("Status code:", response.status_code)
    print("Response text:", response.text[:500])
    data = response.json()
    return data

def extract_products(data):
    products = []
    for parent in data.get("ParentCategories", []):
        for category in parent.get("Data", []):
            category_name = category.get("Name", "")
            for item in category.get("Data", []):
                product_name = item.get("ProductName", "")
                color = item.get("Name", "")
                price = item.get("final_price_value", 0)
                price = f"{int(price):,}"
                products.append({
                    "category": category_name,
                    "product": product_name,
                    "color": color,
                    "price": price
                })
    return products

def escape_special_characters(text):
    escape_chars = ['\\', '(', ')', '[', ']', '~', '*', '_', '-', '+', '>', '#', '.', '!', '|']
    for char in escape_chars:
        text = text.replace(char, '\\' + char)
    return text

def split_message_by_emoji_group(message, max_length=4000):
    lines = message.split('\n')
    parts = []
    current = ""
    group = ""
    for line in lines:
        if line.startswith(('🔵', '🟡', '🍏', '🟣', '💻', '🟠', '🎮')):
            if current and len(current) + len(group) > max_length:
                parts.append(current.rstrip('\n'))
                current = ""
            current += group
            group = ""
        group += line + '\n'
    if current and len(current) + len(group) > max_length:
        parts.append(current.rstrip('\n'))
        current = ""
    current += group
    if current.strip():
        parts.append(current.rstrip('\n'))
    return parts

def get_current_time():
    iran_tz = timezone('Asia/Tehran')
    iran_time = datetime.now(iran_tz)
    current_time = iran_time.strftime('%H:%M')
    return current_time

def prepare_final_message(category_name, category_lines, update_date):
    update_date = JalaliDate.today().strftime("%Y/%m/%d")
    current_time = get_current_time()
    weekday_mapping = {
            "Saturday": "شنبه💪",
            "Sunday": "یکشنبه😃",
            "Monday": "دوشنبه☺️",
            "Tuesday": "سه شنبه🥱",
            "Wednesday": "چهارشنبه😕",
            "Thursday": "پنج شنبه☺️",
            "Friday": "جمعه😎"
    }
    weekday_english = JalaliDate.today().weekday()
    weekday_farsi = list(weekday_mapping.values())[weekday_english]
    update_date_formatted = f"{weekday_farsi} {update_date.replace('-', '/')}"
    header = (
        f"🗓 بروزرسانی {update_date_formatted} 🕓 ساعت: {current_time}\n"
        f"✅ لیست پخش موبایل اهورا\n\n"
        f"⬅️ موجودی گوشیای متفرقه ➡️\n\n"
    )
    formatted_lines = category_lines
    footer = "\n\n☎️ شماره های تماس :\n📞 09371111558\n📞 02833991417"
    final_message = f"{header}" + "\n".join(formatted_lines) + f"{footer}"
    return final_message

def get_credentials():
    encoded = os.getenv("GSHEET_CREDENTIALS_JSON")
    if not encoded:
        raise Exception("Google Sheets credentials not found in environment variable")
    decoded = base64.b64decode(encoded)
    temp_path = "/tmp/creds.json"
    with open(temp_path, "wb") as f:
        f.write(decoded)
    return temp_path

def connect_to_sheet():
    creds_path = get_credentials()
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    credentials = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(credentials)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
    return sheet

def check_and_create_headers(sheet):
    first_row = sheet.get_all_values()[0] if sheet.get_all_values() else []
    headers = ["emoji", "date", "part", "message_id", "text"]
    if first_row != headers:
        sheet.update(values=[headers], range_name="A1:E1")
        logging.info("✅ هدرها اضافه شدند.")
    else:
        logging.info("🔄 هدرها قبلاً موجود هستند.")

def load_sheet_data(sheet):
    records = sheet.get_all_records()
    data = {}
    for row in records:
        emoji = row.get("emoji")
        date = row.get("date")
        part = row.get("part")
        if emoji and date:
            data.setdefault((emoji, date), []).append({
                "part": int(part),
                "message_id": row.get("message_id"),
                "text": row.get("text")
            })
    return data

def update_sheet_data(sheet, emoji, messages):
    today = JalaliDate.today().strftime("%Y-%m-%d")
    records = sheet.get_all_records()
    rows_to_delete = [i+2 for i, row in enumerate(records) if row.get("emoji") == emoji and row.get("date") == today]
    for row_num in reversed(rows_to_delete):
        sheet.delete_rows(row_num)
    for part, (message_id, text) in enumerate(messages, 1):
        sheet.append_row([emoji, today, part, message_id, text])

def send_telegram_message(message, bot_token, chat_id):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    params = {
        "chat_id": chat_id,
        "text": escape_special_characters(message),
        "parse_mode": "MarkdownV2"
    }
    response = requests.post(url, json=params)
    if response.ok:
        return response.json()["result"]["message_id"]
    else:
        logging.error("خطا در ارسال پیام: %s", response.text)
        return None

def edit_telegram_message(message_id, message, bot_token, chat_id):
    url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
    params = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": escape_special_characters(message),
        "parse_mode": "MarkdownV2"
    }
    response = requests.post(url, json=params)
    return response.ok

def delete_telegram_message(message_id, bot_token, chat_id):
    url = f"https://api.telegram.org/bot{bot_token}/deleteMessage"
    params = {
        "chat_id": chat_id,
        "message_id": message_id
    }
    response = requests.post(url, json=params)
    return response.ok

def process_category_messages(emoji, messages, bot_token, chat_id, sheet, today):
    sheet_data = load_sheet_data(sheet)
    prev_msgs = sorted([row for row in sheet_data.get((emoji, today), [])], key=lambda x: x["part"])
    new_msgs = []
    should_send_final_message = False
    for i, msg in enumerate(messages):
        if i < len(prev_msgs):
            if prev_msgs[i]["text"] != msg:
                ok = edit_telegram_message(prev_msgs[i]["message_id"], msg, bot_token, chat_id)
                if not ok:
                    message_id = send_telegram_message(msg, bot_token, chat_id)
                    should_send_final_message = True
                else:
                    message_id = prev_msgs[i]["message_id"]
                    should_send_final_message = True
            else:
                message_id = prev_msgs[i]["message_id"]
        else:
            message_id = send_telegram_message(msg, bot_token, chat_id)
            should_send_final_message = True
        new_msgs.append((message_id, msg))
    for j in range(len(messages), len(prev_msgs)):
        delete_telegram_message(prev_msgs[j]["message_id"], bot_token, chat_id)
        should_send_final_message = True
    update_sheet_data(sheet, emoji, new_msgs)
    return [msg_id for msg_id, _ in new_msgs], should_send_final_message

def update_final_message_in_sheet(sheet, message_id, text):
    today = JalaliDate.today().strftime("%Y-%m-%d")
    records = sheet.get_all_records()
    found = False
    for i, row in enumerate(records, start=2):
        if row.get("emoji") == "FINAL" and row.get("date") == today:
            sheet.update(values=[["FINAL", today, 1, message_id, text]], range_name=f"A{i}:E{i}")
            found = True
            break
    if not found:
        sheet.append_row(["FINAL", today, 1, message_id, text])

def get_final_message_from_sheet(sheet):
    today = JalaliDate.today().strftime("%Y-%m-%d")
    records = sheet.get_all_records()
    for row in records:
        if row.get("emoji") == "FINAL" and row.get("date") == today:
            return row.get("message_id"), row.get("text")
    return None, None

def send_or_edit_final_message(sheet, final_message, bot_token, chat_id, button_markup, should_send):
    message_id, prev_text = get_final_message_from_sheet(sheet)
    escaped_text = escape_special_characters(final_message)
    if message_id and prev_text == final_message and not should_send:
        logging.info("🔁 پیام نهایی تغییری نکرده است.")
        return message_id
    if message_id and (prev_text != final_message or should_send):
        url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
        params = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": escaped_text,
            "parse_mode": "MarkdownV2",
            "reply_markup": json.dumps(button_markup)
        }
        response = requests.post(url, json=params)
        if response.ok:
            update_final_message_in_sheet(sheet, message_id, final_message)
            logging.info("✅ پیام نهایی ویرایش شد.")
            return message_id
        else:
            logging.warning("❌ خطا در ویرایش پیام نهایی، حذف پیام قبلی و ارسال پیام جدید.")
            # حذف پیام قبلی
            del_url = f"https://api.telegram.org/bot{bot_token}/deleteMessage"
            del_params = {
                "chat_id": chat_id,
                "message_id": message_id
            }
            del_response = requests.post(del_url, json=del_params)
            if del_response.ok:
                logging.info("✅ پیام نهایی قبلی حذف شد.")
            else:
                logging.warning("❌ حذف پیام نهایی قبلی موفق نبود: %s", del_response.text)
    # ارسال پیام جدید
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    params = {
        "chat_id": chat_id,
        "text": escaped_text,
        "parse_mode": "MarkdownV2",
        "reply_markup": json.dumps(button_markup)
    }
    response = requests.post(url, json=params)
    if response.ok:
        message_id = response.json()["result"]["message_id"]
        update_final_message_in_sheet(sheet, message_id, final_message)
        logging.info("✅ پیام نهایی ارسال شد.")
        return message_id
    else:
        logging.error("❌ خطا در ارسال پیام نهایی: %s", response.text)
        return None

def main():
    try:
        sheet = connect_to_sheet()
        check_and_create_headers(sheet)
        data = fetch_products_json()
        products = extract_products(data)
        if not products:
            logging.warning("❌ داده‌ای برای ارسال وجود ندارد!")
            return
        emoji_map = {
            "گوشی سامسونگ": "🔵",
            "گوشی شیائومی": "🟡",
            "گوشی آیفون": "🍏",
            "گوشی نوکیا": "🟣",
            "گوشی وکال": "🟣",
            "گوشی داریا": "🟣",
            "گوشی تی سی ال": "🟣",
            "گوشی رد تون": "🟣",
            "گوشی ریلمی": "🟣",
            "ناتینگ فون": "🟣",
            "تبلت": "🟠",
        }
        categorized = {}
        for p in products:
            emoji = emoji_map.get(p["category"], "🟣")
            line = f"{emoji} {p['product']} | {p['color']} | {p['price']} تومان"
            categorized.setdefault(emoji, []).append(line)
        today = JalaliDate.today().strftime("%Y-%m-%d")
        all_message_ids = {}
        should_send_final_message = False
        for emoji, lines in categorized.items():
            if not lines:
                continue
            message = prepare_final_message(emoji, lines, today)
            message_parts = split_message_by_emoji_group(message)
            current_time = get_current_time()
            for idx in range(1, len(message_parts)):
                message_parts[idx] = f"⏰ {current_time}\n" + message_parts[idx]
            message_ids, changed = process_category_messages(emoji, message_parts, BOT_TOKEN, CHAT_ID, sheet, today)
            all_message_ids[emoji] = message_ids
            if changed:
                should_send_final_message = True
        final_message = (
            "✅ لیست گوشی و سایر کالاهای بالا بروز میباشد. ثبت خرید تا ساعت 10:30 شب انجام میشود و تحویل کالا ساعت 11:30 صبح روز بعد می باشد..\n\n"
            "✅اطلاعات واریز\n"
            "🔷 شماره شبا : IR970560611828006154229701\n"
            "🔷 شماره کارت : 6219861812467917\n"
            "🔷 بلو بانک   حسین گرئی\n\n"
            "⭕️ حتما رسید واریز به ایدی تلگرام زیر ارسال شود .\n"
            "🆔 @lhossein1\n\n"
            "✅شماره تماس ثبت سفارش :\n"
            "📞 09371111558\n"
            "📞 09386373926\n"
            "📞 09308529712\n"
            "📞 028-3399-1417"
        )
        button_markup = {"inline_keyboard": []}
        emoji_labels = {
            "🔵": "📱 لیست سامسونگ",
            "🟡": "📱 لیست شیائومی",
            "🍏": "📱 لیست آیفون",
            "🟣": "📱 لیست گوشیای متفرقه",
            "🟠": "📱 لیست تبلت"
        }
        for emoji, msg_ids in all_message_ids.items():
            for msg_id in msg_ids:
                if msg_id:
                    button_markup["inline_keyboard"].append([
                        {"text": emoji_labels.get(emoji, emoji), "url": f"https://t.me/c/{CHAT_ID.replace('-100', '')}/{msg_id}"}
                    ])
        send_or_edit_final_message(sheet, final_message, BOT_TOKEN, CHAT_ID, button_markup, should_send_final_message)
    except Exception as e:
        logging.error(f"❌ خطا: {e}")

if __name__ == "__main__":
    main()
