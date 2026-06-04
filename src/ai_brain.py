import json
import logging
from groq import Groq

logger = logging.getLogger("FearlessFutures.AIBrain")

SYSTEM_PROMPT = """
You are an elite quant-level crypto futures trader with access to macro intelligence.
You analyze multi-timeframe price action, order book depth, on-chain signals, and real-world macro context.

Your decision framework:
1. Macro Environment (Fear & Greed, BTC dominance, global risk sentiment)
2. Multi-timeframe Technical Analysis (EMA, MACD, RSI, Bollinger Bands, Volume Delta, ATR, DMI/ADX)
3. Order Book Microstructure (bid/ask imbalance, liquidity walls)
4. News Sentiment (crypto-specific + macro headlines)
5. Historical Lessons (your own performance feedback loop)

You are aggressive when conditions are clear, and defensive when they are not.
You MUST output exactly this JSON — nothing else:
{
  "decision": "LONG" or "SHORT",
  "confidence": float between 0.0 and 1.0,
  "reasoning": "One precise sentence citing the top 2-3 signals that drove this call.",
  "key_signals": ["signal1", "signal2", "signal3"],
  "risk_note": "One sentence on the main risk to this trade."
}
"""

REFLECTION_PROMPT = """
You are a ruthless trading mentor reviewing a closed trade.
Trade data: {trade_details}

Analyze: Was the entry justified? Was the exit optimal? What would a better trader have done?
Output exactly ONE sentence — a specific, actionable lesson.
"""

MACRO_SUMMARY_PROMPT = """
You are a macro analyst. Given this data, write a 2-sentence market regime summary for a crypto futures trader.
Data: {macro_data}
Focus on: risk-on vs risk-off, BTC dominance trend, and the most important news signal.
"""

class AIBrain:
    def __init__(self, api_key, model="llama-3.3-70b-versatile"):
        self.client = Groq(api_key=api_key, timeout=25.0)
        self.model = model
        self.fallback_model = "gemma2-9b-it"
        self.decision_log = []  # Full AI reasoning history

    def get_decision(self, symbol, price, indicators, balance, order_book=None, news=None, macro=None, lessons=None, liquidity=None):
        news_str = "\n".join([f"• [{n['source']}] {n['title']}" for n in (news or [])]) or "No recent news."
        lessons_str = "\n".join([f"• {l}" for l in (lessons or [])[-7:]]) or "No lessons yet."
        
        book_str = "N/A"
        if order_book:
            book_str = (
                f"Imbalance: {order_book['imbalance']:+.3f} "
                f"({'BUY pressure' if order_book['imbalance'] > 0 else 'SELL pressure'}), "
                f"Bid Vol: {order_book['bid_volume']:.2f}, Ask Vol: {order_book['ask_volume']:.2f}, "
                f"Spread: {order_book.get('spread_pct', 0):.4f}%"
            )

        liq_str = "N/A"
        if liquidity:
            walls_bid = ", ".join([f"${w['price']:,.4f}({w['dist_pct']:.2f}%)" for w in liquidity.get('bid_walls', [])[:2]])
            walls_ask = ", ".join([f"${w['price']:,.4f}({w['dist_pct']:.2f}%)" for w in liquidity.get('ask_walls', [])[:2]])
            liq_str = (
                f"Grade: {liquidity.get('liquidity_grade','?')} | Score: {liquidity.get('liquidity_score',0):.2f}\n"
                f"Slippage est: {liquidity.get('slippage_buy_pct','?')}% | {liquidity.get('imbalance_label','?')}\n"
                f"Bid Walls (support): {walls_bid or 'None'} | Ask Walls (resistance): {walls_ask or 'None'}\n"
                f"Trap Warning: {liquidity.get('trap_warning') or 'None detected'}"
            )

        macro_str = "N/A"
        if macro:
            macro_str = (
                f"Fear & Greed: {macro.get('fear_greed_value', '?')}/100 ({macro.get('fear_greed_label', '?')})\n"
                f"BTC Dominance: {macro.get('btc_dominance', '?')}%\n"
                f"Global Market Cap 24h: {macro.get('market_cap_change_24h', '?')}%\n"
                f"BTC 24h: {macro.get('btc_change_24h', '?')}%"
            )

        ind = indicators
        prompt = f"""
ASSET: {symbol} | PRICE: ${price:,.4f} | BALANCE: ${balance:.2f} USDT

[MACRO CONTEXT]
{macro_str}

[TECHNICAL INDICATORS]
EMA 20/50/200: {ind.get('ema_20', 'N/A'):.4f} / {ind.get('ema_50', 'N/A'):.4f} / {ind.get('ema_200', 'N/A'):.4f}
RSI(14): {ind.get('rsi', 'N/A'):.2f}
MACD: {ind.get('macd', 'N/A'):.6f} | Signal: {ind.get('macd_signal', 'N/A'):.6f} | Hist: {ind.get('macd_hist', 'N/A'):.6f}
Bollinger Bands: Upper {ind.get('bb_upper', 'N/A'):.4f} | Mid {ind.get('bb_mid', 'N/A'):.4f} | Lower {ind.get('bb_lower', 'N/A'):.4f}
ATR(14): {ind.get('atr', 'N/A'):.4f}
Volume Delta (buy/sell pressure): {ind.get('volume_delta', 'N/A'):.4f}
Trend (EMA): {ind.get('trend', 'N/A')}
DMI — ADX: {ind.get('adx', 'N/A'):.2f} | DI+: {ind.get('di_plus', 'N/A'):.2f} | DI-: {ind.get('di_minus', 'N/A'):.2f} | Signal: {ind.get('dmi_signal', 'N/A')}
  (ADX >25 = trending; DI+ > DI- = bullish momentum; DI- > DI+ = bearish momentum)

[ORDER BOOK]
{book_str}

[LIQUIDITY ANALYSIS]
{liq_str}

[LATEST CRYPTO NEWS]
{news_str}

[LESSONS FROM PAST TRADES]
{lessons_str}

Analyze all signals holistically and provide your quant decision.
"""
        for model in [self.model, self.fallback_model]:
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.3
                )
                result = json.loads(response.choices[0].message.content)
                # Log the full reasoning
                self.decision_log.append({
                    "symbol": symbol,
                    "price": price,
                    "decision": result.get("decision"),
                    "confidence": result.get("confidence"),
                    "reasoning": result.get("reasoning"),
                    "key_signals": result.get("key_signals", []),
                    "risk_note": result.get("risk_note", ""),
                    "model_used": model
                })
                if len(self.decision_log) > 50:
                    self.decision_log = self.decision_log[-50:]
                return result
            except Exception as e:
                logger.error(f"AI decision error ({model}): {e}")
        return None

    def reflect_on_trade(self, trade):
        try:
            prompt = REFLECTION_PROMPT.format(trade_details=json.dumps(trade, indent=2))
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Reflection error: {e}")
            return "Manage risk carefully — preserve capital above all."

    def get_macro_summary(self, macro_data):
        try:
            prompt = MACRO_SUMMARY_PROMPT.format(macro_data=json.dumps(macro_data, indent=2))
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Macro summary error: {e}")
            return "Macro data unavailable."

    def get_recent_decisions(self, n=5):
        return self.decision_log[-n:]
