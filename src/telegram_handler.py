import telebot
import logging

logger = logging.getLogger("FearlessFutures.Telegram")

class TelegramHandler:
    def __init__(self, token, authorized_chat_id):
        self.bot = telebot.TeleBot(token)
        self.authorized_chat_id = str(authorized_chat_id)

    def is_authorized(self, chat_id):
        return str(chat_id) == self.authorized_chat_id

    def send_message(self, text, parse_mode="Markdown"):
        try:
            self.bot.send_message(self.authorized_chat_id, text, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"Telegram send error: {e}")

    def reply_to(self, message, text, parse_mode="Markdown"):
        try:
            self.bot.reply_to(message, text, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"Telegram reply error: {e}")
