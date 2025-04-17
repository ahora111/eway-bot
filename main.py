import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime

# اتصال به گوگل شیت
def connect_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open("Telegram Messages").sheet1  # اسم شیت
    return sheet

# دریافت تاریخ امروز
def get_today():
    return datetime.datetime.now().strftime('%Y-%m-%d')

# ذخیره message_id در شیت
def save_message_id_to_sheet(message_id):
    sheet = connect_sheet()
    today = get_today()
    cell = sheet.find(today) if sheet.findall(today) else None
    if cell:
        sheet.update_cell(cell.row, cell.col + 1, message_id)
    else:
        sheet.append_row([today, message_id])
    print("Message ID saved to Google Sheet.")

# خواندن message_id از شیت
def get_message_id_from_sheet():
    sheet = connect_sheet()
    today = get_today()
    records = sheet.get_all_records()
    for row in records:
        if row['date'] == today:
            return row['message_id']
    return None
