import json
import datetime
import requests
import os

# دریافت توکن و شناسه چت از متغیرهای محیطی
BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']
MESSAGE_IDS_FILE = 'message_ids.json'

def load_message_ids():
    if os.path.exists(MESSAGE_IDS_FILE):
        with open(MESSAGE_IDS_FILE, 'r') as f:
            data = json.load(f)
            return data.get(get_today(), {})
    return {}

def save_message_ids(message_ids):
    if os.path.exists(MESSAGE_IDS_FILE):
        with open(MESSAGE_IDS_FILE, 'r') as f:
            data = json.load(f)
    else:
        data = {}

    data[get_today()] = message_ids

    try:
        with open(MESSAGE_IDS_FILE, 'w') as f:
            json.dump(data, f)
            print(f"File '{MESSAGE_IDS_FILE}' saved successfully.")
    except Exception as e:
        print(f"Error saving file: {e}")

def get_today():
    return datetime.datetime.now().strftime('%Y-%m-%d')

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    })
    print("Telegram response:", response.text)  # برای دیباگ
    return response.json().get("result", {}).get("message_id")

def edit_telegram_message(message_id, new_text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    response = requests.post(url, data={
        "chat_id": CHAT_ID,
        "message_id": message_id,
        "text": new_text,
        "parse_mode": "HTML"
    })
    print("Telegram response:", response.text)  # برای دیباگ

# پیام نمونه
text = "✅ قیمت‌های امروز:\n- آیفون: 50 میلیون\n- سامسونگ: 30 میلیون"

# بررسی آیا امروز پیام ارسال شده یا نه
message_ids = load_message_ids()

if not message_ids:
    # امروز هنوز پیام ارسال نشده
    msg_id = send_telegram_message(text)
    save_message_ids({"main": msg_id})
    print("پیام جدید ارسال شد.")
else:
    # پیام قبلاً ارسال شده، فقط ویرایش کن
    edit_telegram_message(message_ids["main"], text)
    print("پیام ویرایش شد.")
