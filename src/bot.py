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

logger = logging.getLogger("FearlessFutures.Bot")

class FearlessBot:
    def __init__(self, config):
        self.config = config
        self.storage = Storage()
        
        # Load saved state if exists
        saved_state = self.storage.load_state()
        initial_balance = saved_state['balance'] if saved_state else config['INITIAL_BALANCE']
        
        self.exchange = PaperExchange(
            initial_balance=initial_balance,
            symbol=config['SYMBOL'],
            timeframe=config['TIMEFRAME'],
            leverage=config['LEVERAGE'],
            exchange_key=config['BITGET_API_KEY'],
            exchange_secret=config['BITGET_SECRET_KEY'],
            exchange_passphrase=config['BITGET_PASSPHRASE']
        )
        
        if saved_state:
            self.exchange.position = saved_state['position']
            self.exchange.trade_log = saved_state['trade_log']

        self.ai = AIBrain(api_key=config['GROQ_API_KEY'])
        
        peak_balance = saved_state['peak_balance'] if saved_state else initial_balance
        self.risk = RiskManager(
            initial_balance=initial_balance,
            max_daily_trades=config['MAX_DAILY_TRADES'],
            max_drawdown_pct=config['MAX_DRAWDOWN_PCT']
        )
        self.risk.peak_balance = peak_balance

        self.telegram = TelegramHandler(
            token=config['TELEGRAM_BOT_TOKEN'],
            authorized_chat_id=config['YOUR_CHAT_ID']
        )
        
        self.monitor_interval = config['MONITOR_INTERVAL']
        self.min_confidence = config['MIN_CONFIDENCE']
        self.tp_pct = config['TP_PCT']
        self.sl_pct = config['SL_PCT']
        self.risk_per_trade = config['RISK_PER_TRADE']
        self.auto_trade = config.get('AUTO_TRADE', True)

    def save_current_state(self):
        self.storage.save_state(
            self.exchange.balance,
            self.exchange.position,
            self.exchange.trade_log,
            self.risk.peak_balance
        )

    def get_learning_context(self):
        last_trades = self.exchange.trade_log[-10:]
        if not last_trades:
            return ""
        wins = [t for t in last_trades if t['pnl'] > 0]
        losses = [t for t in last_trades if t['pnl'] <= 0]
        win_rate = len(wins)/len(last_trades)*100 if last_trades else 0
        avg_win = sum(t['pnl'] for t in wins)/len(wins) if wins else 0
        avg_loss = sum(t['pnl'] for t in losses)/len(losses) if losses else 0
        return f"""
Recent Performance (last {len(last_trades)} trades):
Wins: {len(wins)}, Losses: {len(losses)}, Win Rate: {win_rate:.0f}%
Avg Win: {avg_win:.2f}, Avg Loss: {avg_loss:.2f}
"""

    def execute_decision(self, decision_data, price):
        if self.exchange.position:
            return
        
        decision = decision_data.get("decision")
        confidence = decision_data.get("confidence", 0)

        if confidence < self.min_confidence:
            if not self.auto_trade:
                self.telegram.send_message(f"🔕 AI confidence {confidence:.1%} < {self.min_confidence:.0%}. No trade.")
            return

        risk_amount = self.exchange.balance * self.risk_per_trade
        stop_distance = price * self.sl_pct
        qty = risk_amount / (stop_distance * self.exchange.leverage)

        if decision == "LONG":
            tp = price * (1 + self.tp_pct)
            sl = price * (1 - self.sl_pct)
            side = "long"
        else:
            tp = price * (1 - self.tp_pct)
            sl = price * (1 + self.sl_pct)
            side = "short"

        success, msg = self.exchange.open_position(side, qty, tp, sl)
        if success:
            self.risk.trade_count_today += 1
            self.telegram.send_message(
                f"✅ **FULL AI TRADE**\n{msg}\nConfidence: {confidence:.1%}\nReason: {decision_data.get('reasoning', 'N/A')}"
            )
            self.save_current_state()
        else:
            self.telegram.send_message(f"❌ {msg}")

    def run_decide_cycle(self, silent=False):
        can, reason = self.risk.can_trade(self.exchange.balance)
        if not can:
            if not silent: self.telegram.send_message(f"⚠️ Trade blocked: {reason}")
            return
        if self.exchange.position:
            if not silent: self.telegram.send_message("You already have an open position.")
            return

        try:
            ticker = self.exchange.fetch_ticker()
            price = ticker['last']
            
            # Fetch data for multiple timeframes
            df_5m = self.exchange.fetch_ohlcv('5m', limit=50)
            df_1h = self.exchange.fetch_ohlcv('1h', limit=50)
            
            learning_context = self.get_learning_context()
            
            # Combine data for AI
            combined_data = f"5m RSI: {df_5m['rsi'].iloc[-1]:.2f}, 1h RSI: {df_1h['rsi'].iloc[-1]:.2f}\n"
            combined_data += f"5m SMA20/50: {df_5m['sma_20'].iloc[-1]:.2f}/{df_5m['sma_50'].iloc[-1]:.2f}\n"
            
            decision_data = self.ai.get_decision(
                price, df_5m, self.exchange.balance, False, "5m/1h", learning_context + "\nIndicators:\n" + combined_data
            )
            
            if not decision_data:
                if not silent: self.telegram.send_message("❌ AI call failed.")
                return
                
            self.execute_decision(decision_data, price)
        except Exception as e:
            logger.error(f"Decide cycle error: {e}")
            if not silent: self.telegram.send_message(f"❌ Error: {e}")

    def monitor_loop(self):
        while True:
            try:
                if self.exchange.position:
                    ticker = self.exchange.fetch_ticker()
                    price = ticker['last']
                    closed, reason, pnl = self.exchange.check_tp_sl(price)
                    if closed:
                        self.risk.update_peak(self.exchange.balance)
                        self.telegram.send_message(
                            f"💰 **Position Closed ({reason})**\nEntry: {self.exchange.trade_log[-1]['entry_price']:.2f}, Exit: {price:.2f}\nPnL: {pnl:.2f} USDT\nBalance: {self.exchange.balance:.2f}"
                        )
                        self.save_current_state()
                
                schedule.run_pending()
                time.sleep(self.monitor_interval)
            except Exception as e:
                logger.error(f"Monitor error: {e}")
                time.sleep(10)

    def start(self):
        logger.info("Starting Full AI Fearless Bot...")
        self.telegram.send_message("✅ Full AI Bot online. Autonomous trading active (Bitget).\n/help for commands.")
        
        # Schedule autonomous scans every hour
        schedule.every(1).hours.do(self.run_decide_cycle, silent=True)
        
        # Setup Telegram handlers
        @self.telegram.bot.message_handler(commands=['start', 'help'])
        def cmd_help(message):
            if not self.telegram.is_authorized(message.chat.id): return
            txt = """
🤖 *Fearless Futures Full AI Trader*
Commands:
/decide – Force an immediate AI analysis
/status – Current position & indicators
/close – Close current position
/balance – Show paper balance & history
/history – Last 5 closed trades
/toggle – Switch between Autonomous/Manual mode
"""
            self.telegram.reply_to(message, txt)

        @self.telegram.bot.message_handler(commands=['decide'])
        def cmd_decide(message):
            if not self.telegram.is_authorized(message.chat.id): return
            self.telegram.reply_to(message, "🧠 Full AI Analysis starting...")
            threading.Thread(target=self.run_decide_cycle).start()

        @self.telegram.bot.message_handler(commands=['status'])
        def cmd_status(message):
            if not self.telegram.is_authorized(message.chat.id): return
            try:
                ticker = self.exchange.fetch_ticker()
                curr = ticker['last']
                df_5m = self.exchange.fetch_ohlcv('5m', limit=2)
                rsi = df_5m['rsi'].iloc[-1]
                
                txt = f"📈 *Market Status*\nBTC: {curr:.2f} USDT\nRSI (5m): {rsi:.2f}\nMode: {'Autonomous' if self.auto_trade else 'Manual'}\n"
                
                if self.exchange.position:
                    pos = self.exchange.position
                    if pos['side'] == 'long':
                        unrealised = (curr - pos['entry_price']) * pos['quantity'] * self.exchange.leverage
                    else:
                        unrealised = (pos['entry_price'] - curr) * pos['quantity'] * self.exchange.leverage
                    txt += f"\n📊 *Open Position*\nSide: {pos['side'].upper()}\nEntry: {pos['entry_price']:.2f}\nTP: {pos['tp']:.2f} | SL: {pos['sl']:.2f}\nUnrealised: {unrealised:.2f} USDT"
                else:
                    txt += "\nNo open position."
                
                self.telegram.send_message(txt)
            except Exception as e:
                self.telegram.send_message(f"Error getting status: {e}")

        @self.telegram.bot.message_handler(commands=['toggle'])
        def cmd_toggle(message):
            if not self.telegram.is_authorized(message.chat.id): return
            self.auto_trade = not self.auto_trade
            self.telegram.send_message(f"🤖 Autonomous mode: {'ON' if self.auto_trade else 'OFF'}")

        @self.telegram.bot.message_handler(commands=['close'])
        def cmd_close(message):
            if not self.telegram.is_authorized(message.chat.id): return
            if not self.exchange.position:
                self.telegram.reply_to(message, "No open position.")
                return
            ticker = self.exchange.fetch_ticker()
            price = ticker['last']
            closed, msg = self.exchange.close_now(price)
            if closed:
                self.risk.update_peak(self.exchange.balance)
                self.telegram.send_message(f"🔒 Position closed: {msg}")
                self.save_current_state()
            else:
                self.telegram.send_message(f"❌ {msg}")

        @self.telegram.bot.message_handler(commands=['balance'])
        def cmd_balance(message):
            if not self.telegram.is_authorized(message.chat.id): return
            self.telegram.send_message(f"💵 Paper Balance: {self.exchange.balance:.2f} USDT")

        @self.telegram.bot.message_handler(commands=['history'])
        def cmd_history(message):
            if not self.telegram.is_authorized(message.chat.id): return
            trades = self.exchange.trade_log[-5:]
            if not trades:
                self.telegram.reply_to(message, "No closed trades yet.")
                return
            text = "📜 *Last Closed Trades*\n"
            for t in trades:
                text += f"- {t['side'].upper()} | Entry: {t['entry_price']:.2f} | Exit: {t['exit_price']:.2f} | PnL: {t['pnl']:.2f} USDT\n"
            self.telegram.send_message(text)

        # Start Telegram polling
        threading.Thread(target=self.telegram.bot.infinity_polling, daemon=True).start()
        
        # Start monitor loop
        self.monitor_loop()
