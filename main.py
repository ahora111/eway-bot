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

# --- Configuration ---
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = 'Sheet1'
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
# CRITICAL: Move API token to an environment variable
NAMINET_API_TOKEN = os.getenv("NAMINET_API_TOKEN")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def fetch_products_json():
    """
    Fetches product data from the Naminet API.
    Returns parsed JSON data or None on failure.
    """
    if not NAMINet_API_TOKEN:
        logging.error("❌ Naminet API token (NAMINET_API_TOKEN) not found in environment variables.")
        return None

    url = "https://panel.naminet.co/api/catalog/productGroupsAttrNew?term="
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {NAMINet_API_TOKEN}"
    }

    try:
        response = requests.get(url, headers=headers, verify=False, timeout=30)
        response.raise_for_status()  # Raises an exception for bad status codes (4xx or 5xx)
        
        logging.info("✅ Successfully fetched data from API. Status: %s", response.status_code)
        return response.json()
        
    except requests.exceptions.HTTPError as http_err:
        logging.error("❌ HTTP error occurred: %s - Response: %s", http_err, response.text[:500])
    except requests.exceptions.RequestException as req_err:
        logging.error("❌ A request error occurred: %s", req_err)
    except json.JSONDecodeError:
        logging.error("❌ Failed to decode JSON from response: %s", response.text[:500])
        
    return None

def extract_products(data):
    """Extracts and formats product information from the raw API data."""
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
                    "price": f"{int(price):,}" # Format price with commas
                })
    return products

def escape_special_characters(text):
    """Escapes special characters for Telegram's MarkdownV2 parse mode."""
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

def split_message_respecting_groups(message, max_length=4000):
    """
    Splits a long message into parts, ensuring that product groups
    (lines starting with an emoji) are not broken up.
    """
    lines = message.split('\n')
    parts = []
    current_part = ""
    
    for line in lines:
        # If adding the next line exceeds the limit, save the current part
        if len(current_part) + len(line) + 1 > max_length:
            if current_part:
                parts.append(current_part.strip())
            current_part = line + '\n'
        else:
            current_part += line + '\n'
            
    # Add the last remaining part
    if current_part.strip():
        parts.append(current_part.strip())
        
    return parts

def get_current_time_and_date():
    """Returns the current Jalali date, Farsi weekday, and time in Tehran."""
    iran_tz = timezone('Asia/Tehran')
    now = datetime.now(iran_tz)
    
    jalali_today = JalaliDate(now)
    date_str_slash = jalali_today.strftime("%Y/%m/%d")
    date_str_dash = jalali_today.strftime("%Y-%m-%d")
    time_str = now.strftime('%H:%M')
    
    weekday_map = {
        0: "شنبه💪", 1: "یکشنبه😃", 2: "دوشنبه☺️", 3: "سه‌شنبه🥱",
        4: "چهارشنبه😕", 5: "پنج‌شنبه🥳", 6: "جمعه😎"
    }
    weekday_farsi = weekday_map[jalali_today.weekday()]
    
    return date_str_slash, date_str_dash, time_str, weekday_farsi

def prepare_category_message(category_lines, update_date_str, time_str, weekday_farsi):
    """Prepares the message content for a specific category."""
    update_date_formatted = f"{weekday_farsi} {update_date_str}"
    
    header = (
        f"🗓 بروزرسانی {update_date_formatted} 🕓 ساعت: {time_str}\n"
        f"✅ لیست پخش موبایل اهورا\n\n"
    )
    
    # The emoji will be part of the lines already, so we just join them.
    content = "\n".join(category_lines)
    
    footer = "\n\n☎️ شماره های تماس :\n📞 09371111558\n📞 02833991417"
    
    return f"{header}{content}{footer}"

def get_credentials():
    """Decodes Google Sheets credentials from a base64 env var."""
    encoded_creds = os.getenv("GSHEET_CREDENTIALS_JSON")
    if not encoded_creds:
        raise ValueError("❌ Google Sheets credentials (GSHEET_CREDENTIALS_JSON) not found.")
    
    decoded_creds = base64.b64decode(encoded_creds)
    # Using a temporary file is a standard pattern for libraries that expect a file path.
    temp_path = "/tmp/gsheet_creds.json"
    with open(temp_path, "wb") as f:
        f.write(decoded_creds)
    return temp_path

def connect_to_sheet():
    """Connects to Google Sheets and returns the worksheet object."""
    creds_path = get_credentials()
    scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets', "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
    logging.info("✅ Successfully connected to Google Sheet.")
    return sheet

def check_and_create_headers(sheet):
    """Ensures the sheet has the correct headers."""
    try:
        first_row = sheet.row_values(1)
    except gspread.exceptions.APIError:
        first_row = []
        
    headers = ["emoji", "date", "part", "message_id", "text"]
    if first_row != headers:
        sheet.insert_row(headers, 1)
        logging.info("✅ Headers created in the sheet.")
    else:
        logging.info("🔄 Headers already exist.")

