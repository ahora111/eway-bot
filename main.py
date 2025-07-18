
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

# --- Configuration (خواندن متغیرها از محیط) ---
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = 'Sheet1'
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
# مهم: توکن API از اینجا خوانده می‌شود و دیگر در کد نیست
NAMINet_API_TOKEN = os.getenv("NAMINET_API_TOKEN")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def fetch_products_json():
    """اطلاعات محصولات را از API دریافت می‌کند."""
    if not NAMINet_API_TOKEN:
        logging.error("❌ توکن Naminet API در متغیرهای محیطی (NAMINET_API_TOKEN) یافت نشد.")
        return None

    url = "https://panel.naminet.co/api/catalog/productGroupsAttrNew?term="
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {NAMINet_API_TOKEN}" # استفاده از توکن خوانده شده
    }

    try:
        response = requests.get(url, headers=headers, verify=False, timeout=30)
        response.raise_for_status()
        logging.info("✅ اطلاعات با موفقیت از API دریافت شد. وضعیت: %s", response.status_code)
        return response.json()
    except requests.exceptions.RequestException as err:
        logging.error("❌ خطا در درخواست به API: %s", err)
    return None

def extract_products(data):
    """اطلاعات محصولات را از داده خام استخراج و فرمت‌بندی می‌کند."""
    if not data or "ParentCategories" not in data:
        return []
        
    products = []
    for parent in data.get("ParentCategories", []):
        for category in parent.get("Data", []):
            category_name = category.get("Name", "متفرقه")
            for item in category.get("Data", []):
                price = item.get("final_price_value", 0)
                products.append({
                    "category": category_name,
                    "product": item.get("ProductName", "نامشخص"),
                    "color": item.get("Name", "نامشخص"),
                    "price": f"{int(price):,}"
                })
    return products

def escape_special_characters(text):
    """کاراکترهای خاص تلگرام برای MarkdownV2 را اصلاح می‌کند."""
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

def split_message(message, max_length=4000):
    """یک پیام طولانی را به بخش‌های کوچک‌تر تقسیم می‌کند."""
    lines = message.split('\n')
    parts = []
    current_part = ""
    for line in lines:
        if len(current_part) + len(line) + 1 > max_length:
            if current_part:
                parts.append(current_part.strip())
            current_part = line + '\n'
        else:
            current_part += line + '\n'
    if current_part.strip():
        parts.append(current_part.strip())
    return parts

def get_current_time_and_date():
    """تاریخ و زمان فعلی شمسی و نام روز هفته را برمی‌گرداند."""
    iran_tz = timezone('Asia/Tehran')
    now = datetime.now(iran_tz)
    jalali_today = JalaliDate(now)
    date_str_slash = jalali_today.strftime("%Y/%m/%d")
    date_str_dash = jalali_today.strftime("%Y-%m-%d")
    time_str = now.strftime('%H:%M')
    weekday_map = {0: "شنبه💪", 1: "یکشنبه😃", 2: "دوشنبه☺️", 3: "سه‌شنبه🥱", 4: "چهارشنبه😕", 5: "پنج‌شنبه🥳", 6: "جمعه😎"}
    weekday_farsi = weekday_map[jalali_today.weekday()]
    return date_str_slash, date_str_dash, time_str, weekday_farsi

def prepare_category_message(category_lines, update_date_str, time_str, weekday_farsi):
    """متن پیام برای یک دسته‌بندی خاص را آماده می‌کند."""
    update_date_formatted = f"{weekday_farsi} {update_date_str}"
    header = f"🗓 بروزرسانی {update_date_formatted} 🕓 ساعت: {time_str}\n✅ لیست پخش موبایل اهورا\n\n"
    content = "\n".join(category_lines)
    footer = "\n\n☎️ شماره های تماس :\n📞 09371111558\n📞 02833991417"
    return f"{header}{content}{footer}"

def get_credentials():
    """اطلاعات گوگل شیت را از متغیر محیطی می‌خواند."""
    encoded_creds = os.getenv("GSHEET_CREDENTIALS_JSON")
    if not encoded_creds:
        raise ValueError("❌ متغیر GSHEET_CREDENTIALS_JSON یافت نشد.")
    decoded_creds = base64.b64decode(encoded_creds)
    temp_path = "/tmp/gsheet_creds.json"
    with open(temp_path, "wb") as f:
        f.write(decoded_creds)
    return temp_path

def connect_to_sheet():
    """به گوگل شیت متصل می‌شود."""
    creds_path = get_credentials()
    scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets', "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
    logging.info("✅ با موفقیت به گوگل شیت متصل شد.")
    return sheet

