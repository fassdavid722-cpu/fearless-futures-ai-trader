# Fearless Futures AI Trader (Bitget Edition) 🤖

An autonomous, aggressive crypto futures paper-trading bot powered by AI. This bot runs 24/7 on free infrastructure, controlled via Telegram, with a fearless AI brain, self-monitoring TP/SL, and a learning memory that feeds its own performance back into every decision.

## Features 🚀

- **Aggressive AI**: Powered by Groq (Llama 3), forced to call LONG or SHORT every time.
- **Bitget Integration**: Uses Bitget for market data and simulated execution.
- **24/7 Monitoring**: Checks open positions every 60 seconds and auto-closes when TP/SL hit.
- **Telegram Control**: Command-based control via Telegram (/decide, /status, /close, /balance, /history).
- **Risk Management**: Built-in daily trade limits and drawdown protection.
- **Learning Loop**: Stores trade outcomes and feeds them back into the AI for better decision-making.

## Project Structure 📁

```
fearless-futures-ai-trader/
├── src/
│   ├── ai_brain.py         # AI decision logic via Groq
│   ├── bot.py              # Main bot orchestration
│   ├── exchange.py         # Bitget exchange interface
│   ├── risk_manager.py     # Risk and drawdown management
│   └── telegram_handler.py # Telegram bot interaction
├── main.py                 # Entry point
├── requirements.txt        # Dependencies
└── .env                    # Environment variables (not tracked)
```

## Setup Instructions 🛠️

### 1. Get Your API Keys
- **GroqCloud**: Get an API key from [console.groq.com](https://console.groq.com).
- **Telegram**: Create a bot with [@BotFather](https://t.me/BotFather) and get your token and chat ID.
- **Bitget**: Generate API keys from your Bitget account (ensure you have the Key, Secret, and Passphrase).

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment
Create a `.env` file in the root directory:
```env
GROQ_API_KEY=your_groq_key
TELEGRAM_BOT_TOKEN=your_telegram_token
YOUR_CHAT_ID=your_chat_id
BITGET_API_KEY=your_bitget_key
BITGET_SECRET_KEY=your_bitget_secret
BITGET_PASSPHRASE=your_bitget_passphrase
```

### 4. Run the Bot
```bash
python main.py
```

## Disclaimer ⚠️
This bot is for educational and paper-trading purposes only. Use it with real funds at your own risk.