def load_sheet_data(sheet, date_str_dash):
    """Loads today's message data from the sheet for efficient lookup."""
    records = sheet.get_all_records()
    today_data = {}
    for row in records:
        # Filter for today's date to reduce memory usage
        if row.get("date") == date_str_dash:
            emoji = row.get("emoji")
            if emoji:
                # Use a dictionary for easier lookup
                if emoji not in today_data:
                    today_data[emoji] = {}
                today_data[emoji][int(row["part"])] = {
                    "message_id": row["message_id"],
                    "text": row["text"]
                }
    return today_data

def update_sheet_data(sheet, emoji, date_str_dash, new_messages):
    """Deletes old rows for the day and appends the new message data."""
    # Find all rows for the given emoji and date to delete them
    cell_list = sheet.findall(emoji, in_column=1)
    date_cell_list = sheet.findall(date_str_dash, in_column=2)
    
    rows_to_delete = []
    # Find intersection of rows
    emoji_rows = {cell.row for cell in cell_list}
    date_rows = {cell.row for cell in date_cell_list}
    
    for row_num in sorted(list(emoji_rows.intersection(date_rows)), reverse=True):
        sheet.delete_rows(row_num)
        
    # Append new rows
    rows_to_add = []
    for part, (message_id, text) in enumerate(new_messages, 1):
        if message_id: # Only add if the message was sent successfully
            rows_to_add.append([emoji, date_str_dash, part, message_id, text])
    
    if rows_to_add:
        sheet.append_rows(rows_to_add)
    logging.info(f"🔄 Sheet updated for emoji '{emoji}'.")


