import json
import logging
from groq import Groq

logger = logging.getLogger("FearlessFutures.AIBrain")

SYSTEM_PROMPT = """
You are a fearless, expert crypto futures trader. Your goal is to maximize profit on Bitget.
You must analyze the data and provide a decision in JSON format.
You are aggressive but disciplined. You MUST choose either LONG or SHORT.
Your output must be exactly this JSON structure:
{"decision": "LONG"|"SHORT", "confidence": 0.0-1.0, "reasoning": "one short sentence"}
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

    def get_decision(self, price, ohlcv_df, balance, has_position, timeframe, learning_context="", lessons=""):
        prompt = f"""
Current Price: {price}
Balance: {balance}
Timeframe: {timeframe}
Recent History: {ohlcv_df.tail(5).to_dict()}
{learning_context}

Lessons from past mistakes/successes:
{lessons}

Analyze the data and provide your decision.
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
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Reflection error: {e}")
            return "Be more careful with volatility."
