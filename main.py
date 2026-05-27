import os
import logging
from dotenv import load_dotenv
from src.bot import FearlessBot

# Load environment variables
load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FearlessFutures")

# Configuration
config = {
    "SYMBOL": "BTC/USDT",
    "TIMEFRAME": "5m",
    "LEVERAGE": 10,
    "RISK_PER_TRADE": 0.01,
    "TP_PCT": 0.02,
    "SL_PCT": 0.01,
    "MIN_CONFIDENCE": 0.65,
    "MAX_DAILY_TRADES": 5,
    "MAX_DRAWDOWN_PCT": 0.50,
    "MONITOR_INTERVAL": 60,
    "INITIAL_BALANCE": 1000,
    
    # Environment variables
    "GROQ_API_KEY": os.getenv("GROQ_API_KEY"),
    "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN"),
    "YOUR_CHAT_ID": os.getenv("YOUR_CHAT_ID"),
    "BINANCE_FUTURES_TESTNET_KEY": os.getenv("BINANCE_FUTURES_TESTNET_KEY"),
    "BINANCE_FUTURES_TESTNET_SECRET": os.getenv("BINANCE_FUTURES_TESTNET_SECRET")
}

# Check for required environment variables
required_vars = [
    "GROQ_API_KEY", "TELEGRAM_BOT_TOKEN", "YOUR_CHAT_ID", 
    "BINANCE_FUTURES_TESTNET_KEY", "BINANCE_FUTURES_TESTNET_SECRET"
]
missing_vars = [var for var in required_vars if not config[var]]

if __name__ == "__main__":
    if missing_vars:
        logger.error(f"Missing environment variables: {', '.join(missing_vars)}")
        print(f"Please set the following environment variables in a .env file:\n{', '.join(missing_vars)}")
    else:
        bot = FearlessBot(config)
        bot.start()
