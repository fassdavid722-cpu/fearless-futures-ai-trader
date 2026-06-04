import logging
from datetime import datetime, timezone

logger = logging.getLogger("FearlessFutures.RiskManager")

class RiskManager:
    def __init__(self, initial_balance, max_daily_trades=6, max_drawdown_pct=0.40):
        self.trade_count_today = 0
        self.last_day = datetime.now(timezone.utc).day
        self.peak_balance = initial_balance
        self.max_daily_trades = max_daily_trades
        self.max_drawdown_pct = max_drawdown_pct
        self.consecutive_losses = 0
        self.max_consecutive_losses = 3  # cool-down after 3 in a row

    def reset_daily(self):
        today = datetime.now(timezone.utc).day
        if today != self.last_day:
            self.trade_count_today = 0
            self.last_day = today
            logger.info("Daily trade counter reset.")

    def can_trade(self, current_balance) -> tuple:
        self.reset_daily()
        drawdown = (self.peak_balance - current_balance) / self.peak_balance
        if drawdown >= self.max_drawdown_pct:
            return False, f"Max drawdown hit ({drawdown*100:.1f}% — limit {self.max_drawdown_pct*100:.0f}%)"
        if self.trade_count_today >= self.max_daily_trades:
            return False, f"Daily limit reached ({self.max_daily_trades} trades)"
        if self.consecutive_losses >= self.max_consecutive_losses:
            return False, f"Cooling down after {self.consecutive_losses} consecutive losses 🧊"
        return True, "OK"

    def record_trade_result(self, pnl: float):
        if pnl > 0:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

    def update_peak(self, current_balance):
        if current_balance > self.peak_balance:
            self.peak_balance = current_balance

    def get_status(self) -> dict:
        return {
            "trades_today": self.trade_count_today,
            "max_daily": self.max_daily_trades,
            "consecutive_losses": self.consecutive_losses,
            "peak_balance": round(self.peak_balance, 2),
            "max_drawdown_pct": self.max_drawdown_pct * 100
        }
