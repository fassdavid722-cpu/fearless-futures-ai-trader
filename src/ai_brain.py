import json
import logging
from groq import Groq

logger = logging.getLogger("FearlessFutures.AIBrain")

SYSTEM_PROMPT = """
You are an aggressive crypto futures trader. You analyze market data and you MUST decide LONG or SHORT.
No HOLD. No indecision. Base your call on recent price action.
Answer ONLY with a JSON object:
{"decision": "LONG"|"SHORT", "confidence": 0.0-1.0, "reasoning": "one short sentence"}
"""

class AIBrain:
    def __init__(self, api_key, model="llama-3.3-70b-versatile"):
        self.client = Groq(api_key=api_key)
        self.model = model

    def get_decision(self, price, ohlcv_df, balance, has_position, timeframe, learning_context=""):
        recent_ohlcv = ohlcv_df.tail(5).to_string()
        prompt = f"""
Current price: {price:.2f} USDT
Recent {timeframe} candles:
{recent_ohlcv}
Account: Balance {balance:.2f}, Open position: {"Yes" if has_position else "No"}
{learning_context}
Your decision (LONG/SHORT):
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=200
            )
            raw = response.choices[0].message.content
            return self._parse_decision(raw)
        except Exception as e:
            logger.error(f"Groq API error: {e}")
            return None

    def _parse_decision(self, raw):
        try:
            json_start = raw.rfind('{')
            data = json.loads(raw[json_start:])
            return data
        except Exception:
            return None
