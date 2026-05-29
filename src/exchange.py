import ccxt
import logging
import pandas as pd
from datetime import datetime, timezone
from typing import Optional, Dict, List

logger = logging.getLogger("FearlessFutures.Exchange")

class PaperExchange:
    def __init__(self, initial_balance=1000, symbol="BTC/USDT", timeframe="5m", leverage=10, 
                 exchange_key=None, exchange_secret=None, exchange_passphrase=None):
        self.balance = initial_balance
        self.position: Optional[Dict] = None
        self.trade_log: List[Dict] = []  # closed trades
        self.symbol = symbol
        self.timeframe = timeframe
        self.leverage = leverage
        self.exchange_key = exchange_key
        self.exchange_secret = exchange_secret
        self.exchange_passphrase = exchange_passphrase

    def _real_exchange(self):
        return ccxt.bitget({
            'apiKey': self.exchange_key,
            'secret': self.exchange_secret,
            'password': self.exchange_passphrase,
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })

    def fetch_ticker(self):
        return self._real_exchange().fetch_ticker(self.symbol)

    def fetch_ohlcv(self, timeframe, limit=100):
        ohlcv = self._real_exchange().fetch_ohlcv(self.symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return self._add_indicators(df)

    def _add_indicators(self, df):
        # Add Simple Moving Averages
        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['sma_50'] = df['close'].rolling(window=50).mean()
        
        # Add RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        return df

    def open_position(self, side, qty, tp, sl):
        if self.position:
            return False, "Position already open"
        ticker = self.fetch_ticker()
        entry = ticker['last']
        fee = entry * qty * 0.0006 * 2
        self.balance -= fee
        self.position = {
            "side": side,
            "entry_price": entry,
            "quantity": qty,
            "leverage": self.leverage,
            "tp": tp,
            "sl": sl,
            "time": datetime.now(timezone.utc).isoformat()
        }
        return True, f"Opened {side.upper()} {qty:.4f} @ {entry:.2f}, TP:{tp:.2f}, SL:{sl:.2f}"

    def check_tp_sl(self, price):
        if not self.position:
            return False, None, 0.0
        pos = self.position
        if pos['side'] == 'long':
            if price >= pos['tp']:
                reason = "TP ✅"
            elif price <= pos['sl']:
                reason = "SL ❌"
            else:
                return False, None, 0.0
        else:
            if price <= pos['tp']:
                reason = "TP ✅"
            elif price >= pos['sl']:
                reason = "SL ❌"
            else:
                return False, None, 0.0

        exit_price = price
        fee = pos['quantity'] * exit_price * 0.0006
        if pos['side'] == 'long':
            pnl = (exit_price - pos['entry_price']) * pos['quantity'] * self.leverage - fee
        else:
            pnl = (pos['entry_price'] - exit_price) * pos['quantity'] * self.leverage - fee
        self.balance += pnl
        closed_trade = {**self.position, "exit_price": exit_price, "pnl": pnl, "reason": reason}
        self.trade_log.append(closed_trade)
        self.position = None
        return True, reason, pnl

    def close_now(self, price):
        if not self.position:
            return False, "No open position"
        pos = self.position
        fee = pos['quantity'] * price * 0.0006
        if pos['side'] == 'long':
            pnl = (price - pos['entry_price']) * pos['quantity'] * self.leverage - fee
        else:
            pnl = (pos['entry_price'] - price) * pos['quantity'] * self.leverage - fee
        self.balance += pnl
        self.trade_log.append({**self.position, "exit_price": price, "pnl": pnl, "reason": "Manual close"})
        self.position = None
        return True, f"Closed manually, PnL: {pnl:.2f}"