def check_and_create_headers(sheet):
    """هدرهای شیت را بررسی و در صورت نیاز ایجاد می‌کند."""
    try:
        first_row = sheet.row_values(1)
    except (gspread.exceptions.APIError, IndexError):
        first_row = []
    headers = ["emoji", "date", "part", "message_id", "text"]
    if first_row != headers:
        sheet.insert_row(headers, 1)
        logging.info("✅ هدرها در شیت ایجاد شدند.")
    else:
        logging.info("🔄 هدرها از قبل موجود هستند.")

def load_sheet_data(sheet, date_str_dash):
    """اطلاعات پیام‌های امروز را از شیت بارگذاری می‌کند."""
    records = sheet.get_all_records()
    today_data = {}
    for row in records:
        if str(row.get("date")) == date_str_dash:
            emoji = row.get("emoji")
            if emoji and emoji not in today_data:
                today_data[emoji] = {}
            today_data[emoji][int(row["part"])] = {"message_id": row["message_id"], "text": row["text"]}
    return today_data

def update_sheet_data(sheet, emoji, date_str_dash, new_messages):
    """اطلاعات جدید پیام‌ها را در شیت آپدیت (حذف و اضافه) می‌کند."""
    cell_list = sheet.findall(emoji, in_column=1)
    date_cell_list = sheet.findall(date_str_dash, in_column=2)
    emoji_rows = {cell.row for cell in cell_list}
    date_rows = {cell.row for cell in date_cell_list}
    for row_num in sorted(list(emoji_rows.intersection(date_rows)), reverse=True):
        sheet.delete_rows(row_num)
    rows_to_add = []
    for part, (message_id, text) in enumerate(new_messages, 1):
        if message_id:
            rows_to_add.append([emoji, date_str_dash, part, message_id, text])
    if rows_to_add:
        sheet.append_rows(rows_to_add)
    logging.info(f"🔄 شیت برای ایموجی '{emoji}' بروزرسانی شد.")