def send_telegram_message(text, bot_token, chat_id, reply_markup=None):
    """Sends a new message to Telegram."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    params = {
        "chat_id": chat_id,
        "text": escape_special_characters(text),
        "parse_mode": "MarkdownV2"
    }
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
        
    response = requests.post(url, json=params)
    if response.ok:
        message_id = response.json()["result"]["message_id"]
        logging.info("✅ Message sent successfully. ID: %s", message_id)
        return message_id
    else:
        logging.error("❌ Error sending message: %s", response.text)
        return None

def edit_telegram_message(message_id, text, bot_token, chat_id, reply_markup=None):
    """Edits an existing Telegram message."""
    url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
    params = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": escape_special_characters(text),
        "parse_mode": "MarkdownV2"
    }
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
        
    response = requests.post(url, json=params)
    if response.ok:
        logging.info("✅ Message %s edited successfully.", message_id)
        return True
    else:
        # Ignore "message is not modified" error, as it's not a failure
        if "message is not modified" not in response.text:
            logging.warning("⚠️ Could not edit message %s: %s", message_id, response.text)
        return False

def delete_telegram_message(message_id, bot_token, chat_id):
    """Deletes a Telegram message."""
    url = f"https://api.telegram.org/bot{bot_token}/deleteMessage"
    params = {"chat_id": chat_id, "message_id": message_id}
    response = requests.post(url, json=params)
    if response.ok:
        logging.info("🗑️ Message %s deleted successfully.", message_id)
        return True
    else:
        logging.warning("⚠️ Could not delete message %s: %s", message_id, response.text)
        return False

def process_category_messages(emoji, new_messages, today_sheet_data, bot_token, chat_id):
    """
    Compares new messages with old ones from the sheet, and sends, edits,
    or deletes them accordingly. Returns a list of the final message IDs and
    a flag indicating if any changes were made.
    """
    prev_msgs = today_sheet_data.get(emoji, {})
    final_message_ids = []
    changes_made = False

    # Iterate through new messages and compare with old ones
    for i, new_msg_text in enumerate(new_messages, 1):
        message_id = None
        if i in prev_msgs:
            # An old message for this part exists
            old_msg = prev_msgs[i]
            if old_msg["text"] != new_msg_text:
                # Text has changed, try to edit
                changes_made = True
                if edit_telegram_message(old_msg["message_id"], new_msg_text, bot_token, chat_id):
                    message_id = old_msg["message_id"]
                else:
                    # Editing failed (e.g., message too old), send a new one
                    delete_telegram_message(old_msg["message_id"], bot_token, chat_id)
                    message_id = send_telegram_message(new_msg_text, bot_token, chat_id)
            else:
                # Text is the same, no change needed
                message_id = old_msg["message_id"]
        else:
            # This is a new message part, send it
            changes_made = True
            message_id = send_telegram_message(new_msg_text, bot_token, chat_id)
        
        if message_id:
            final_message_ids.append((message_id, new_msg_text))

    # Delete any old message parts that are no longer needed
    for part_num, old_msg in prev_msgs.items():
        if part_num > len(new_messages):
            changes_made = True
            delete_telegram_message(old_msg["message_id"], bot_token, chat_id)

    return final_message_ids, changes_made
    
def send_or_edit_final_message(final_message_text, button_markup, today_sheet_data, bot_token, chat_id, force_resend=False):
    """
    Manages the final summary message with buttons. Edits if possible,
    sends a new one otherwise.
    """
    prev_final_msg_data = today_sheet_data.get("FINAL", {}).get(1)

    if prev_final_msg_data:
        # A final message already exists for today
        message_id = prev_final_msg_data["message_id"]
        prev_text = prev_final_msg_data["text"]

        if prev_text == final_message_text and not force_resend:
            logging.info("🔁 Final message is unchanged. No action needed.")
            return [(message_id, final_message_text)]

        # Try to edit the existing final message
        if edit_telegram_message(message_id, final_message_text, bot_token, chat_id, button_markup):
             return [(message_id, final_message_text)]
        else:
            # If editing fails, delete the old one before sending a new one
            delete_telegram_message(message_id, bot_token, chat_id)

    # Send a new final message
    logging.info("🚀 Sending a new final message.")
    new_message_id = send_telegram_message(final_message_text, bot_token, chat_id, button_markup)
    if new_message_id:
        return [(new_message_id, final_message_text)]
    return []

def main():
    """Main execution function."""
    # Check for essential environment variables first
    if not all([SPREADSHEET_ID, BOT_TOKEN, CHAT_ID, NAMINet_API_TOKEN]):
        logging.error("❌ One or more required environment variables are missing. Exiting.")
        return

    try:
        # 1. Fetch and process data
        json_data = fetch_products_json()
        if not json_data:
            logging.warning("⚠️ No data fetched from API. Exiting.")
            return

        products = extract_products(json_data)
        if not products:
            logging.info("✅ No products are currently available. Exiting.")
            return

        # 2. Get current time and date info
        date_slash, date_dash, current_time, weekday_farsi = get_current_time_and_date()

        # 3. Connect to Google Sheets and load today's data
        sheet = connect_to_sheet()
        check_and_create_headers(sheet)
        today_sheet_data = load_sheet_data(sheet, date_dash)

        # 4. Categorize products and prepare messages
        emoji_map = {
            "گوشی سامسونگ": "🔵", "گوشی شیائومی": "🟡", "گوشی آیفون": "🍏",
            "تبلت": "🟠", "لپ تاپ": "💻"
        }
        categorized_lines = {}
        for p in products:
            emoji = emoji_map.get(p["category"], "🟣") # Default emoji for others
            line = f"{emoji} {p['product']} | {p['color']} | {p['price']} تومان"
            categorized_lines.setdefault(emoji, []).append(line)
        
        any_changes_made = False
        all_message_ids_for_buttons = {}

        # 5. Process each category
        for emoji, lines in categorized_lines.items():
            full_category_message = prepare_category_message(lines, date_slash, current_time, weekday_farsi)
            message_parts = split_message_respecting_groups(full_category_message)
            
            final_ids, changed = process_category_messages(emoji, message_parts, today_sheet_data, BOT_TOKEN, CHAT_ID)
            
            if changed:
                any_changes_made = True
                update_sheet_data(sheet, emoji, date_dash, final_ids)
            
            # Store the message IDs to create buttons later
            all_message_ids_for_buttons[emoji] = [msg_id for msg_id, _ in final_ids]

        # 6. Prepare and send/edit the final summary message
        final_message_text = (
            "✅ لیست گوشی و سایر کالاهای بالا بروز میباشد. ثبت خرید تا ساعت 10:30 شب انجام میشود و تحویل کالا ساعت 11:30 صبح روز بعد می باشد.\n\n"
            "✅اطلاعات واریز\n"
            "🔷 شماره شبا : IR970560611828006154229701\n"
a           "🔷 شماره کارت : 6219861812467917\n"
            "🔷 بلو بانک   حسین گرئی\n\n"
            "⭕️ حتما رسید واریز به ایدی تلگرام زیر ارسال شود .\n"
            "🆔 @lhossein1\n\n"
            "✅شماره تماس ثبت سفارش :\n"
            "📞 09371111558\n"
            "📞 09386373926\n"
            "📞 09308529712\n"
            "📞 028-3399-1417"
        )

        button_labels = {
            "🔵": "📱 لیست سامسونگ", "🟡": "📱 لیست شیائومی", "🍏": "📱 لیست آیفون",
            "🟣": "📱 لیست سایر برندها", "🟠": "📱 لیست تبلت", "💻": "💻 لیست لپ‌تاپ"
        }
        
        # Ensure channel ID is numeric for URL building
        channel_numeric_id = CHAT_ID.replace('-100', '')
        
        inline_keyboard = []
        for emoji, label in button_labels.items():
            if emoji in all_message_ids_for_buttons and all_message_ids_for_buttons[emoji]:
                # Link to the first message of the category
                first_message_id = all_message_ids_for_buttons[emoji][0]
                url = f"https://t.me/c/{channel_numeric_id}/{first_message_id}"
                inline_keyboard.append([{"text": label, "url": url}])
        
        button_markup = {"inline_keyboard": inline_keyboard}

        final_message_data = send_or_edit_final_message(
            final_message_text, button_markup, today_sheet_data, BOT_TOKEN, CHAT_ID, force_resend=any_changes_made
        )

        if final_message_data:
             update_sheet_data(sheet, "FINAL", date_dash, final_message_data)
        
        logging.info("🎉 Script finished successfully.")

    except Exception as e:
        logging.error(f"❌ An unexpected error occurred in the main function: {e}", exc_info=True)

if __name__ == "__main__":
    main()
