#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات استخراج قیمت محصولات از سایت همراه تل و ارسال به تلگرام
ویژگی‌ها:
- استخراج خودکار داده‌ها با Selenium
- دسته‌بندی هوشمند محصولات
- فرمت‌دهی حرفه‌ای پیام‌ها
- مدیریت پیام‌های تلگرام (ارسال/ویرایش)
- پشتیبانی از دکمه‌های اینلاین
- ذخیره‌سازی تاریخچه در Google Sheets
- پردازش قیمت‌ها با الگوریتم اختصاصی
"""

# ---------------------------- 📦 کتابخانه‌های مورد نیاز ----------------------------
import os
import re
import json
import time
import logging
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

import requests
import gspread
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from oauth2client.service_account import ServiceAccountCredentials
from persiantools.jdatetime import JalaliDate
from tenacity import retry, stop_after_attempt, wait_exponential

# ---------------------------- ⚙️ تنظیمات اولیه ----------------------------
# تنظیمات لاگ‌گیری
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("price_bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# تنظیمات محیطی (مقداردهی از طریق متغیرهای محیطی)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "YOUR_SHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "PriceData")

# ---------------------------- 🏷 مدل‌های داده ----------------------------
@dataclass
class Product:
    """مدل داده‌ای برای محصولات"""
    raw_name: str
    brand: str
    model: str
    price: float
    category: str = "other"
    
    def formatted_price(self) -> str:
        """فرمت‌دهی قیمت به صورت فارسی"""
        return f"{self.price:,.0f}".replace(",", "،")

@dataclass
class TelegramMessage:
    """مدل داده‌ای برای پیام‌های تلگرام"""
    category: str
    message_id: int
    content: str
    date: str

# ---------------------------- 🛠 ابزارهای کمکی ----------------------------
class PriceProcessor:
    """پردازشگر قیمت‌ها با الگوریتم اختصاصی"""
    
    @staticmethod
    def process_price(price: float) -> float:
        """
        اعمال فرمول محاسبه قیمت نهایی:
        - زیر 1 میلیون: قیمت ثابت
        - 1-7 میلیون: +260 هزار تومان
        - 7-10 میلیون: 3.5% افزایش
        - 10-20 میلیون: 2.5% افزایش
        - 20-30 میلیون: 2% افزایش
        - 30-40 میلیون: 1.5% افزایش
        - بالای 40 میلیون: 1.5% افزایش
        """
        if price <= 1_000_000:
            return price
        elif price <= 7_000_000:
            return price + 260_000
        elif price <= 10_000_000:
            return price * 1.035
        elif price <= 20_000_000:
            return price * 1.025
        elif price <= 30_000_000:
            return price * 1.02
        elif price <= 40_000_000:
            return price * 1.015
        else:
            return price * 1.015
    
    @staticmethod
    def round_price(price: float) -> float:
        """گرد کردن قیمت به مضرب 100 هزار تومان"""
        return round(price / 100_000) * 100_000

class PersianTextFormatter:
    """فرمت‌دهی متن‌های فارسی برای تلگرام"""
    
    @staticmethod
    def escape_markdown(text: str) -> str:
        """پاکسازی متن برای MarkdownV2 تلگرام"""
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return ''.join(f'\\{char}' if char in escape_chars else char for char in text)
    
    @staticmethod
    def format_date() -> str:
        """فرمت‌دهی تاریخ شمسی"""
        jdate = JalaliDate.today()
        weekday_map = {
            0: "شنبه",
            1: "یکشنبه",
            2: "دوشنبه",
            3: "سه‌شنبه",
            4: "چهارشنبه",
            5: "پنجشنبه",
            6: "جمعه"
        }
        return f"{weekday_map[jdate.weekday()]} {jdate.strftime('%Y/%m/%d')}"

# ---------------------------- 🔍 استخراج داده‌ها ----------------------------
class DataExtractor:
    """استخراج‌گر داده‌ها از وبسایت"""
    
    def __init__(self):
        self.driver = self._init_driver()
        self.valid_brands = [
            "Galaxy", "iPhone", "Redmi", "POCO", "Nartab", 
            "PlayStation", "لپ‌تاپ", "تبلت", "کنسول"
        ]
    
    def _init_driver(self):
        """تنظیمات اولیه Selenium WebDriver"""
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        return webdriver.Chrome(options=options)
    
    def extract_products(self, url: str) -> List[Product]:
        """استخراج محصولات از یک URL خاص"""
        self.driver.get(url)
        WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'product-item'))
        
        self._scroll_page()
        items = self.driver.find_elements(By.CLASS_NAME, 'product-item')
        return [self._parse_product(item) for item in items]
    
    def _scroll_page(self):
        """اسکرول صفحه برای بارگذاری تمام محصولات"""
        last_height = self.driver.execute_script("return document.body.scrollHeight")
        while True:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            new_height = self.driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height
    
    def _parse_product(self, item) -> Product:
        """پردازش هر آیتم محصول"""
        name = item.find_element(By.CLASS_NAME, 'product-name').text.strip()
        price_text = item.find_element(By.CLASS_NAME, 'product-price').text
        price = self._clean_price(price_text)
        
        brand, model = self._parse_brand_model(name)
        processed_price = PriceProcessor.process_price(price)
        rounded_price = PriceProcessor.round_price(processed_price)
        
        return Product(
            raw_name=name,
            brand=brand,
            model=model,
            price=rounded_price,
            category=self._detect_category(name)
        )
    
    def _clean_price(self, price_text: str) -> float:
        """تبدیل متن قیمت به عدد"""
        digits = re.sub(r"[^\d]", "", price_text)
        return float(digits) if digits else 0.0
    
    def _parse_brand_model(self, name: str) -> Tuple[str, str]:
        """تشخیص برند و مدل از نام محصول"""
        for brand in self.valid_brands:
            if brand in name:
                return brand, name.replace(brand, "").strip()
        return "سایر", name
    
    def _detect_category(self, name: str) -> str:
        """تشخیص دسته‌بندی محصول"""
        name_lower = name.lower()
        if "galaxy" in name_lower:
            return "samsung"
        elif "iphone" in name_lower:
            return "iphone"
        elif "laptop" in name_lower or "لپ‌تاپ" in name_lower:
            return "laptop"
        elif "tablet" in name_lower or "تبلت" in name_lower:
            return "tablet"
        elif "playstation" in name_lower or "کنسول" in name_lower:
            return "gaming"
        else:
            return "other"

# ---------------------------- ✉️ مدیریت تلگرام ----------------------------
class TelegramManager:
    """مدیریت تمام ارتباطات با تلگرام"""
    
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        
        # ایموجی‌های دسته‌بندی
        self.category_emojis = {
            "samsung": "🔵",
            "iphone": "🍏",
            "laptop": "💻",
            "tablet": "🟠",
            "gaming": "🎮",
            "other": "🟣"
        }
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def send_message(self, text: str, reply_markup: Optional[Dict] = None) -> Optional[int]:
        """ارسال پیام جدید به تلگرام"""
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": PersianTextFormatter.escape_markdown(text),
            "parse_mode": "MarkdownV2",
            "reply_markup": json.dumps(reply_markup) if reply_markup else None
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200 and response.json().get("ok"):
                return response.json()["result"]["message_id"]
            logging.error(f"خطا در ارسال پیام: {response.text}")
        except Exception as e:
            logging.error(f"خطا در ارتباط با تلگرام: {str(e)}")
        return None
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def edit_message(self, message_id: int, new_text: str) -> bool:
        """ویرایش پیام موجود در تلگرام"""
        url = f"{self.base_url}/editMessageText"
        payload = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "text": PersianTextFormatter.escape_markdown(new_text),
            "parse_mode": "MarkdownV2"
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200 and response.json().get("ok")
        except Exception as e:
            logging.error(f"خطا در ویرایش پیام: {str(e)}")
            return False
    
    def create_inline_buttons(self, message_ids: Dict[str, int]) -> Dict:
        """ساخت دکمه‌های اینلاین برای دسته‌بندی‌ها"""
        keyboard = []
        for cat, msg_id in message_ids.items():
            if cat in self.category_emojis:
                keyboard.append([{
                    "text": f"{self.category_emojis[cat]} لیست {cat}",
                    "url": f"https://t.me/c/{self.chat_id[4:]}/{msg_id}"
                }])
        return {"inline_keyboard": keyboard} if keyboard else None
    
    def prepare_product_message(self, products: List[Product], category: str) -> str:
        """آماده‌سازی پیام نهایی برای هر دسته‌بندی"""
        if not products:
            return ""
            
        header = (
            f"🗓 بروزرسانی {PersianTextFormatter.format_date()}\n"
            f"✅ لیست قیمت محصولات\n\n"
            f"⬅️ موجودی {self._get_category_name(category)} ➡️\n\n"
        )
        
        products_str = []
        for product in products:
            products_str.append(
                f"{self.category_emojis.get(product.category, '🟣')} {product.brand} {product.model}\n"
                f"💰 قیمت: {product.formatted_price()} تومان"
            )
        
        footer = (
            "\n\n☎️ تماس:\n"
            "📞 09371111558\n"
            "📞 02833991417"
        )
        
        return header + "\n\n".join(products_str) + footer
    
    def _get_category_name(self, category: str) -> str:
        """دریافت نام فارسی دسته‌بندی"""
        names = {
            "samsung": "سامسونگ",
            "iphone": "آیفون",
            "laptop": "لپ‌تاپ",
            "tablet": "تبلت",
            "gaming": "کنسول بازی",
            "other": "متفرقه"
        }
        return names.get(category, "محصولات")

# ---------------------------- 📊 مدیریت Google Sheets ----------------------------
class SheetsManager:
    """مدیریت ارتباط با Google Sheets"""
    
    def __init__(self, spreadsheet_id: str, sheet_name: str):
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        self.client = self._authenticate()
    
    def _authenticate(self):
        """احراز هویت با Google Sheets API"""
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            json.loads(os.getenv("GOOGLE_CREDS_JSON")), scope)
        return gspread.authorize(creds)
    
    def get_sheet(self):
        """دریافت شیء صفحه مورد نظر"""
        try:
            sheet = self.client.open_by_key(self.spreadsheet_id)
            return sheet.worksheet(self.sheet_name)
        except Exception as e:
            logging.error(f"خطا در دریافت صفحه: {str(e)}")
            return None
    
    def save_message_data(self, message_data: TelegramMessage) -> bool:
        """ذخیره اطلاعات پیام در Sheets"""
        try:
            sheet = self.get_sheet()
            if not sheet:
                return False
                
            sheet.append_row([
                message_data.date,
                str(message_data.message_id),
                message_data.category,
                message_data.content
            ])
            return True
        except Exception as e:
            logging.error(f"خطا در ذخیره داده: {str(e)}")
            return False
    
    def get_last_message_data(self, category: str) -> Optional[TelegramMessage]:
        """دریافت آخرین پیام ذخیره شده برای یک دسته‌بندی"""
        try:
            sheet = self.get_sheet()
            if not sheet:
                return None
                
            records = sheet.get_all_records()
            for record in reversed(records):
                if record["category"] == category:
                    return TelegramMessage(
                        category=record["category"],
                        message_id=int(record["message_id"]),
                        content=record["content"],
                        date=record["date"]
                    )
            return None
        except Exception as e:
            logging.error(f"خطا در خواندن داده: {str(e)}")
            return None

# ---------------------------- 🤖 کلاس اصلی ربات ----------------------------
class PriceBot:
    """کلاس اصلی ربات مدیریت قیمت‌ها"""
    
    def __init__(self):
        self.extractor = DataExtractor()
        self.telegram = TelegramManager(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        self.sheets = SheetsManager(SPREADSHEET_ID, SHEET_NAME)
        
        # URL‌های مورد نظر برای استخراج
        self.target_urls = {
            "mobile": "https://hamrahtel.com/mobiles",
            "laptop": "https://hamrahtel.com/laptops",
            "tablet": "https://hamrahtel.com/tablets",
            "gaming": "https://hamrahtel.com/gaming"
        }
    
    def run(self):
        """روال اصلی اجرای ربات"""
        try:
            # 1. استخراج محصولات از تمام دسته‌بندی‌ها
            all_products = []
            for category, url in self.target_urls.items():
                logging.info(f"در حال استخراج محصولات از {category}...")
                products = self.extractor.extract_products(url)
                all_products.extend(products)
                time.sleep(3)  # فاصله بین درخواست‌ها
            
            # 2. دسته‌بندی محصولات
            categorized = self._categorize_products(all_products)
            
            # 3. بررسی تغییرات و تصمیم‌گیری برای ارسال/ویرایش
            today = JalaliDate.today().strftime("%Y-%m-%d")
            message_ids = {}
            
            for category, products in categorized.items():
                if not products:
                    continue
                    
                # آماده‌سازی پیام
                message = self.telegram.prepare_product_message(products, category)
                
                # بررسی پیام قبلی
                last_message = self.sheets.get_last_message_data(category)
                
                if last_message:
                    # ویرایش پیام موجود
                    if self.telegram.edit_message(last_message.message_id, message):
                        logging.info(f"پیام {category} با موفقیت ویرایش شد")
                else:
                    # ارسال پیام جدید
                    message_id = self.telegram.send_message(message)
                    if message_id:
                        message_ids[category] = message_id
                        self.sheets.save_message_data(TelegramMessage(
                            category=category,
                            message_id=message_id,
                            content=message,
                            date=today
                        ))
                        logging.info(f"پیام جدید برای {category} ارسال شد")
            
            # 4. ارسال پیام نهایی با دکمه‌های دسته‌بندی
            if message_ids:
                self._send_final_message(message_ids)
            
            logging.info("✅ پردازش با موفقیت انجام شد")
        
        except Exception as e:
            logging.error(f"❌ خطا در اجرای ربات: {str(e)}", exc_info=True)
        finally:
            self.extractor.driver.quit()
    
    def _categorize_products(self, products: List[Product]) -> Dict[str, List[Product]]:
        """دسته‌بندی محصولات بر اساس نوع"""
        categorized = {
            "samsung": [],
            "iphone": [],
            "laptop": [],
            "tablet": [],
            "gaming": [],
            "other": []
        }
        
        for product in products:
            categorized[product.category].append(product)
        
        # مرتب‌سازی بر اساس قیمت
        for category in categorized:
            categorized[category].sort(key=lambda x: x.price)
        
        return categorized
    
    def _send_final_message(self, message_ids: Dict[str, int]):
        """ارسال پیام نهایی با دکمه‌های دسترسی سریع"""
        final_text = (
            "✅ لیست قیمت‌های به‌روزرسانی شده:\n\n"
            "برای مشاهده جزئیات هر دسته روی دکمه مربوطه کلیک کنید.\n\n"
            "⏰ زمان ثبت سفارش: تا ساعت 22 شب\n"
            "🚚 تحویل: روز بعد از 9 صبح"
        )
        
        buttons = self.telegram.create_inline_buttons(message_ids)
        self.telegram.send_message(final_text, buttons)

# ---------------------------- 🚀 اجرای ربات ----------------------------
if __name__ == "__main__":
    bot = PriceBot()
    bot.run()
