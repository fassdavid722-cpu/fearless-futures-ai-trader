import json
import logging
from groq import Groq

logger = logging.getLogger("FearlessFutures.AIBrain")

SYSTEM_PROMPT = """
You are a QUANT-LEVEL crypto futures trader. Your goal is to maximize profit on Bitget.
You must analyze:
1. Price Action & Indicators
2. Order Book Imbalance (Market Depth)
3. Latest Market Sentiment (News)
4. Past Lessons Learned

You are aggressive but disciplined. You MUST choose either LONG or SHORT.
Your output must be exactly this JSON structure:
{"decision": "LONG"|"SHORT", "confidence": 0.0-1.0, "reasoning": "one short sentence explaining the quant-based reason"}
"""

REFLECTION_PROMPT = """
You are a trading mentor. Analyze this closed trade and provide a one-sentence lesson.
Trade: {trade_details}
Why did it win or lose? What should we avoid next time?
Output only the one-sentence lesson.
"""

class AIBrain:
    def __init__(self, api_key, model="llama-3.3-70b-versatile"):
        self.client = Groq(api_key=api_key)
        self.model = model

    def get_decision(self, price, ohlcv_df, balance, timeframe, learning_context="", lessons="", order_book=None, news=None):
        news_str = "\n".join([f"- {n['title']}" for n in news]) if news else "No recent news."
        book_str = f"Imbalance: {order_book['imbalance']:.2f}, Bid Vol: {order_book['bid_volume']:.2f}, Ask Vol: {order_book['ask_volume']:.2f}" if order_book else "N/A"
        
        prompt = f"""
Current Price: {price}
Balance: {balance}
Timeframe: {timeframe}

[Indicators]
{learning_context}

[Order Book]
{book_str}

[Sentiment/News]
{news_str}

[Lessons Learned]
{lessons}

Analyze all data and provide your quant decision.
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"Groq API error: {e}")
            return None

    def reflect_on_trade(self, trade):
        try:
            prompt = REFLECTION_PROMPT.format(trade_details=json.dumps(trade))
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Reflection error: {e}")
            return "Be more careful with volatility."
