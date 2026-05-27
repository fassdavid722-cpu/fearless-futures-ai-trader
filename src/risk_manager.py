import logging
from datetime import datetime, timezone

logger = logging.getLogger("FearlessFutures.RiskManager")

class RiskManager:
    def __init__(self, initial_balance, max_daily_trades=5, max_drawdown_pct=0.5):
        self.trade_count_today = 0
        self.last_day = datetime.now(timezone.utc).day
        self.peak_balance = initial_balance
        self.max_daily_trades = max_daily_trades
        self.max_drawdown_pct = max_drawdown_pct

    def reset_daily(self):
        today = datetime.now(timezone.utc).day
        if today != self.last_day:
            self.trade_count_today = 0
            self.last_day = today

    def can_trade(self, current_balance):
        self.reset_daily()
        # Drawdown check
        if current_balance < self.peak_balance * (1 - self.max_drawdown_pct):
            return False, f"Max drawdown hit ({self.max_drawdown_pct*100}%)"
        # Daily trade limit
        if self.trade_count_today >= self.max_daily_trades:
            return False, "Daily trade limit reached"
        return True, "OK"

    def update_peak(self, current_balance):
        self.peak_balance = max(self.peak_balance, current_balance)
