import logging
import threading
import time
import schedule
from datetime import datetime, timezone

from .exchange import PaperExchange
from .ai_brain import AIBrain
from .risk_manager import RiskManager
from .telegram_handler import TelegramHandler
from .storage import Storage
from .news import NewsFetcher
from .macro import MacroFetcher
from .liquidity import LiquidityAnalyzer
from .adaptive_tp import AdaptiveTPEngine

logger = logging.getLogger("FearlessFutures.Bot")

HELP_TEXT = """
🤖 *Fearless Futures AI — Command Center*
📄 Mode: *PAPER TRADING*

*Trading*
/decide — Scan all pairs, pick the best trade
/close — Force-close current position
/toggle — Toggle auto-trading on/off

*Intelligence*
/status — Full system status
/balance — Current balance
/pnl — Performance stats
/macro — Global macro snapshot
/ai\_log — Last 5 AI decisions with full reasoning
/lessons — Learned trade lessons
/history — Last 10 closed trades
/liquidity [SYMBOL] — Deep liquidity analysis
/adaptive — Adaptive TP learning engine status

*Config*
/settings — View current config
/help — This menu
"""

class FearlessBot:
    def __init__(self, config):
        self.config     = config
        self.auto_trade = config.get('AUTO_TRADE', True)

        # Storage
        self.storage = Storage()
        saved        = self.storage.load_state()
        initial_bal  = saved['balance'] if saved else config['INITIAL_BALANCE']

        # Core components
        self.exchange = PaperExchange(
            initial_balance=initial_bal,
            leverage=config['LEVERAGE'],
            exchange_key=config['BITGET_API_KEY'],
            exchange_secret=config['BITGET_SECRET_KEY'],
            exchange_passphrase=config['BITGET_PASSPHRASE']
        )
        self.ai                  = AIBrain(api_key=config['GROQ_API_KEY'])
        self.news                = NewsFetcher()
        self.macro               = MacroFetcher()
        self.risk                = RiskManager(
            initial_balance=initial_bal,
            max_daily_trades=config['MAX_DAILY_TRADES'],
            max_drawdown_pct=config['MAX_DRAWDOWN_PCT']
        )
        self.liquidity_analyzer  = LiquidityAnalyzer()
        self.adaptive_tp         = AdaptiveTPEngine(saved.get('adaptive_tp') if saved else None)
        self.telegram            = TelegramHandler(
            token=config['TELEGRAM_BOT_TOKEN'],
            authorized_chat_id=config['YOUR_CHAT_ID']
        )

        # Restore saved state
        self.lessons = []
        if saved:
            self.exchange.position   = saved.get('position')
            self.exchange.trade_log  = saved.get('trade_log', [])
            self.lessons             = saved.get('lessons', [])
            self.risk.peak_balance   = saved.get('peak_balance', initial_bal)
            if saved.get('ai_log'):
                self.ai.decision_log = saved['ai_log']

        self.symbols        = config['SYMBOLS']
        self.risk_per_trade = config['RISK_PER_TRADE']
        self.min_confidence = config['MIN_CONFIDENCE']
        self._deciding      = False

    # ─── Persistence ────────────────────────────────────────────────────────────

    def _save(self):
        self.storage.save_state(
            self.exchange.balance,
            self.exchange.position,
            self.exchange.trade_log,
            self.risk.peak_balance,
            self.lessons,
            self.ai.decision_log,
            self.adaptive_tp.to_dict()
        )

    # ─── Core trade cycle ───────────────────────────────────────────────────────

    def run_decide_cycle(self, silent=False):
        if self._deciding:
            if not silent: self.telegram.send("⏳ Already scanning. Please wait.")
            return
        if self.exchange.position:
            if not silent: self.telegram.send("📌 Position already open. Use /status to check it.")
            return

        can, reason = self.risk.can_trade(self.exchange.balance)
        if not can:
            if not silent: self.telegram.send(f"⚠️ *Trade blocked:* {reason}")
            return

        self._deciding = True
        try:
            latest_news  = self.news.fetch_latest_news()
            macro_data   = self.macro.fetch()
            lessons_list = self.lessons[-7:]

            best_decision  = None
            best_symbol    = None
            best_price     = None
            best_indicators = None
            best_liquidity = None

            for symbol in self.symbols:
                try:
                    ticker     = self.exchange.fetch_ticker(symbol)
                    price      = ticker['last']
                    df         = self.exchange.fetch_ohlcv(symbol, '5m', limit=250)
                    indicators = self.exchange.get_latest_indicators(df)
                    order_book = self.exchange.fetch_order_book(symbol)
                    raw_book   = self.exchange.fetch_order_book_raw(symbol)
                    liquidity  = self.liquidity_analyzer.analyze(raw_book, price)

                    # Skip illiquid pairs
                    if not liquidity.get('tradeable', True) and liquidity.get('liquidity_score', 1) < 0.3:
                        logger.info(f"Skipping {symbol} — poor liquidity ({liquidity.get('liquidity_score',0):.2f})")
                        continue

                    # Skip if DMI says no trend (ADX < 20) — only trade trending markets
                    adx = indicators.get('adx', 0)
                    if adx < 20:
                        logger.info(f"Skipping {symbol} — weak trend (ADX {adx:.1f})")
                        continue

                    decision = self.ai.get_decision(
                        symbol=symbol,
                        price=price,
                        indicators=indicators,
                        balance=self.exchange.balance,
                        order_book=order_book,
                        news=latest_news,
                        macro=macro_data,
                        lessons=lessons_list,
                        liquidity=liquidity
                    )

                    if decision and decision.get('confidence', 0) >= self.min_confidence:
                        if not best_decision or decision['confidence'] > best_decision['confidence']:
                            best_decision   = decision
                            best_symbol     = symbol
                            best_price      = price
                            best_indicators = indicators
                            best_liquidity  = liquidity
                except Exception as e:
                    logger.error(f"Error scanning {symbol}: {e}")

            if best_decision:
                self._execute_trade(best_symbol, best_decision, best_price, best_indicators, best_liquidity)
            elif not silent:
                self.telegram.send(
                    f"🔍 *Scanned {len(self.symbols)} pairs.* No high-confidence trending setup found.\n"
                    f"_Min confidence: {self.min_confidence:.0%} | Min ADX: 20_"
                )
        except Exception as e:
            logger.error(f"Decide cycle error: {e}")
            if not silent: self.telegram.send(f"❌ *Error during scan:* `{e}`")
        finally:
            self._deciding = False

    def _execute_trade(self, symbol, decision, price, indicators, liquidity=None):
        side = decision['decision'].lower()
        atr  = indicators.get('atr', price * 0.012)

        # Use adaptive TP engine for dynamic levels
        entry_notional = self.exchange.balance * self.risk_per_trade * self.exchange.leverage
        levels = self.adaptive_tp.calculate_levels(price, side, atr, entry_notional)

        tp  = levels['tp']
        sl  = levels['sl']
        qty = (self.exchange.balance * self.risk_per_trade) / (levels['sl_dist'] * self.exchange.leverage)

        success, msg = self.exchange.open_position(
            symbol, side, qty, tp, sl,
            atr=atr, entry_notional=entry_notional
        )
        if success:
            self.risk.trade_count_today += 1
            key_signals = "\n".join([f"  • {s}" for s in decision.get('key_signals', [])])
            adx  = indicators.get('adx', 0)
            dmi  = indicators.get('dmi_signal', 'N/A')
            dip  = indicators.get('di_plus', 0)
            dim  = indicators.get('di_minus', 0)
            text = (
                f"📄 *PAPER TRADE — {symbol}*\n\n"
                f"{msg}\n\n"
                f"📊 *DMI:* ADX `{adx:.1f}` | DI+ `{dip:.1f}` | DI- `{dim:.1f}` | {dmi}\n"
                f"🎯 *TP Multiplier:* `{levels['tp_mult_used']}x ATR` (RR: `{levels['rr_ratio']}`)\n"
                f"💰 *Target Profit:* `+${levels['target_profit_usdt']:.2f}` (30% of notional)\n\n"
                f"🤖 *AI Confidence:* `{decision['confidence']:.1%}`\n"
                f"📝 *Reasoning:* {decision.get('reasoning', 'N/A')}\n"
                f"🔑 *Key Signals:*\n{key_signals}\n"
                f"⚠️ *Risk Note:* {decision.get('risk_note', 'N/A')}"
            )
            self.telegram.send(text)
            self._save()

    # ─── Position monitor ────────────────────────────────────────────────────────

    def _monitor_loop(self):
        while True:
            try:
                if self.exchange.position:
                    pos    = self.exchange.position
                    ticker = self.exchange.fetch_ticker(pos['symbol'])
                    closed, reason, pnl = self.exchange.check_tp_sl(ticker['last'])
                    if closed:
                        closed_trade = self.exchange.trade_log[-1]
                        atr_at_entry = pos.get('atr_at_entry') or (pos['entry_price'] * 0.012)

                        # Learn from the trade
                        adapt_result = self.adaptive_tp.learn(closed_trade, atr_at_entry)
                        lesson       = self.ai.reflect_on_trade(closed_trade)
                        self.lessons.append(lesson)
                        self.risk.record_trade_result(pnl)
                        self.risk.update_peak(self.exchange.balance)
                        self.storage.append_journal(closed_trade)

                        pnl_emoji = "🟢" if pnl > 0 else "🔴"
                        adapt_str = ""
                        if adapt_result.get('adapted'):
                            adapt_str = (
                                f"\n\n🧠 *TP Engine Evolved (v{adapt_result['version']})*\n"
                                f"Multiplier: `{adapt_result['old_tp_mult']}x` → `{adapt_result['new_tp_mult']}x`\n"
                                f"Avg extraction: `{adapt_result['avg_extraction']*100:.1f}%`\n"
                                f"_{adapt_result['reason']}_"
                            )

                        tp_status = self.adaptive_tp.status()
                        self.telegram.send(
                            f"{pnl_emoji} *Paper Trade Closed — {pos['symbol']}*\n"
                            f"Reason: {reason}\n"
                            f"PnL: `{'+'if pnl>0 else ''}{pnl:.4f} USDT`\n"
                            f"New Balance: `${self.exchange.balance:.2f}`\n"
                            f"Extraction: `{abs(pnl)/pos.get('entry_notional',1)*100:.1f}%` of notional\n\n"
                            f"🧠 *Lesson:* {lesson}"
                            f"{adapt_str}"
                        )
                        self._save()

                        # Auto-decide after close if auto mode on
                        if self.auto_trade:
                            time.sleep(5)
                            threading.Thread(
                                target=self.run_decide_cycle,
                                kwargs={"silent": True}
                            ).start()

                schedule.run_pending()
                time.sleep(self.config.get('MONITOR_INTERVAL', 60))
            except Exception as e:
                logger.error(f"Monitor error: {e}")
                time.sleep(15)

    # ─── Telegram commands ───────────────────────────────────────────────────────

    def _register_handlers(self):
        bot = self.telegram.bot

        def auth(fn):
            def wrapper(msg):
                if self.telegram.is_authorized(msg.chat.id): fn(msg)
                else: bot.reply_to(msg, "🚫 Unauthorized.")
            return wrapper

        def auth_cb(fn):
            def wrapper(call):
                if self.telegram.is_authorized(call.message.chat.id):
                    fn(call)
                    bot.answer_callback_query(call.id)
                else:
                    bot.answer_callback_query(call.id, "Unauthorized")
            return wrapper

        @bot.message_handler(commands=['start', 'help'])
        @auth
        def cmd_help(msg):
            self.telegram.reply_to(msg, HELP_TEXT, reply_markup=self.telegram.main_keyboard())

        @bot.message_handler(commands=['decide'])
        @auth
        def cmd_decide(msg):
            self.telegram.reply_to(msg, f"🔍 Scanning {len(self.symbols)} pairs for trending + high-confidence setups...")
            threading.Thread(target=self.run_decide_cycle).start()

        @bot.message_handler(commands=['close'])
        @auth
        def cmd_close(msg):
            if not self.exchange.position:
                self.telegram.reply_to(msg, "📭 No open position.")
                return
            ticker = self.exchange.fetch_ticker(self.exchange.position['symbol'])
            ok, result, pnl = self.exchange.close_now(ticker['last'])
            if ok:
                pnl_emoji = "🟢" if pnl > 0 else "🔴"
                self.telegram.reply_to(
                    msg,
                    f"{pnl_emoji} *Closed manually.*\nPnL: `{'+'if pnl>0 else ''}{pnl:.4f} USDT`\nBalance: `${self.exchange.balance:.2f}`"
                )
                self._save()

        @bot.message_handler(commands=['status'])
        @auth
        def cmd_status(msg):
            pos  = self.exchange.position
            risk = self.risk.get_status()
            auto_str = "✅ ON" if self.auto_trade else "❌ OFF"
            tp_s     = self.adaptive_tp.status()
            text = (
                f"📊 *System Status* — 📄 PAPER MODE\n\n"
                f"💰 Balance: `${self.exchange.balance:.2f}`\n"
                f"🤖 Auto-trade: {auto_str}\n"
                f"📅 Trades today: `{risk['trades_today']}/{risk['max_daily']}`\n"
                f"📉 Consecutive losses: `{risk['consecutive_losses']}`\n"
                f"🧠 TP Engine: `{tp_s['tp_mult_used'] if 'tp_mult_used' in tp_s else tp_s['tp_mult']}x ATR` | Avg extraction: `{tp_s['avg_extraction_pct']:.1f}%` {tp_s['status_emoji']}\n\n"
            )
            if pos:
                ticker    = self.exchange.fetch_ticker(pos['symbol'])
                curr      = ticker['last']
                unrealized = (
                    (curr - pos['entry_price']) * pos['quantity'] * pos['leverage']
                    if pos['side'] == 'long'
                    else (pos['entry_price'] - curr) * pos['quantity'] * pos['leverage']
                )
                pnl_emoji = "🟢" if unrealized > 0 else "🔴"
                text += (
                    f"📌 *Open Paper Position: {pos['symbol']}*\n"
                    f"Side: `{pos['side'].upper()}`\n"
                    f"Entry: `${pos['entry_price']:,.4f}` → Now: `${curr:,.4f}`\n"
                    f"TP: `${pos['tp']:,.4f}` | SL: `${pos['sl']:,.4f}`\n"
                    f"{pnl_emoji} Unrealized: `{'+'if unrealized>0 else ''}{unrealized:.4f} USDT`"
                )
            else:
                text += "📭 *No open position.*"
            self.telegram.reply_to(msg, text)

        @bot.message_handler(commands=['balance'])
        @auth
        def cmd_balance(msg):
            self.telegram.reply_to(
                msg,
                f"💰 *Balance:* `${self.exchange.balance:.4f} USDT` *(paper)*\n"
                f"📈 Peak: `${self.risk.peak_balance:.4f}`"
            )

        @bot.message_handler(commands=['pnl'])
        @auth
        def cmd_pnl(msg):
            stats = self.exchange.get_stats()
            if not stats:
                self.telegram.reply_to(msg, "📭 No trades yet.")
                return
            tp_s = self.adaptive_tp.status()
            text = (
                f"📈 *Paper Performance Stats*\n\n"
                f"Total Trades: `{stats['total_trades']}`\n"
                f"Wins / Losses: `{stats['wins']} / {stats['losses']}`\n"
                f"Win Rate: `{stats['win_rate']}%`\n"
                f"Total PnL: `{'+'if stats['total_pnl']>0 else ''}{stats['total_pnl']} USDT`\n"
                f"Avg Win: `+{stats['avg_win']}` | Avg Loss: `{stats['avg_loss']}`\n"
                f"Profit Factor: `{stats['profit_factor']}`\n"
                f"Best: `+{stats['best_trade']}` | Worst: `{stats['worst_trade']}`\n\n"
                f"🧠 Avg Extraction Rate: `{tp_s['avg_extraction_pct']:.1f}%` (target: 30%)"
            )
            self.telegram.reply_to(msg, text)

        @bot.message_handler(commands=['adaptive'])
        @auth
        def cmd_adaptive(msg):
            self.telegram.reply_to(msg, self.adaptive_tp.format_for_telegram())

        @bot.message_handler(commands=['macro'])
        @auth
        def cmd_macro(msg):
            self.telegram.reply_to(msg, "🌍 Fetching macro data...")
            data = self.macro.fetch()
            self.telegram.send(self.macro.format_for_telegram(data))

        @bot.message_handler(commands=['ai_log'])
        @auth
        def cmd_ai_log(msg):
            decisions = self.ai.get_recent_decisions(5)
            if not decisions:
                self.telegram.reply_to(msg, "🤖 No AI decisions logged yet.")
                return
            text = "🧠 *Last 5 AI Decisions*\n\n"
            for i, d in enumerate(reversed(decisions), 1):
                signals = ", ".join(d.get('key_signals', []))
                text += (
                    f"*#{i} — {d.get('symbol')} {d.get('decision')}*\n"
                    f"Confidence: `{d.get('confidence', 0):.1%}` | Price: `${d.get('price', 0):,.4f}`\n"
                    f"📝 {d.get('reasoning', 'N/A')}\n"
                    f"🔑 {signals}\n"
                    f"⚠️ {d.get('risk_note', 'N/A')}\n\n"
                )
            self.telegram.reply_to(msg, text)

        @bot.message_handler(commands=['lessons'])
        @auth
        def cmd_lessons(msg):
            if not self.lessons:
                self.telegram.reply_to(msg, "📚 No lessons yet — make some trades!")
                return
            text = "📚 *AI-Learned Lessons*\n\n"
            for i, l in enumerate(self.lessons[-10:], 1):
                text += f"{i}. {l}\n"
            self.telegram.reply_to(msg, text)

        @bot.message_handler(commands=['history'])
        @auth
        def cmd_history(msg):
            trades = self.exchange.trade_log[-10:]
            if not trades:
                self.telegram.reply_to(msg, "📭 No trade history yet.")
                return
            text = "📓 *Last 10 Paper Trades*\n\n"
            for t in reversed(trades):
                pnl   = t.get('pnl', 0)
                emoji = "🟢" if pnl > 0 else "🔴"
                text += (
                    f"{emoji} {t['symbol']} `{t['side'].upper()}`\n"
                    f"   Entry: `${t['entry_price']:,.4f}` → Exit: `${t.get('exit_price',0):,.4f}`\n"
                    f"   PnL: `{'+'if pnl>0 else ''}{pnl:.4f}` | {t.get('reason','')}\n\n"
                )
            self.telegram.reply_to(msg, text)

        @bot.message_handler(commands=['toggle'])
        @auth
        def cmd_toggle(msg):
            self.auto_trade = not self.auto_trade
            state = (
                "✅ *Auto-trading ENABLED*\nI'll scan and paper-trade automatically."
                if self.auto_trade else
                "❌ *Auto-trading DISABLED*\nUse /decide to paper-trade manually."
            )
            self.telegram.reply_to(msg, state)

        @bot.message_handler(commands=['settings'])
        @auth
        def cmd_settings(msg):
            tp_s = self.adaptive_tp.status()
            text = (
                f"⚙️ *Current Settings*\n\n"
                f"🟡 Mode: *PAPER TRADING* (no real money)\n\n"
                f"Symbols: `{', '.join(self.symbols)}`\n"
                f"Leverage: `{self.exchange.leverage}x`\n"
                f"Risk/Trade: `{self.risk_per_trade*100:.1f}%` of balance\n"
                f"Min ADX (trend filter): `20`\n"
                f"Min Confidence: `{self.min_confidence:.0%}`\n"
                f"Max Daily Trades: `{self.risk.max_daily_trades}`\n"
                f"Max Drawdown: `{self.risk.max_drawdown_pct*100:.0f}%`\n"
                f"Auto-trade: `{'ON' if self.auto_trade else 'OFF'}`\n\n"
                f"🧠 *Adaptive TP Engine*\n"
                f"TP Mult: `{tp_s['tp_mult']}x ATR` | SL Mult: `{tp_s['sl_mult']}x ATR`\n"
                f"Target extraction: `30%` of entry notional\n"
                f"Learning version: `v{tp_s['learning_version']}` ({tp_s['wins_learned_from']} wins)"
            )
            self.telegram.reply_to(msg, text)

        @bot.message_handler(commands=['liquidity'])
        @auth
        def cmd_liquidity(msg):
            parts  = msg.text.strip().split()
            symbol = parts[1].upper() if len(parts) > 1 else "BTC/USDT"
            if "/" not in symbol: symbol += "/USDT"
            self.telegram.reply_to(msg, f"💧 Analyzing {symbol} liquidity...")
            try:
                ticker   = self.exchange.fetch_ticker(symbol)
                price    = ticker['last']
                raw_book = self.exchange.fetch_order_book_raw(symbol, limit=30)
                analysis = self.liquidity_analyzer.analyze(raw_book, price)
                self.telegram.send(self.liquidity_analyzer.format_for_telegram(analysis, symbol))
            except Exception as e:
                self.telegram.send(f"❌ Error: {e}")

        # ── Inline keyboard callbacks ──────────────────────────────────────────

        @bot.callback_query_handler(func=lambda c: True)
        @auth_cb
        def handle_callback(call):
            d = call.data
            m = call.message
            if   d == "decide":   cmd_decide(m); return
            elif d == "status":   cmd_status(m)
            elif d == "balance":  cmd_balance(m)
            elif d == "pnl":      cmd_pnl(m)
            elif d == "macro":    cmd_macro(m)
            elif d == "ai_log":   cmd_ai_log(m)
            elif d == "lessons":  cmd_lessons(m)
            elif d == "history":  cmd_history(m)
            elif d == "close":    cmd_close(m)
            elif d == "toggle":   cmd_toggle(m)
            elif d == "settings": cmd_settings(m)
            elif d == "adaptive": cmd_adaptive(m)

    # ─── Startup ──────────────────────────────────────────────────────────────────

    def start(self):
        self._register_handlers()

        # Hourly auto-scan
        schedule.every(1).hours.do(
            lambda: threading.Thread(
                target=self.run_decide_cycle, kwargs={"silent": True}
            ).start()
        )

        # Start monitor thread
        threading.Thread(target=self._monitor_loop, daemon=True).start()

        # Startup message
        tp_s    = self.adaptive_tp.status()
        auto    = "✅ ON" if self.auto_trade else "❌ OFF"
        self.telegram.send(
            f"🚀 *Fearless Futures AI — Online*\n"
            f"📄 Mode: *PAPER TRADING*\n\n"
            f"💰 Balance: `${self.exchange.balance:.2f} USDT`\n"
            f"📡 Scanning: `{', '.join(self.symbols)}`\n"
            f"🤖 Auto-trade: {auto}\n"
            f"📊 Trend filter: ADX ≥ 20 (DMI)\n"
            f"🧠 Adaptive TP: `{tp_s['tp_mult']}x ATR` | Target: `30% extraction` | v{tp_s['learning_version']}\n\n"
            f"Type /help for all commands.",
            reply_markup=self.telegram.main_keyboard()
        )

        logger.info("FearlessBot started — PAPER MODE. Polling...")
        self.telegram.bot.infinity_polling(timeout=30, long_polling_timeout=20)
