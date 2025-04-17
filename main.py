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
            return int(row.get('message_id'))
    return None
    
print(f"Message ID دریافت‌شده از شیت: {message_id}")



# --- ذخیره message_id در شیت ---
def get_message_id_from_sheet():
    ws = get_worksheet()
    today = get_today()
    records = ws.get_all_records()
    message_ids = [int(row['message_id']) for row in records if str(row.get('تاریخ')) == today and row.get('message_id')]
    if message_ids:
        return message_ids[-1]  # آخرین آیدی
    return None
    
print(f"Message ID دریافت‌شده از شیت: {message_id}")

# --- متن نمونه ---
text = "✅ قیمت‌های امروز:\n- آیفون: 50 میلیون\n- سامسونگ: 30 میلیون"

# --- منطق اصلی ---
message_id = get_message_id_from_sheet()
if message_id:
    edit_telegram_message(message_id, text)
    print("پیام ویرایش شد.")
else:
    new_id = send_telegram_message(text)
    save_message_id_to_sheet(new_id)
    print("پیام جدید ارسال و ذخیره شد.")
