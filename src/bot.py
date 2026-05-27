import logging
import pandas as pd
import threading
import time
from .exchange import PaperExchange
from .ai_brain import AIBrain
from .risk_manager import RiskManager
from .telegram_handler import TelegramHandler

logger = logging.getLogger("FearlessFutures.Bot")

class FearlessBot:
    def __init__(self, config):
        self.config = config
        self.exchange = PaperExchange(
            initial_balance=config['INITIAL_BALANCE'],
            symbol=config['SYMBOL'],
            timeframe=config['TIMEFRAME'],
            leverage=config['LEVERAGE'],
            exchange_key=config['BITGET_API_KEY'],
            exchange_secret=config['BITGET_SECRET_KEY'],
            exchange_passphrase=config['BITGET_PASSPHRASE']
        )
        self.ai = AIBrain(api_key=config['GROQ_API_KEY'])
        self.risk = RiskManager(
            initial_balance=config['INITIAL_BALANCE'],
            max_daily_trades=config['MAX_DAILY_TRADES'],
            max_drawdown_pct=config['MAX_DRAWDOWN_PCT']
        )
        self.telegram = TelegramHandler(
            token=config['TELEGRAM_BOT_TOKEN'],
            authorized_chat_id=config['YOUR_CHAT_ID']
        )
        self.monitor_interval = config['MONITOR_INTERVAL']
        self.min_confidence = config['MIN_CONFIDENCE']
        self.tp_pct = config['TP_PCT']
        self.sl_pct = config['SL_PCT']
        self.risk_per_trade = config['RISK_PER_TRADE']

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
                f"✅ **PAPER TRADE**\n{msg}\nConfidence: {confidence:.1%}\nReason: {decision_data.get('reasoning', 'N/A')}"
            )
        else:
            self.telegram.send_message(f"❌ {msg}")

    def run_decide_cycle(self):
        can, reason = self.risk.can_trade(self.exchange.balance)
        if not can:
            self.telegram.send_message(f"⚠️ Trade blocked: {reason}")
            return
        if self.exchange.position:
            self.telegram.send_message("You already have an open position. Use /close first.")
            return

        try:
            ticker = self.exchange.fetch_ticker()
            price = ticker['last']
            ohlcv = self.exchange.fetch_ohlcv(limit=20)
            df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
            learning_context = self.get_learning_context()
            decision_data = self.ai.get_decision(
                price, df, self.exchange.balance, False, self.exchange.timeframe, learning_context
            )
            if not decision_data:
                self.telegram.send_message("❌ AI call failed.")
                return
            self.execute_decision(decision_data, price)
        except Exception as e:
            logger.error(f"Decide cycle error: {e}")
            self.telegram.send_message(f"❌ Error: {e}")

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
                time.sleep(self.monitor_interval)
            except Exception as e:
                logger.error(f"Monitor error: {e}")
                time.sleep(10)

    def start(self):
        logger.info("Starting Fearless Bot...")
        self.telegram.send_message("✅ Bot online. Paper trading active (Bitget).\n/help for commands.")
        
        # Setup Telegram handlers
        @self.telegram.bot.message_handler(commands=['start', 'help'])
        def cmd_help(message):
            if not self.telegram.is_authorized(message.chat.id): return
            txt = """
🤖 *Fearless Futures Paper Trader (Bitget)*
Commands:
/decide – Analyze market & open a trade
/status – Show current position & balance
/close – Close current position
/balance – Show paper balance
/history – Last 5 closed trades
"""
            self.telegram.reply_to(message, txt)

        @self.telegram.bot.message_handler(commands=['decide'])
        def cmd_decide(message):
            if not self.telegram.is_authorized(message.chat.id): return
            self.telegram.reply_to(message, "🧠 Analyzing market & getting AI decision...")
            threading.Thread(target=self.run_decide_cycle).start()

        @self.telegram.bot.message_handler(commands=['status'])
        def cmd_status(message):
            if not self.telegram.is_authorized(message.chat.id): return
            if self.exchange.position:
                pos = self.exchange.position
                ticker = self.exchange.fetch_ticker()
                curr = ticker['last']
                if pos['side'] == 'long':
                    unrealised = (curr - pos['entry_price']) * pos['quantity'] * self.exchange.leverage
                else:
                    unrealised = (pos['entry_price'] - curr) * pos['quantity'] * self.exchange.leverage
                txt = f"""
📊 *Open Position*
Side: {pos['side'].upper()}
Entry: {pos['entry_price']:.2f}
Current: {curr:.2f}
TP: {pos['tp']:.2f}  SL: {pos['sl']:.2f}
Unrealised PnL: {unrealised:.2f} USDT
"""
            else:
                txt = "No open position."
            txt += f"\n💵 Paper Balance: {self.exchange.balance:.2f} USDT"
            self.telegram.send_message(txt)

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
