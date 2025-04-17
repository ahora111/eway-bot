import datetime
import os
import json
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime


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


def get_message_id_from_sheet(today):
    sheet = service.spreadsheets()
    result = sheet.values().get(spreadsheetId=SHEET_ID, range=SHEET_RANGE).execute()
    rows = result.get("values", [])

    if not rows:
        return None

    headers = rows[0]
    for row in rows[1:]:
        record = dict(zip(headers, row))
        if record.get("تاریخ") == today:
            try:
                return int(record.get("شناسه پیام"))
            except (ValueError, TypeError):
                return None
    return None


today = datetime.now().strftime('%Y-%m-%d')
message_id = get_message_id_from_sheet(today)

# --- ذخیره message_id در شیت ---
def save_message_id_to_sheet(message_id):
    ws = get_worksheet()
    today = get_today()
    ws.append_row([today, message_id])  # ذخیره message_id جدید

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
