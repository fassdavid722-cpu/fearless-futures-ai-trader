import ccxt
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Dict, List

logger = logging.getLogger("FearlessFutures.Exchange")

class PaperExchange:
    def __init__(self, initial_balance=1000, timeframe="5m", leverage=10,
                 exchange_key=None, exchange_secret=None, exchange_passphrase=None):
        self.balance = initial_balance
        self.position: Optional[Dict] = None
        self.trade_log: List[Dict] = []
        self.timeframe = timeframe
        self.leverage = leverage
        self._exchange_key = exchange_key
        self._exchange_secret = exchange_secret
        self._exchange_passphrase = exchange_passphrase
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = ccxt.bitget({
                'apiKey': self._exchange_key,
                'secret': self._exchange_secret,
                'password': self._exchange_passphrase,
                'enableRateLimit': True,
                'options': {'defaultType': 'swap'}
            })
        return self._client

    def fetch_ticker(self, symbol):
        return self._get_client().fetch_ticker(symbol)

    def fetch_order_book(self, symbol, limit=20):
        try:
            book = self._get_client().fetch_order_book(symbol, limit=limit)
            bids, asks = book['bids'], book['asks']
            bid_vol = sum(b[1] for b in bids)
            ask_vol = sum(a[1] for a in asks)
            imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0
            spread_pct = ((asks[0][0] - bids[0][0]) / bids[0][0]) * 100 if bids and asks else 0
            return {
                "top_bid": bids[0][0] if bids else 0,
                "top_ask": asks[0][0] if asks else 0,
                "bid_volume": bid_vol,
                "ask_volume": ask_vol,
                "imbalance": imbalance,
                "spread_pct": spread_pct
            }
        except Exception as e:
            logger.error(f"Order book error ({symbol}): {e}")
            return None

    def fetch_ohlcv(self, symbol, timeframe, limit=100):
        warmup = limit + 210  # enough for EMA200
        ohlcv = self._get_client().fetch_ohlcv(symbol, timeframe, limit=warmup)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = self._add_indicators(df)
        return df.tail(limit).reset_index(drop=True)

    def _add_indicators(self, df):
        close = df['close']
        high = df['high']
        low = df['low']
        volume = df['volume']

        # EMAs
        df['ema_20'] = close.ewm(span=20, adjust=False).mean()
        df['ema_50'] = close.ewm(span=50, adjust=False).mean()
        df['ema_200'] = close.ewm(span=200, adjust=False).mean()

        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        df['rsi'] = (100 - (100 / (1 + rs))).fillna(50)

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df['macd'] = ema12 - ema26
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']

        # Bollinger Bands
        df['bb_mid'] = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        df['bb_upper'] = df['bb_mid'] + 2 * bb_std
        df['bb_lower'] = df['bb_mid'] - 2 * bb_std

        # ATR
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        df['atr'] = tr.rolling(14).mean()

        # Volume Delta (proxy: up candles = buy pressure)
        df['volume_delta'] = np.where(close > df['open'], volume, -volume)

        # Trend label
        df['trend'] = np.where(
            (df['ema_20'] > df['ema_50']) & (df['ema_50'] > df['ema_200']), 'STRONG UPTREND',
            np.where(
                (df['ema_20'] < df['ema_50']) & (df['ema_50'] < df['ema_200']), 'STRONG DOWNTREND',
                np.where(df['ema_20'] > df['ema_50'], 'UPTREND', 'DOWNTREND')
            )
        )

        return df.replace([np.inf, -np.inf], np.nan).ffill()

    def get_latest_indicators(self, df):
        row = df.iloc[-1]
        return {
            "ema_20": row['ema_20'],
            "ema_50": row['ema_50'],
            "ema_200": row['ema_200'],
            "rsi": row['rsi'],
            "macd": row['macd'],
            "macd_signal": row['macd_signal'],
            "macd_hist": row['macd_hist'],
            "bb_upper": row['bb_upper'],
            "bb_mid": row['bb_mid'],
            "bb_lower": row['bb_lower'],
            "atr": row['atr'],
            "volume_delta": row['volume_delta'],
            "trend": row['trend']
        }

    def open_position(self, symbol, side, qty, tp, sl, atr=None):
        if self.position:
            return False, "Position already open."
        ticker = self.fetch_ticker(symbol)
        entry = ticker['last']
        fee = entry * qty * 0.0006 * 2
        self.balance -= fee
        self.position = {
            "symbol": symbol,
            "side": side,
            "entry_price": entry,
            "quantity": qty,
            "leverage": self.leverage,
            "tp": tp,
            "sl": sl,
            "atr_at_entry": atr,
            "time": datetime.now(timezone.utc).isoformat()
        }
        return True, (
            f"Opened {side.upper()} {qty:.4f} {symbol} @ ${entry:,.4f}\n"
            f"TP: ${tp:,.4f} | SL: ${sl:,.4f}\n"
            f"Leverage: {self.leverage}x | Fee: ${fee:.4f}"
        )

    def check_tp_sl(self, price):
        if not self.position:
            return False, None, 0.0
        pos = self.position
        hit = False
        reason = None
        if pos['side'] == 'long':
            if price >= pos['tp']:   reason, hit = "TP ✅", True
            elif price <= pos['sl']: reason, hit = "SL 🛑", True
        else:
            if price <= pos['tp']:   reason, hit = "TP ✅", True
            elif price >= pos['sl']: reason, hit = "SL 🛑", True
        if not hit:
            return False, None, 0.0
        return self._close_position(price, reason)

    def close_now(self, price):
        if not self.position:
            return False, "No open position."
        return self._close_position(price, "Manual close 🤙")

    def _close_position(self, price, reason):
        pos = self.position
        fee = pos['quantity'] * price * 0.0006
        if pos['side'] == 'long':
            pnl = (price - pos['entry_price']) * pos['quantity'] * self.leverage - fee
        else:
            pnl = (pos['entry_price'] - price) * pos['quantity'] * self.leverage - fee
        self.balance += pnl
        closed = {**pos, "exit_price": price, "pnl": round(pnl, 4), "reason": reason,
                  "exit_time": datetime.now(timezone.utc).isoformat()}
        self.trade_log.append(closed)
        self.position = None
        return True, reason, pnl

    def get_stats(self):
        if not self.trade_log:
            return {}
        pnls = [t['pnl'] for t in self.trade_log]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / len(pnls) * 100
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        profit_factor = abs(sum(wins) / sum(losses)) if sum(losses) != 0 else float('inf')
        return {
            "total_trades": len(pnls),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "total_pnl": round(sum(pnls), 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 2),
            "best_trade": round(max(pnls), 4),
            "worst_trade": round(min(pnls), 4)
        }

    def fetch_order_book_raw(self, symbol, limit=30):
        """Returns raw ccxt order book for LiquidityAnalyzer."""
        try:
            return self._get_client().fetch_order_book(symbol, limit=limit)
        except Exception as e:
            logger.error(f"Raw order book error ({symbol}): {e}")
            return {"bids": [], "asks": []}
