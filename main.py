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
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

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

# --- خواندن message_id از شیت ---
def get_message_id_from_sheet():
    ws = get_worksheet()
    today = get_today()
    try:
        records = ws.get_all_records()
        for row in records:
            if row['date'] == today:
                return int(row['message_id'])
    except Exception as e:
        print("Error reading from sheet:", e)
    return None

# --- ذخیره message_id در شیت ---
def save_message_id_to_sheet(message_id):
    ws = get_worksheet()
    today = get_today()
    try:
        ws.append_row([today, message_id])
        print("Message ID saved to Google Sheet.")
    except Exception as e:
        print("Error writing to sheet:", e)

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
