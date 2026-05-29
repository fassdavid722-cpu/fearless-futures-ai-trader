import ccxt
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Dict, List

logger = logging.getLogger("FearlessFutures.Exchange")

class PaperExchange:
    def __init__(self, initial_balance=1000, symbol="BTC/USDT", timeframe="5m", leverage=10, 
                 exchange_key=None, exchange_secret=None, exchange_passphrase=None):
        self.balance = initial_balance
        self.position: Optional[Dict] = None
        self.trade_log: List[Dict] = []
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

    def fetch_order_book(self, limit=20):
        try:
            book = self._real_exchange().fetch_order_book(self.symbol, limit=limit)
            bids = book['bids']
            asks = book['asks']
            bid_vol = sum([b[1] for b in bids])
            ask_vol = sum([a[1] for a in asks])
            imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
            return {
                "top_bid": bids[0][0] if bids else None,
                "top_ask": asks[0][0] if asks else None,
                "bid_volume": bid_vol,
                "ask_volume": ask_vol,
                "imbalance": imbalance
            }
        except Exception as e:
            logger.error(f"Error fetching order book: {e}")
            return None

    def fetch_ohlcv(self, timeframe, limit=100):
        warmup_limit = limit + 50
        ohlcv = self._real_exchange().fetch_ohlcv(self.symbol, timeframe, limit=warmup_limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = self._add_indicators(df)
        return df.tail(limit).reset_index(drop=True)

    def _add_indicators(self, df):
        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['sma_50'] = df['close'].rolling(window=50).mean()
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        df = df.replace([np.inf, -np.inf], np.nan)
        df['rsi'] = df['rsi'].fillna(50)
        df['sma_20'] = df['sma_20'].fillna(df['close'])
        df['sma_50'] = df['sma_50'].fillna(df['close'])
        return df

    def open_position(self, side, qty, tp, sl):
        if self.position: return False, "Position already open"
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
        if not self.position: return False, None, 0.0
        pos = self.position
        if pos['side'] == 'long':
            if price >= pos['tp']: reason = "TP ✅"
            elif price <= pos['sl']: reason = "SL ❌"
            else: return False, None, 0.0
        else:
            if price <= pos['tp']: reason = "TP ✅"
            elif price >= pos['sl']: reason = "SL ❌"
            else: return False, None, 0.0

        exit_price = price
        fee = pos['quantity'] * exit_price * 0.0006
        pnl = (exit_price - pos['entry_price']) * pos['quantity'] * self.leverage - fee if pos['side'] == 'long' else (pos['entry_price'] - exit_price) * pos['quantity'] * self.leverage - fee
        self.balance += pnl
        self.trade_log.append({**self.position, "exit_price": exit_price, "pnl": pnl, "reason": reason})
        self.position = None
        return True, reason, pnl

    def close_now(self, price):
        if not self.position: return False, "No open position"
        pos = self.position
        fee = pos['quantity'] * price * 0.0006
        pnl = (price - pos['entry_price']) * pos['quantity'] * self.leverage - fee if pos['side'] == 'long' else (pos['entry_price'] - price) * pos['quantity'] * self.leverage - fee
        self.balance += pnl
        self.trade_log.append({**self.position, "exit_price": price, "pnl": pnl, "reason": "Manual close"})
        self.position = None
        return True, f"Closed manually, PnL: {pnl:.2f}"
