import datetime
import os
import json
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- تنظیمات ---
BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']
SPREADSHEET_ID = '1nMtYsaa9_ZSGrhQvjdVx91WSG4gANg2R0s4cSZAZu7E'
SHEET_NAME = 'Sheet1'  # نام شیت مورد نظر

# --- اتصال به Google Sheets ---
def get_worksheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # Load credentials from environment variable
    credentials_str = os.environ.get("GSHEET_CREDENTIALS_JSON")
    credentials_dict = json.loads(credentials_str)

    creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID)
    worksheet = sheet.worksheet(SHEET_NAME)
    return worksheet

# --- گرفتن تاریخ امروز ---
def get_today():
    return datetime.datetime.now().strftime('%Y-%m-%d')

# --- ارسال پیام تلگرام ---
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    })
    print("Telegram response:", response.text)
    return response.json().get("result", {}).get("message_id")

# --- ویرایش پیام تلگرام ---
def edit_telegram_message(message_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    response = requests.post(url, data={
        "chat_id": CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    })
    print("Edit response:", response.text)

# --- خواندن message_id امروز از شیت ---
def get_message_id_from_sheet():
    ws = get_worksheet()
    today = get_today()
    records = ws.get_all_records()
    for row in records:
        if row.get('تاریخ') == today or row.get('date') == today:
            message_id = row.get('message_id')
            if message_id:
                print(f"Message ID دریافت‌شده از شیت: {message_id}")
                return int(message_id)
    print("هیچ message_id برای امروز پیدا نشد.")
    return None

# --- ذخیره message_id در شیت ---
def save_message_id_to_sheet(message_id):
    ws = get_worksheet()
    today = get_today()
    records = ws.get_all_records()
    updated = False
    for i, row in enumerate(records):
        if row.get('تاریخ') == today or row.get('date') == today:
            ws.update_cell(i + 2, records[0].index('message_id') + 1, message_id)
            updated = True
            print(f"Message ID جدید {message_id} در شیت ذخیره شد.")
            break
    if not updated:
        ws.append_row([today, message_id])
        print(f"Message ID جدید {message_id} به شیت اضافه شد.")

# --- منطق اصلی ---
message_id = get_message_id_from_sheet()
text = "✅ قیمت‌های امروز:\n- آیفون: 50 میلیون\n- سامسونگ: 30 میلیون"

if message_id:
    print(f"Message ID برای ویرایش: {message_id}")
    edit_telegram_message(message_id, text)
    print("پیام ویرایش شد.")
else:
    new_id = send_telegram_message(text)
    save_message_id_to_sheet(new_id)
    print("پیام جدید ارسال و ذخیره شد.")