def send_telegram_api(method, params):
    """یک متد را در API تلگرام فراخوانی می‌کند."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    response = requests.post(url, json=params)
    if not response.ok:
        # خطای "message is not modified" یک خطای واقعی نیست، پس آن را نادیده می‌گیریم
        if "message is not modified" not in response.text:
            logging.warning("⚠️ خطا در API تلگرام (%s): %s", method, response.text)
        return None
    return response.json().get("result")

def process_category_messages(emoji, new_messages, today_sheet_data, bot_token, chat_id):
    """پیام‌های یک دسته را مدیریت (ارسال، ویرایش، حذف) می‌کند."""
    prev_msgs = today_sheet_data.get(emoji, {})
    final_message_data = []
    changes_made = False
    for i, new_msg_text in enumerate(new_messages, 1):
        message_id = None
        if i in prev_msgs:
            old_msg = prev_msgs[i]
            if old_msg["text"] != new_msg_text:
                changes_made = True
                result = send_telegram_api("editMessageText", {"chat_id": chat_id, "message_id": old_msg["message_id"], "text": escape_special_characters(new_msg_text), "parse_mode": "MarkdownV2"})
                message_id = old_msg["message_id"] if result else None
            else:
                message_id = old_msg["message_id"]
        else:
            changes_made = True
        
        if not message_id: # اگر نیاز به ارسال پیام جدید باشد (چه برای اولین بار، چه به دلیل شکست ویرایش)
            if i in prev_msgs: send_telegram_api("deleteMessage", {"chat_id": chat_id, "message_id": prev_msgs[i]["message_id"]})
            result = send_telegram_api("sendMessage", {"chat_id": chat_id, "text": escape_special_characters(new_msg_text), "parse_mode": "MarkdownV2"})
            if result: message_id = result["message_id"]

        if message_id: final_message_data.append((message_id, new_msg_text))

    for part_num, old_msg in prev_msgs.items():
        if part_num > len(new_messages):
            changes_made = True
            send_telegram_api("deleteMessage", {"chat_id": chat_id, "message_id": old_msg["message_id"]})
            
    return final_message_data, changes_made

def send_or_edit_final_message(text, markup, today_data, bot_token, chat_id, force_resend=False):
    """پیام نهایی (پین شده) را مدیریت می‌کند."""
    prev_final = today_data.get("FINAL", {}).get(1)
    if prev_final:
        message_id = prev_final["message_id"]
        if prev_final["text"] == text and not force_resend:
            logging.info("🔁 پیام نهایی بدون تغییر است.")
            return [(message_id, text)]
        
        result = send_telegram_api("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": escape_special_characters(text), "parse_mode": "MarkdownV2", "reply_markup": markup})
        if result: return [(message_id, text)]
        send_telegram_api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    logging.info("🚀 در حال ارسال پیام نهایی جدید...")
    result = send_telegram_api("sendMessage", {"chat_id": chat_id, "text": escape_special_characters(text), "parse_mode": "MarkdownV2", "reply_markup": markup})
    if result: return [(result["message_id"], text)]
    return []

def main():
    """تابع اصلی اجرای اسکریپت."""
    if not all([SPREADSHEET_ID, BOT_TOKEN, CHAT_ID, NAMINet_API_TOKEN]):
        logging.error("❌ یک یا چند متغیر محیطی ضروری تعریف نشده‌اند. لطفا از وجود SPREADSHEET_ID, TELEGRAM_TOKEN, CHAT_ID, NAMINet_API_TOKEN اطمینان حاصل کنید.")
        return

    try:
        json_data = fetch_products_json()
        if not json_data: return
        products = extract_products(json_data)
        if not products:
            logging.info("✅ در حال حاضر محصولی برای نمایش وجود ندارد.")
            return

        date_slash, date_dash, current_time, weekday_farsi = get_current_time_and_date()
        sheet = connect_to_sheet()
        check_and_create_headers(sheet)
        today_sheet_data = load_sheet_data(sheet, date_dash)

        emoji_map = {"گوشی سامسونگ": "🔵", "گوشی شیائومی": "🟡", "گوشی آیفون": "🍏", "تبلت": "🟠", "لپ تاپ": "💻"}
        categorized_lines = {}
        for p in products:
            emoji = emoji_map.get(p["category"], "🟣")
            line = f"{emoji} {p['product']} | {p['color']} | {p['price']} تومان"
            categorized_lines.setdefault(emoji, []).append(line)
        
        any_changes_made = False
        all_message_ids_for_buttons = {}

        for emoji, lines in categorized_lines.items():
            full_message = prepare_category_message(lines, date_slash, current_time, weekday_farsi)
            message_parts = split_message(full_message)
            final_data, changed = process_category_messages(emoji, message_parts, today_sheet_data, BOT_TOKEN, CHAT_ID)
            if changed:
                any_changes_made = True
                update_sheet_data(sheet, emoji, date_dash, final_data)
            all_message_ids_for_buttons[emoji] = [msg_id for msg_id, _ in final_data]

        final_message_text = "✅ لیست گوشی و سایر کالاهای بالا بروز میباشد. ثبت خرید تا ساعت 10:30 شب انجام میشود و تحویل کالا ساعت 11:30 صبح روز بعد می باشد.\n\n✅اطلاعات واریز\n🔷 شماره شبا : IR970560611828006154229701\n🔷 شماره کارت : 6219861812467917\n🔷 بلو بانک   حسین گرئی\n\n⭕️ حتما رسید واریز به ایدی تلگرام زیر ارسال شود .\n🆔 @lhossein1\n\n✅شماره تماس ثبت سفارش :\n📞 09371111558\n📞 09386373926\n📞 09308529712\n📞 028-3399-1417"
        button_labels = {"🔵": "📱 لیست سامسونگ", "🟡": "📱 لیست شیائومی", "🍏": "📱 لیست آیفون", "🟣": "📱 لیست سایر برندها", "🟠": "📱 لیست تبلت", "💻": "💻 لیست لپ‌تاپ"}
        channel_numeric_id = CHAT_ID.replace('-100', '')
        inline_keyboard = []
        for emoji, label in button_labels.items():
            if all_message_ids_for_buttons.get(emoji):
                first_message_id = all_message_ids_for_buttons[emoji][0]
                inline_keyboard.append([{"text": label, "url": f"https://t.me/c/{channel_numeric_id}/{first_message_id}"}])
        
        button_markup = {"inline_keyboard": inline_keyboard}
        final_message_data = send_or_edit_final_message(final_message_text, button_markup, today_sheet_data, BOT_TOKEN, CHAT_ID, force_resend=any_changes_made)
        if final_message_data:
            update_sheet_data(sheet, "FINAL", date_dash, final_message_data)
        
        logging.info("🎉 اسکریپت با موفقیت اجرا شد.")

    except Exception as e:
        logging.error(f"❌ یک خطای پیش‌بینی نشده در اجرای اصلی رخ داد: {e}", exc_info=True)

if __name__ == "__main__":
    main()
