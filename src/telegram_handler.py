import telebot
import logging
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger("FearlessFutures.Telegram")

class TelegramHandler:
    def __init__(self, token, authorized_chat_id):
        self.bot = telebot.TeleBot(token, parse_mode="Markdown")
        self.authorized_chat_id = str(authorized_chat_id)

    def is_authorized(self, chat_id) -> bool:
        return str(chat_id) == self.authorized_chat_id

    def send(self, text: str, reply_markup=None):
        try:
            self.bot.send_message(
                self.authorized_chat_id,
                text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Send error: {e}")

    def reply_to(self, message, text: str, reply_markup=None):
        try:
            self.bot.reply_to(
                message,
                text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Reply error: {e}")

    def main_keyboard(self):
        kb = InlineKeyboardMarkup(row_width=3)
        kb.add(
            InlineKeyboardButton("📊 Status", callback_data="status"),
            InlineKeyboardButton("💰 Balance", callback_data="balance"),
            InlineKeyboardButton("📈 PnL", callback_data="pnl"),
        )
        kb.add(
            InlineKeyboardButton("🤖 Decide", callback_data="decide"),
            InlineKeyboardButton("❌ Close", callback_data="close"),
            InlineKeyboardButton("🌍 Macro", callback_data="macro"),
        )
        kb.add(
            InlineKeyboardButton("🧠 AI Log", callback_data="ai_log"),
            InlineKeyboardButton("📚 Lessons", callback_data="lessons"),
            InlineKeyboardButton("📓 History", callback_data="history"),
        )
        kb.add(
            InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
            InlineKeyboardButton("🔄 Auto: ON/OFF", callback_data="toggle"),
        )
        return kb
