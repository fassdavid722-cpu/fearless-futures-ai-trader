import os
import logging
from dotenv import load_dotenv
from src.bot import FearlessBot

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s"
)
logger = logging.getLogger("FearlessFutures")

config = {
    # Assets to scan
    "SYMBOLS": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "NEAR/USDT", "BNB/USDT"],

    # Trading parameters
    "LEVERAGE": 10,
    "RISK_PER_TRADE": 0.01,         # 1% of balance per trade
    "TP_PCT": 0.025,                 # fallback TP (ATR-based preferred)
    "SL_PCT": 0.012,                 # fallback SL
    "MIN_CONFIDENCE": 0.68,          # minimum AI confidence to execute
    "MAX_DAILY_TRADES": 6,
    "MAX_DRAWDOWN_PCT": 0.40,        # halt at 40% drawdown
    "MONITOR_INTERVAL": 60,          # seconds between position checks
    "AUTO_TRADE": True,
    "INITIAL_BALANCE": 1000,

    # API keys (from .env)
    "GROQ_API_KEY":         os.getenv("GROQ_API_KEY"),
    "TELEGRAM_BOT_TOKEN":   os.getenv("TELEGRAM_BOT_TOKEN"),
    "YOUR_CHAT_ID":         os.getenv("YOUR_CHAT_ID"),
    "BITGET_API_KEY":       os.getenv("BITGET_API_KEY"),
    "BITGET_SECRET_KEY":    os.getenv("BITGET_SECRET_KEY"),
    "BITGET_PASSPHRASE":    os.getenv("BITGET_PASSPHRASE"),
}

REQUIRED = ["GROQ_API_KEY", "TELEGRAM_BOT_TOKEN", "YOUR_CHAT_ID",
            "BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"]

missing = [k for k in REQUIRED if not config[k]]

if __name__ == "__main__":
    if missing:
        logger.error(f"Missing env vars: {', '.join(missing)}")
        print(f"\n❌ Set these in your .env file:\n" + "\n".join(f"  {k}=..." for k in missing))
    else:
        logger.info("All env vars present. Starting bot...")
        FearlessBot(config).start()
