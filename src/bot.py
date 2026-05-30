import logging
import pandas as pd
import threading
import time
import schedule
from .exchange import PaperExchange
from .ai_brain import AIBrain
from .risk_manager import RiskManager
from .telegram_handler import TelegramHandler
from .storage import Storage
from .news import NewsFetcher

logger = logging.getLogger("FearlessFutures.Bot")

class FearlessBot:
    def __init__(self, config):
        self.config = config
        self.storage = Storage()
        saved_state = self.storage.load_state()
        initial_balance = saved_state['balance'] if saved_state else config['INITIAL_BALANCE']
        
        self.exchange = PaperExchange(
            initial_balance=initial_balance,
            timeframe=config['TIMEFRAME'],
            leverage=config['LEVERAGE'],
            exchange_key=config['BITGET_API_KEY'],
            exchange_secret=config['BITGET_SECRET_KEY'],
            exchange_passphrase=config['BITGET_PASSPHRASE']
        )
        
        self.lessons = []
        if saved_state:
            self.exchange.position = saved_state['position']
            self.exchange.trade_log = saved_state['trade_log']
            self.lessons = saved_state.get('lessons', [])

        self.ai = AIBrain(api_key=config['GROQ_API_KEY'])
        self.news_fetcher = NewsFetcher()
        self.risk = RiskManager(initial_balance=initial_balance, max_daily_trades=config['MAX_DAILY_TRADES'], max_drawdown_pct=config['MAX_DRAWDOWN_PCT'])
        if saved_state: self.risk.peak_balance = saved_state['peak_balance']

        self.telegram = TelegramHandler(token=config['TELEGRAM_BOT_TOKEN'], authorized_chat_id=config['YOUR_CHAT_ID'])
        self.symbols = config['SYMBOLS']
        self.monitor_interval = config['MONITOR_INTERVAL']
        self.min_confidence = config['MIN_CONFIDENCE']
        self.tp_pct = config['TP_PCT']
        self.sl_pct = config['SL_PCT']
        self.risk_per_trade = config['RISK_PER_TRADE']
        self.auto_trade = config.get('AUTO_TRADE', True)

    def save_current_state(self):
        self.storage.save_state(self.exchange.balance, self.exchange.position, self.exchange.trade_log, self.risk.peak_balance, self.lessons)

    def run_decide_cycle(self, silent=False):
        if self.exchange.position: return
        can, reason = self.risk.can_trade(self.exchange.balance)
        if not can:
            if not silent: self.telegram.send_message(f"⚠️ Blocked: {reason}")
            return

        best_decision = None
        best_symbol = None
        best_price = None

        try:
            news = self.news_fetcher.fetch_latest_news()
            lessons_str = "\n".join([f"- {l}" for l in self.lessons[-5:]])
            
            for symbol in self.symbols:
                ticker = self.exchange.fetch_ticker(symbol)
                price = ticker['last']
                df_5m = self.exchange.fetch_ohlcv(symbol, '5m', limit=50)
                df_1h = self.exchange.fetch_ohlcv(symbol, '1h', limit=50)
                order_book = self.exchange.fetch_order_book(symbol)
                
                combined_data = f"5m RSI: {df_5m['rsi'].iloc[-1]:.2f}, 1h RSI: {df_1h['rsi'].iloc[-1]:.2f}\n"
                combined_data += f"5m SMA20/50: {df_5m['sma_20'].iloc[-1]:.2f}/{df_5m['sma_50'].iloc[-1]:.2f}\n"
                
                decision_data = self.ai.get_decision(price, df_5m, self.exchange.balance, f"{symbol} 5m/1h", combined_data, lessons_str, order_book, news)
                
                if decision_data and decision_data.get('confidence', 0) >= self.min_confidence:
                    if not best_decision or decision_data['confidence'] > best_decision['confidence']:
                        best_decision = decision_data
                        best_symbol = symbol
                        best_price = price
            
            if best_decision:
                self.execute_decision(best_symbol, best_decision, best_price)
            elif not silent:
                self.telegram.send_message("🔍 Scanned all pairs. No high-confidence setups found.")
        except Exception as e:
            logger.error(f"Decide cycle error: {e}")
            if not silent: self.telegram.send_message(f"❌ Error: {e}")

    def execute_decision(self, symbol, decision_data, price):
        decision = decision_data.get("decision")
        risk_amount = self.exchange.balance * self.risk_per_trade
        stop_distance = price * self.sl_pct
        qty = risk_amount / (stop_distance * self.exchange.leverage)
        tp, sl, side = (price * (1 + self.tp_pct), price * (1 - self.sl_pct), "long") if decision == "LONG" else (price * (1 - self.tp_pct), price * (1 + self.sl_pct), "short")
        
        success, msg = self.exchange.open_position(symbol, side, qty, tp, sl)
        if success:
            self.risk.trade_count_today += 1
            self.telegram.send_message(f"💎 **QUANT AI TRADE ({symbol})**\n{msg}\nConfidence: {decision_data['confidence']:.1%}\nReason: {decision_data.get('reasoning', 'N/A')}")
            self.save_current_state()

    def monitor_loop(self):
        while True:
            try:
                if self.exchange.position:
                    pos = self.exchange.position
                    ticker = self.exchange.fetch_ticker(pos['symbol'])
                    closed, reason, pnl = self.exchange.check_tp_sl(ticker['last'])
                    if closed:
                        lesson = self.ai.reflect_on_trade(self.exchange.trade_log[-1])
                        self.lessons.append(lesson)
                        self.risk.update_peak(self.exchange.balance)
                        self.telegram.send_message(f"💰 **Closed {pos['symbol']} ({reason})**\nPnL: {pnl:.2f} USDT\n🧠 **Reflection:** {lesson}")
                        self.save_current_state()
                schedule.run_pending()
                time.sleep(self.monitor_interval)
            except Exception as e:
                logger.error(f"Monitor error: {e}")
                time.sleep(10)

    def start(self):
        self.telegram.send_message(f"💎 **Multi-Asset Quant AI Active.**\nScanning: {', '.join(self.symbols)}\n/help for commands.")
        schedule.every(1).hours.do(self.run_decide_cycle, silent=True)
        
        @self.telegram.bot.message_handler(commands=['start', 'help'])
        def cmd_help(message):
            if self.telegram.is_authorized(message.chat.id): self.telegram.reply_to(message, "🤖 *Fearless Quant AI*\n/decide | /status | /close | /balance | /history | /lessons | /toggle")

        @self.telegram.bot.message_handler(commands=['lessons'])
        def cmd_lessons(message):
            if self.telegram.is_authorized(message.chat.id): self.telegram.send_message("🧠 *Lessons*\n" + "\n".join([f"- {l}" for l in self.lessons[-5:]]) if self.lessons else "No lessons.")

        @self.telegram.bot.message_handler(commands=['decide'])
        def cmd_decide(message):
            if self.telegram.is_authorized(message.chat.id):
                self.telegram.reply_to(message, f"💎 Scanning {len(self.symbols)} pairs for the best opportunity...")
                threading.Thread(target=self.run_decide_cycle).start()

        @self.telegram.bot.message_handler(commands=['status'])
        def cmd_status(message):
            if not self.telegram.is_authorized(message.chat.id): return
            if self.exchange.position:
                pos = self.exchange.position
                ticker = self.exchange.fetch_ticker(pos['symbol'])
                self.telegram.send_message(f"📊 *Position: {pos['symbol']}*\nSide: {pos['side'].upper()}\nPnL: {(ticker['last']-pos['entry_price'])*pos['quantity']*self.exchange.leverage if pos['side']=='long' else (pos['entry_price']-ticker['last'])*pos['quantity']*self.exchange.leverage:.2f} USDT")
            else:
                self.telegram.send_message(f"🔍 No open position. Watchlist: {', '.join(self.symbols)}")

        @self.telegram.bot.message_handler(commands=['toggle'])
        def cmd_toggle(message):
            if self.telegram.is_authorized(message.chat.id):
                self.auto_trade = not self.auto_trade
                self.telegram.send_message(f"🤖 Auto: {'ON' if self.auto_trade else 'OFF'}")

        @self.telegram.bot.message_handler(commands=['close'])
        def cmd_close(message):
            if self.telegram.is_authorized(message.chat.id) and self.exchange.position:
                ticker = self.exchange.fetch_ticker(self.exchange.position['symbol'])
                closed, msg = self.exchange.close_now(ticker['last'])
                if closed:
                    self.risk.update_peak(self.exchange.balance)
                    self.telegram.send_message(f"🔒 Closed: {msg}")
                    self.save_current_state()

        @self.telegram.bot.message_handler(commands=['balance'])
        def cmd_balance(message):
            if self.telegram.is_authorized(message.chat.id): self.telegram.send_message(f"💵 Balance: {self.exchange.balance:.2f} USDT")

        @self.telegram.bot.message_handler(commands=['history'])
        def cmd_history(message):
            if self.telegram.is_authorized(message.chat.id) and self.exchange.trade_log:
                self.telegram.send_message("📜 *History*\n" + "\n".join([f"- {t['symbol']} {t['side'].upper()} | PnL: {t['pnl']:.2f}" for t in self.exchange.trade_log[-5:]]))

        threading.Thread(target=self.telegram.bot.infinity_polling, daemon=True).start()
        self.monitor_loop()
