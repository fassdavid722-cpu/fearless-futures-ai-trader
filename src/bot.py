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

*Auto-alerts (no commands needed):*
💎 New trade opened
💰 Trade closed (TP/SL/Trail)
🔒 Trailing stop activated
⚡ Volatility spike detected
📊 Hourly market briefing
🧠 TP engine evolved

*Your commands:*
/ping — Is the bot alive? Quick check
/alive — Full diagnostics
/decide — Force a scan right now
/close — Manually close position
/toggle — Auto-trade ON/OFF
/status — Full system status
/balance — Balance
/pnl — Performance stats
/macro — Global macro snapshot
/ai\\_log — Last 5 AI decisions
/lessons — Learned lessons
/history — Last 10 trades
/liquidity [SYMBOL] — Liquidity report
/adaptive — TP learning engine
/settings — Config overview
/help — This menu
"""

class FearlessBot:
    def __init__(self, config):
        self.config     = config
        self.auto_trade = config.get('AUTO_TRADE', True)

        self.storage = Storage()
        saved        = self.storage.load_state()
        initial_bal  = saved['balance'] if saved else config['INITIAL_BALANCE']

        self.exchange = PaperExchange(
            initial_balance=initial_bal,
            leverage=config['LEVERAGE'],
            exchange_key=config['BITGET_API_KEY'],
            exchange_secret=config['BITGET_SECRET_KEY'],
            exchange_passphrase=config['BITGET_PASSPHRASE']
        )
        self.ai                 = AIBrain(api_key=config['GROQ_API_KEY'])
        self.news               = NewsFetcher()
        self.macro_fetcher      = MacroFetcher()
        self.risk               = RiskManager(
            initial_balance=initial_bal,
            max_daily_trades=config['MAX_DAILY_TRADES'],
            max_drawdown_pct=config['MAX_DRAWDOWN_PCT']
        )
        self.liquidity_analyzer = LiquidityAnalyzer()
        self.adaptive_tp        = AdaptiveTPEngine(saved.get('adaptive_tp') if saved else None)
        self.telegram           = TelegramHandler(
            token=config['TELEGRAM_BOT_TOKEN'],
            authorized_chat_id=config['YOUR_CHAT_ID']
        )

        self.lessons = []
        if saved:
            self.exchange.position  = saved.get('position')
            self.exchange.trade_log = saved.get('trade_log', [])
            self.lessons            = saved.get('lessons', [])
            self.risk.peak_balance  = saved.get('peak_balance', initial_bal)
            if saved.get('ai_log'):
                self.ai.decision_log = saved['ai_log']

        self.symbols        = config['SYMBOLS']
        self.risk_per_trade = config['RISK_PER_TRADE']
        self.min_confidence = config['MIN_CONFIDENCE']
        self._deciding       = False
        self._alerted_spikes = set()   # track which symbols got spike alerts this hour
        self._startup_time   = datetime.now(timezone.utc)
        self._last_scan_time = None
        self._last_trade_time = None
        self._last_activity_alert = datetime.now(timezone.utc)

    # ─── Persistence ─────────────────────────────────────────────────────────────

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

    # ─── Decide cycle ────────────────────────────────────────────────────────────

    def run_decide_cycle(self, silent=False):
        if self._deciding:
            if not silent: self.telegram.send("⏳ Already scanning — hold tight.")
            return
        if self.exchange.position:
            if not silent: self.telegram.send("📌 I've already got a position open. Check /status for details.")
            return

        can, reason = self.risk.can_trade(self.exchange.balance)
        if not can:
            if not silent: self.telegram.send(f"⚠️ Can't trade right now: {reason}")
            return

        self._deciding = True
        self._last_scan_time = datetime.now(timezone.utc)
        try:
            latest_news  = self.news.fetch_latest_news()
            macro_data   = self.macro_fetcher.fetch()
            lessons_list = self.lessons[-7:]

            best_decision  = None
            best_symbol    = None
            best_price     = None
            best_indicators = None
            best_liquidity  = None
            skipped_no_trend = 0
            skipped_illiquid = 0

            for symbol in self.symbols:
                try:
                    ticker     = self.exchange.fetch_ticker(symbol)
                    price      = ticker['last']
                    df         = self.exchange.fetch_ohlcv(symbol, '5m', limit=250)
                    indicators = self.exchange.get_latest_indicators(df)
                    order_book = self.exchange.fetch_order_book(symbol)
                    raw_book   = self.exchange.fetch_order_book_raw(symbol)
                    liquidity  = self.liquidity_analyzer.analyze(raw_book, price)

                    # Liquidity filter
                    if not liquidity.get('tradeable', True) and liquidity.get('liquidity_score', 1) < 0.3:
                        skipped_illiquid += 1
                        continue

                    # DMI trend filter
                    adx = indicators.get('adx', 0)
                    if adx < 20:
                        skipped_no_trend += 1
                        continue

                    # Volatility spike alert (once per symbol per hour)
                    if indicators.get('volatility_spike') and symbol not in self._alerted_spikes:
                        self._alerted_spikes.add(symbol)
                        atr     = indicators.get('atr', 0)
                        rsi     = indicators.get('rsi', 50)
                        dmi_sig = indicators.get('dmi_signal', '')
                        self.telegram.send(
                            f"⚡ *Volatility Spike — {symbol}*\n\n"
                            f"ATR just jumped 1.8x above its average — big move incoming or already happening.\n\n"
                            f"Price: `${price:,.4f}` | RSI: `{rsi:.0f}` | ADX: `{adx:.0f}`\n"
                            f"DMI: {dmi_sig}\n\n"
                            f"_I'm scanning for a trade entry..._"
                        )

                    decision = self.ai.get_decision(
                        symbol=symbol, price=price,
                        indicators=indicators, balance=self.exchange.balance,
                        order_book=order_book, news=latest_news,
                        macro=macro_data, lessons=lessons_list, liquidity=liquidity
                    )

                    if decision and decision.get('confidence', 0) >= self.min_confidence:
                        if not best_decision or decision['confidence'] > best_decision['confidence']:
                            best_decision   = decision
                            best_symbol     = symbol
                            best_price      = price
                            best_indicators = indicators
                            best_liquidity  = liquidity
                except Exception as e:
                    logger.error(f"Scan error ({symbol}): {e}")

            if best_decision:
                self._execute_trade(best_symbol, best_decision, best_price, best_indicators, best_liquidity)
            elif not silent:
                self.telegram.send(
                    f"🔍 Scanned all {len(self.symbols)} pairs — nothing worth trading right now.\n\n"
                    f"Skipped {skipped_no_trend} pairs (weak trend, ADX < 20), "
                    f"{skipped_illiquid} pairs (poor liquidity).\n"
                    f"_I'll check again in an hour, or use /decide to force a scan._"
                )
        except Exception as e:
            logger.error(f"Decide cycle error: {e}")
            if not silent: self.telegram.send(f"❌ Something went wrong during the scan: `{e}`")
        finally:
            self._deciding = False

    def _execute_trade(self, symbol, decision, price, indicators, liquidity=None):
        side           = decision['decision'].lower()
        atr            = indicators.get('atr', price * 0.012)
        entry_notional = self.exchange.balance * self.risk_per_trade * self.exchange.leverage
        levels         = self.adaptive_tp.calculate_levels(price, side, atr, entry_notional)
        qty            = (self.exchange.balance * self.risk_per_trade) / (levels['sl_dist'] * self.exchange.leverage)

        success, msg = self.exchange.open_position(
            symbol, side, qty, levels['tp'], levels['sl'],
            atr=atr, entry_notional=entry_notional
        )
        if success:
            self.risk.trade_count_today += 1
            self._last_trade_time = datetime.now(timezone.utc)
            self._last_activity_alert = datetime.now(timezone.utc)
            adx  = indicators.get('adx', 0)
            dip  = indicators.get('di_plus', 0)
            dim  = indicators.get('di_minus', 0)
            dmi  = indicators.get('dmi_signal', '')
            signals_str = "\n".join([f"  • {s}" for s in decision.get('key_signals', [])])

            self.telegram.send(
                f"💎 *New Paper Trade — {symbol}*\n\n"
                f"{msg}\n\n"
                f"📊 *DMI:* ADX `{adx:.1f}` | DI+ `{dip:.1f}` | DI- `{dim:.1f}`\n"
                f"Trend signal: *{dmi}*\n\n"
                f"🎯 TP multiplier: `{levels['tp_mult_used']}x ATR` (R:R `{levels['rr_ratio']}`)\n"
                f"💰 Target profit: `+${levels['target_profit_usdt']:.2f}` *(30% of notional)*\n"
                f"🔒 Trailing stop activates at `+15%` profit\n\n"
                f"🤖 AI confidence: `{decision['confidence']:.1%}`\n"
                f"📝 {decision.get('reasoning', '')}\n\n"
                f"🔑 Key signals:\n{signals_str}\n\n"
                f"⚠️ {decision.get('risk_note', '')}"
            )
            self._save()

    # ─── Position monitor ────────────────────────────────────────────────────────

    def _monitor_loop(self):
        while True:
            try:
                if self.exchange.position:
                    pos    = self.exchange.position
                    ticker = self.exchange.fetch_ticker(pos['symbol'])
                    price  = ticker['last']

                    closed, reason, pnl = self.exchange.check_tp_sl(price)

                    # Trailing stop just activated — alert immediately
                    if not closed and reason == "TRAIL_ACTIVATED":
                        new_sl = pos.get('sl', 0)
                        self.telegram.send(
                            f"🔒 *Trailing Stop Activated — {pos['symbol']}*\n\n"
                            f"You're up `+15%` on this trade — the stop is now following the price.\n"
                            f"Current SL locked at: `${new_sl:,.4f}`\n"
                            f"_Profits are protected. Let it run._"
                        )

                    if closed:
                        closed_trade = self.exchange.trade_log[-1]
                        atr_at_entry = pos.get('atr_at_entry') or (pos['entry_price'] * 0.012)
                        adapt_result = self.adaptive_tp.learn(closed_trade, atr_at_entry)
                        lesson       = self.ai.reflect_on_trade(closed_trade)
                        self.lessons.append(lesson)
                        self.risk.record_trade_result(pnl)
                        self.risk.update_peak(self.exchange.balance)
                        self.storage.append_journal(closed_trade)

                        pnl_emoji    = "🟢" if pnl > 0 else "🔴"
                        extraction   = abs(pnl) / pos.get('entry_notional', 1) * 100
                        trail_used   = pos.get('trail_activated', False)
                        trail_str    = " *(trailing stop protected this profit)*" if trail_used and pnl > 0 else ""

                        adapt_str = ""
                        if adapt_result.get('adapted'):
                            adapt_str = (
                                f"\n\n🧠 *TP Engine just levelled up (v{adapt_result['version']})*\n"
                                f"Multiplier adjusted: `{adapt_result['old_tp_mult']}x` → `{adapt_result['new_tp_mult']}x ATR`\n"
                                f"Avg extraction now: `{adapt_result['avg_extraction']*100:.1f}%` (target 30%)\n"
                                f"_{adapt_result['reason']}_"
                            )

                        self.telegram.send(
                            f"{pnl_emoji} *Trade Closed — {pos['symbol']}*\n\n"
                            f"Reason: {reason}{trail_str}\n"
                            f"PnL: `{'+'if pnl>0 else ''}{pnl:.4f} USDT`\n"
                            f"Extraction: `{extraction:.1f}%` of notional\n"
                            f"New balance: `${self.exchange.balance:.2f} USDT`\n\n"
                            f"🧠 Lesson learned: _{lesson}_"
                            f"{adapt_str}"
                        )
                        self._save()

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

    # ─── Hourly market briefing ───────────────────────────────────────────────────

    def _send_hourly_briefing(self):
        try:
            macro   = self.macro_fetcher.fetch()
            stats   = self.exchange.get_stats()
            tp_s    = self.adaptive_tp.status()
            pos     = self.exchange.position

            fg      = macro.get('fear_greed_value', '?')
            fg_lab  = macro.get('fear_greed_label', '?')
            btc_chg = macro.get('btc_change_24h', 0)
            btc_px  = macro.get('btc_price', 0)
            mkt_chg = macro.get('market_cap_change_24h', 0)
            btc_e   = "🟢" if btc_chg > 0 else "🔴"

            risk    = self.risk.get_status()

            pos_str = "📭 No open position."
            if pos:
                try:
                    ticker    = self.exchange.fetch_ticker(pos['symbol'])
                    curr      = ticker['last']
                    unrealized = (
                        (curr - pos['entry_price']) * pos['quantity'] * pos['leverage']
                        if pos['side'] == 'long'
                        else (pos['entry_price'] - curr) * pos['quantity'] * pos['leverage']
                    )
                    trail_str = " 🔒 trail active" if pos.get('trail_activated') else ""
                    pos_str = (
                        f"📌 {pos['symbol']} {pos['side'].upper()}{trail_str}\n"
                        f"   Entry `${pos['entry_price']:,.4f}` → Now `${curr:,.4f}`\n"
                        f"   Unrealized: `{'+'if unrealized>0 else ''}{unrealized:.4f} USDT`"
                    )
                except:
                    pos_str = f"📌 {pos['symbol']} {pos['side'].upper()} — price unavailable"

            win_rate_str = f"`{stats['win_rate']}%` win rate, `{stats['total_trades']}` trades" if stats else "No trades yet"

            self.telegram.send(
                f"📊 *Hourly Market Briefing*\n\n"
                f"🌍 *Macro*\n"
                f"{btc_e} BTC: `${btc_px:,.0f}` ({btc_chg:+.2f}%)\n"
                f"Fear & Greed: `{fg}/100` ({fg_lab})\n"
                f"Market cap 24h: `{mkt_chg:+.2f}%`\n\n"
                f"🤖 *Bot Status*\n"
                f"{pos_str}\n"
                f"Trades today: `{risk['trades_today']}/{risk['max_daily']}`\n"
                f"Balance: `${self.exchange.balance:.2f} USDT`\n\n"
                f"📈 *Performance*\n"
                f"{win_rate_str}\n"
                f"TP engine: `{tp_s['tp_mult']}x ATR` | Extraction: `{tp_s['avg_extraction_pct']:.1f}%` {tp_s['status_emoji']}"
            )
            # Reset hourly spike tracker
            self._alerted_spikes.clear()
        except Exception as e:
            logger.error(f"Hourly briefing error: {e}")

    # ─── Telegram commands ────────────────────────────────────────────────────────

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
                    fn(call); bot.answer_callback_query(call.id)
                else: bot.answer_callback_query(call.id, "Unauthorized")
            return wrapper

        @bot.message_handler(commands=['start', 'help'])
        @auth
        def cmd_help(msg):
            self.telegram.reply_to(msg, HELP_TEXT, reply_markup=self.telegram.main_keyboard())

        @bot.message_handler(commands=['decide'])
        @auth
        def cmd_decide(msg):
            self.telegram.reply_to(msg, f"🔍 Scanning {len(self.symbols)} pairs for trending setups — give me a sec...")
            threading.Thread(target=self.run_decide_cycle).start()

        @bot.message_handler(commands=['close'])
        @auth
        def cmd_close(msg):
            if not self.exchange.position:
                self.telegram.reply_to(msg, "Nothing to close — no open position right now.")
                return
            ticker = self.exchange.fetch_ticker(self.exchange.position['symbol'])
            ok, result, pnl = self.exchange.close_now(ticker['last'])
            if ok:
                e = "🟢" if pnl > 0 else "🔴"
                self.telegram.reply_to(
                    msg,
                    f"{e} *Closed manually.*\n"
                    f"PnL: `{'+'if pnl>0 else ''}{pnl:.4f} USDT`\n"
                    f"Balance now: `${self.exchange.balance:.2f}`"
                )
                self._save()

        @bot.message_handler(commands=['status'])
        @auth
        def cmd_status(msg):
            pos  = self.exchange.position
            risk = self.risk.get_status()
            tp_s = self.adaptive_tp.status()
            auto = "✅ ON" if self.auto_trade else "❌ OFF"
            text = (
                f"📊 *Bot Status* — 📄 PAPER MODE\n\n"
                f"💰 Balance: `${self.exchange.balance:.2f}`\n"
                f"🤖 Auto-trade: {auto}\n"
                f"📅 Trades today: `{risk['trades_today']}/{risk['max_daily']}`\n"
                f"📉 Consecutive losses: `{risk['consecutive_losses']}`\n"
                f"🧠 TP engine: `{tp_s['tp_mult']}x ATR` | Avg extraction: `{tp_s['avg_extraction_pct']:.1f}%` {tp_s['status_emoji']}\n\n"
            )
            if pos:
                try:
                    curr = self.exchange.fetch_ticker(pos['symbol'])['last']
                    unr  = (
                        (curr - pos['entry_price']) * pos['quantity'] * pos['leverage']
                        if pos['side'] == 'long'
                        else (pos['entry_price'] - curr) * pos['quantity'] * pos['leverage']
                    )
                    trail = " 🔒 *Trailing stop active*" if pos.get('trail_activated') else f"\n_Trailing stop activates at +15% (current: {((curr-pos['entry_price'])/pos['entry_price']*100*(1 if pos['side']=='long' else -1)):+.1f}%)_"
                    text += (
                        f"📌 *Open: {pos['symbol']} {pos['side'].upper()}*{trail}\n"
                        f"Entry: `${pos['entry_price']:,.4f}` → Now: `${curr:,.4f}`\n"
                        f"TP: `${pos['tp']:,.4f}` | SL: `${pos['sl']:,.4f}`\n"
                        f"Unrealized: `{'+'if unr>0 else ''}{unr:.4f} USDT`"
                    )
                except:
                    text += f"📌 {pos['symbol']} {pos['side'].upper()} — fetching price failed"
            else:
                text += "📭 No open position."
            self.telegram.reply_to(msg, text)

        @bot.message_handler(commands=['balance'])
        @auth
        def cmd_balance(msg):
            self.telegram.reply_to(
                msg,
                f"💰 *Paper Balance:* `${self.exchange.balance:.4f} USDT`\n"
                f"📈 All-time peak: `${self.risk.peak_balance:.4f}`"
            )

        @bot.message_handler(commands=['pnl'])
        @auth
        def cmd_pnl(msg):
            stats = self.exchange.get_stats()
            if not stats:
                self.telegram.reply_to(msg, "No trades yet — use /decide to kick things off.")
                return
            tp_s = self.adaptive_tp.status()
            self.telegram.reply_to(msg,
                f"📈 *Paper Performance*\n\n"
                f"Trades: `{stats['total_trades']}` | Wins: `{stats['wins']}` | Losses: `{stats['losses']}`\n"
                f"Win rate: `{stats['win_rate']}%`\n"
                f"Total PnL: `{'+'if stats['total_pnl']>0 else ''}{stats['total_pnl']} USDT`\n"
                f"Avg win: `+{stats['avg_win']}` | Avg loss: `{stats['avg_loss']}`\n"
                f"Profit factor: `{stats['profit_factor']}`\n"
                f"Best: `+{stats['best_trade']}` | Worst: `{stats['worst_trade']}`\n\n"
                f"🧠 Avg extraction: `{tp_s['avg_extraction_pct']:.1f}%` of notional (target: 30%)"
            )

        @bot.message_handler(commands=['adaptive'])
        @auth
        def cmd_adaptive(msg):
            self.telegram.reply_to(msg, self.adaptive_tp.format_for_telegram())

        @bot.message_handler(commands=['macro'])
        @auth
        def cmd_macro(msg):
            self.telegram.reply_to(msg, "Fetching macro data...")
            data = self.macro_fetcher.fetch()
            self.telegram.send(self.macro_fetcher.format_for_telegram(data))

        @bot.message_handler(commands=['ai_log'])
        @auth
        def cmd_ai_log(msg):
            decisions = self.ai.get_recent_decisions(5)
            if not decisions:
                self.telegram.reply_to(msg, "No AI decisions logged yet. Use /decide to start.")
                return
            text = "🧠 *Last 5 AI Decisions*\n\n"
            for i, d in enumerate(reversed(decisions), 1):
                sigs = ", ".join(d.get('key_signals', []))
                text += (
                    f"*#{i} {d.get('symbol')} — {d.get('decision')}*\n"
                    f"Confidence: `{d.get('confidence',0):.1%}` @ `${d.get('price',0):,.4f}`\n"
                    f"📝 {d.get('reasoning','')}\n"
                    f"🔑 {sigs}\n"
                    f"⚠️ {d.get('risk_note','')}\n\n"
                )
            self.telegram.reply_to(msg, text)

        @bot.message_handler(commands=['lessons'])
        @auth
        def cmd_lessons(msg):
            if not self.lessons:
                self.telegram.reply_to(msg, "No lessons yet — need to close some trades first.")
                return
            text = "📚 *Lessons learned so far:*\n\n"
            for i, l in enumerate(self.lessons[-10:], 1):
                text += f"{i}. {l}\n"
            self.telegram.reply_to(msg, text)

        @bot.message_handler(commands=['history'])
        @auth
        def cmd_history(msg):
            trades = self.exchange.trade_log[-10:]
            if not trades:
                self.telegram.reply_to(msg, "No trade history yet.")
                return
            text = "📓 *Last 10 Paper Trades*\n\n"
            for t in reversed(trades):
                pnl = t.get('pnl', 0)
                e   = "🟢" if pnl > 0 else "🔴"
                text += (
                    f"{e} {t['symbol']} `{t['side'].upper()}`\n"
                    f"   `${t['entry_price']:,.4f}` → `${t.get('exit_price',0):,.4f}` | "
                    f"`{'+'if pnl>0 else ''}{pnl:.4f}` | {t.get('reason','')}\n\n"
                )
            self.telegram.reply_to(msg, text)

        @bot.message_handler(commands=['toggle'])
        @auth
        def cmd_toggle(msg):
            self.auto_trade = not self.auto_trade
            self.telegram.reply_to(msg,
                "✅ Auto-trading is now *ON* — I'll scan and trade on my own every hour."
                if self.auto_trade else
                "❌ Auto-trading *OFF* — I'll only trade when you use /decide."
            )

        @bot.message_handler(commands=['settings'])
        @auth
        def cmd_settings(msg):
            tp_s = self.adaptive_tp.status()
            self.telegram.reply_to(msg,
                f"⚙️ *Settings*\n\n"
                f"🟡 Mode: *PAPER TRADING*\n\n"
                f"Symbols: `{', '.join(self.symbols)}`\n"
                f"Leverage: `{self.exchange.leverage}x`\n"
                f"Risk per trade: `{self.risk_per_trade*100:.1f}%` of balance\n"
                f"Min ADX (trend filter): `20`\n"
                f"Min AI confidence: `{self.min_confidence:.0%}`\n"
                f"Max daily trades: `{self.risk.max_daily_trades}`\n"
                f"Max drawdown: `{self.risk.max_drawdown_pct*100:.0f}%`\n"
                f"Auto-trade: `{'ON' if self.auto_trade else 'OFF'}`\n\n"
                f"🧠 *Adaptive TP*\n"
                f"TP: `{tp_s['tp_mult']}x ATR` | SL: `{tp_s['sl_mult']}x ATR`\n"
                f"Target: `30% extraction` | Version: `v{tp_s['learning_version']}`\n"
                f"Wins learned from: `{tp_s['wins_learned_from']}`\n\n"
                f"🔒 Trailing stop: activates at `+15%` profit"
            )

        @bot.message_handler(commands=['liquidity'])
        @auth
        def cmd_liquidity(msg):
            parts  = msg.text.strip().split()
            symbol = parts[1].upper() if len(parts) > 1 else "BTC/USDT"
            if "/" not in symbol: symbol += "/USDT"
            self.telegram.reply_to(msg, f"Pulling liquidity data for {symbol}...")
            try:
                price    = self.exchange.fetch_ticker(symbol)['last']
                raw_book = self.exchange.fetch_order_book_raw(symbol, limit=30)
                analysis = self.liquidity_analyzer.analyze(raw_book, price)
                self.telegram.send(self.liquidity_analyzer.format_for_telegram(analysis, symbol))
            except Exception as e:
                self.telegram.send(f"❌ Couldn't fetch liquidity: {e}")


        @bot.message_handler(commands=['ping'])
        @auth
        def cmd_ping(msg):
            uptime, last_scan, last_trade, pos_str, risk, tp_s = self._alive_status_text()
            self.telegram.reply_to(msg,
                f"✅ *Alive and watching.*\n\n"
                f"Uptime: `{uptime}`\n"
                f"Last scan: `{last_scan}` | Last trade: `{last_trade}`\n"
                f"Balance: `${self.exchange.balance:.2f} USDT`\n"
                f"{pos_str}\n"
                f"Auto-trade: `{'ON' if self.auto_trade else 'OFF'}`"
            )

        @bot.message_handler(commands=['alive'])
        @auth
        def cmd_alive(msg):
            uptime, last_scan, last_trade, pos_str, risk, tp_s = self._alive_status_text()
            import sys, os
            mem_mb = 0
            try:
                import resource
                mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            except:
                pass
            self.telegram.reply_to(msg,
                f"🩺 *Full Diagnostics*\n\n"
                f"✅ Status: *Running*\n"
                f"⏱ Uptime: `{uptime}`\n"
                f"🔍 Last scan: `{last_scan}`\n"
                f"💹 Last trade: `{last_trade}`\n"
                f"📊 Trades today: `{risk['trades_today']}/{risk['max_daily']}`\n"
                f"💰 Balance: `${self.exchange.balance:.2f} USDT`\n"
                f"📈 Peak: `${self.risk.peak_balance:.2f}`\n"
                f"{pos_str}\n\n"
                f"🧠 TP engine: `v{tp_s['learning_version']}` | `{tp_s['tp_mult']}x ATR`\n"
                f"Extraction avg: `{tp_s['avg_extraction_pct']:.1f}%` (target 30%)\n"
                f"Wins learned from: `{tp_s['wins_learned_from']}`\n\n"
                f"🤖 Auto-trade: `{'ON' if self.auto_trade else 'OFF'}`\n"
                f"💾 Mem usage: `{mem_mb:.0f} MB`"
            )

        # ── Inline keyboard ────────────────────────────────────────────────────

        @bot.callback_query_handler(func=lambda c: True)
        @auth_cb
        def handle_callback(call):
            m = call.message
            d = call.data
            dispatch = {
                "decide":   lambda: (self.telegram.send(f"🔍 Scanning {len(self.symbols)} pairs..."), threading.Thread(target=self.run_decide_cycle).start()),
                "status":   lambda: cmd_status(m),
                "balance":  lambda: cmd_balance(m),
                "pnl":      lambda: cmd_pnl(m),
                "macro":    lambda: cmd_macro(m),
                "ai_log":   lambda: cmd_ai_log(m),
                "lessons":  lambda: cmd_lessons(m),
                "history":  lambda: cmd_history(m),
                "close":    lambda: cmd_close(m),
                "toggle":   lambda: cmd_toggle(m),
                "settings": lambda: cmd_settings(m),
                "adaptive": lambda: cmd_adaptive(m),
                "ping":     lambda: cmd_ping(m),
            }
            if d in dispatch:
                dispatch[d]()


    # ─── Proactive alive check-in ─────────────────────────────────────────────────

    def _uptime_str(self):
        delta = datetime.now(timezone.utc) - self._startup_time
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m = rem // 60
        if h >= 24:
            return f"{h//24}d {h%24}h"
        return f"{h}h {m}m"

    def _alive_status_text(self):
        uptime   = self._uptime_str()
        pos      = self.exchange.position
        risk     = self.risk.get_status()
        tp_s     = self.adaptive_tp.status()

        last_scan = "Never" if not self._last_scan_time else (
            f"{int((datetime.now(timezone.utc)-self._last_scan_time).total_seconds()//60)}m ago"
        )
        last_trade = "None yet" if not self._last_trade_time else (
            f"{int((datetime.now(timezone.utc)-self._last_trade_time).total_seconds()//60)}m ago"
        )

        pos_str = "📭 No position"
        if pos:
            try:
                curr = self.exchange.fetch_ticker(pos['symbol'])['last']
                unr  = (
                    (curr - pos['entry_price']) * pos['quantity'] * pos['leverage']
                    if pos['side'] == 'long'
                    else (pos['entry_price'] - curr) * pos['quantity'] * pos['leverage']
                )
                pos_str = f"📌 {pos['symbol']} {pos['side'].upper()} | `{'+'if unr>0 else ''}{unr:.2f} USDT`"
            except:
                pos_str = f"📌 {pos['symbol']} {pos['side'].upper()}"

        return (
            uptime, last_scan, last_trade, pos_str,
            risk, tp_s
        )

    def _send_alive_checkin(self):
        """
        Fires every 6 hours IF the bot has been quiet (no trade/alert in last 6h).
        Keeps user confident the bot is alive during slow markets.
        """
        try:
            hours_since = (datetime.now(timezone.utc) - self._last_activity_alert).total_seconds() / 3600
            if hours_since < 5.5:
                return  # Something happened recently, no need to ping

            uptime, last_scan, last_trade, pos_str, risk, tp_s = self._alive_status_text()
            self.telegram.send(
                "Still here \u2014 just a quiet market.\n\n"
                f"Uptime: `{uptime}` | Last scan: `{last_scan}`\n"
                f"Balance: `${self.exchange.balance:.2f} USDT`\n"
                f"{pos_str}\n\n"
                "No setups lately. Watching \u2014 I will alert you the moment something looks good.\n"
                "Use /status for full details or /decide to force a scan."
            )
            self._last_activity_alert = datetime.now(timezone.utc)
        except Exception as e:
            logger.error(f"Alive check-in error: {e}")

    # ─── Startup ──────────────────────────────────────────────────────────────────

    def start(self):
        self._register_handlers()

        # 6-hour alive check-in (fires only if bot was quiet)
        schedule.every(6).hours.do(
            lambda: threading.Thread(target=self._send_alive_checkin).start()
        )

        # Hourly scan
        schedule.every(1).hours.do(
            lambda: threading.Thread(target=self.run_decide_cycle, kwargs={"silent": True}).start()
        )
        # Hourly briefing (offset by 30 min so it doesn't fire at the same time as scan)
        schedule.every(1).hours.do(
            lambda: threading.Thread(target=self._send_hourly_briefing).start()
        )

        threading.Thread(target=self._monitor_loop, daemon=True).start()

        tp_s = self.adaptive_tp.status()
        self.telegram.send(
            f"🚀 *Fearless Futures AI — Online*\n"
            f"📄 Mode: *PAPER TRADING* (no real money)\n\n"
            f"💰 Balance: `${self.exchange.balance:.2f} USDT`\n"
            f"📡 Watching: `{', '.join(self.symbols)}`\n"
            f"🤖 Auto-trade: {'✅ ON' if self.auto_trade else '❌ OFF'}\n"
            f"📊 Trend filter: ADX ≥ 20 (DMI)\n"
            f"🔒 Trailing stop: activates at +15% profit\n"
            f"🧠 Adaptive TP: `{tp_s['tp_mult']}x ATR` → targeting 30% extraction | v{tp_s['learning_version']}\n\n"
            f"I'll text you automatically when anything happens.\n"
            f"Type /help to see all commands.",
            reply_markup=self.telegram.main_keyboard()
        )

        logger.info("FearlessBot online — PAPER MODE. Polling...")
        self.telegram.bot.infinity_polling(timeout=30, long_polling_timeout=20)
