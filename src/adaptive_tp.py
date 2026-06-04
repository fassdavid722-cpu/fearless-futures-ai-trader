"""
Adaptive TP (Take-Profit) Engine
──────────────────────────────────
The bot starts with a default ATR-based TP multiplier.
As it accumulates trade data, it learns what TP distance actually gets hit
vs what gets reversed — and adapts.

The long-term goal: reliably extract ≥30% of entry notional as profit
on winning trades, by tuning the TP multiplier based on real performance.

How it works:
  - Each closed win teaches the engine how far price actually ran (run_ratio)
  - Over time it builds a rolling average of run_ratios
  - TP multiplier is adjusted so that ~70th percentile run = TP (conservative but reachable)
  - A separate "extraction rate" tracks: avg_win_pnl / avg_entry_notional
  - Once extraction_rate >= 0.30 (30%), the engine is considered "mature"
  - Below 30% it nudges TP multiplier down slightly to increase hit rate
  - Above 30% with decent win rate it nudges up to capture more

Stored persistently in state.json under key "adaptive_tp".
"""

import logging
import statistics

logger = logging.getLogger("FearlessFutures.AdaptiveTP")

DEFAULT_TP_MULT   = 2.5   # ATR multiplier for TP (starting point)
DEFAULT_SL_MULT   = 1.5   # ATR multiplier for SL (kept more stable)
MIN_TP_MULT       = 1.2   # never go below this
MAX_TP_MULT       = 6.0   # never go above this
MIN_TRADES_TO_LEARN = 5   # need at least this many trades before adapting
TARGET_EXTRACTION = 0.30  # target: 30% of entry notional extracted on wins


class AdaptiveTPEngine:
    def __init__(self, state: dict = None):
        s = state or {}
        self.tp_mult          = s.get('tp_mult',         DEFAULT_TP_MULT)
        self.sl_mult          = s.get('sl_mult',         DEFAULT_SL_MULT)
        self.run_ratios       = s.get('run_ratios',       [])   # how far price ran vs ATR on wins
        self.extraction_rates = s.get('extraction_rates', [])   # pnl/entry_notional on wins
        self.version          = s.get('version',          0)    # how many times it's self-updated

    # ── TP/SL calculation ─────────────────────────────────────────────────────

    def calculate_levels(self, price: float, side: str, atr: float,
                         entry_notional: float) -> dict:
        """
        Returns TP, SL prices and the target profit in USDT (30% of notional).
        """
        tp_dist = atr * self.tp_mult
        sl_dist = atr * self.sl_mult

        if side == 'long':
            tp = price + tp_dist
            sl = price - sl_dist
        else:
            tp = price - tp_dist
            sl = price + sl_dist

        target_profit_usdt = entry_notional * TARGET_EXTRACTION

        return {
            "tp":                  round(tp, 6),
            "sl":                  round(sl, 6),
            "tp_dist":             round(tp_dist, 6),
            "sl_dist":             round(sl_dist, 6),
            "tp_mult_used":        round(self.tp_mult, 3),
            "sl_mult_used":        round(self.sl_mult, 3),
            "target_profit_usdt":  round(target_profit_usdt, 4),
            "rr_ratio":            round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0,
        }

    # ── Learning from closed trades ───────────────────────────────────────────

    def learn(self, closed_trade: dict, atr_at_entry: float) -> dict:
        """
        Called after every closed trade. Updates internal learning state.
        Returns a summary of what changed.
        """
        pnl            = closed_trade.get('pnl', 0)
        entry_price    = closed_trade.get('entry_price', 0)
        exit_price     = closed_trade.get('exit_price', 0)
        entry_notional = closed_trade.get('entry_notional', 0)
        side           = closed_trade.get('side', 'long')
        reason         = closed_trade.get('reason', '')

        is_win = pnl > 0

        if is_win and atr_at_entry and atr_at_entry > 0 and entry_notional > 0:
            # How far did price actually run (as ATR multiples)?
            price_run = abs(exit_price - entry_price)
            run_ratio = price_run / atr_at_entry
            self.run_ratios.append(run_ratio)

            # Extraction rate: what % of entry notional was captured
            extraction = pnl / entry_notional
            self.extraction_rates.append(extraction)

            # Keep rolling window of last 30 winning trades
            if len(self.run_ratios)       > 30: self.run_ratios       = self.run_ratios[-30:]
            if len(self.extraction_rates) > 30: self.extraction_rates = self.extraction_rates[-30:]

        changed = self._adapt()
        return changed

    def _adapt(self) -> dict:
        """Core learning logic — adjust TP multiplier based on real data."""
        n = len(self.run_ratios)
        if n < MIN_TRADES_TO_LEARN:
            return {"adapted": False, "reason": f"Need {MIN_TRADES_TO_LEARN - n} more winning trades to learn"}

        old_tp_mult = self.tp_mult
        avg_extraction = statistics.mean(self.extraction_rates) if self.extraction_rates else 0
        avg_run_ratio  = statistics.mean(self.run_ratios)       if self.run_ratios       else 0

        # Percentile-based: use 60th pct of actual runs as our TP target
        sorted_runs = sorted(self.run_ratios)
        idx_60 = max(0, int(len(sorted_runs) * 0.60) - 1)
        p60_run = sorted_runs[idx_60]

        # Nudge logic
        if avg_extraction < TARGET_EXTRACTION * 0.8:
            # We're extracting less than 24% — TP is too far, reduce it
            new_mult = max(MIN_TP_MULT, self.tp_mult * 0.92)
            reason   = f"Extraction {avg_extraction:.1%} < target — bringing TP closer"
        elif avg_extraction >= TARGET_EXTRACTION and avg_run_ratio > self.tp_mult * 1.2:
            # We're hitting target AND price runs further — push TP out to capture more
            new_mult = min(MAX_TP_MULT, self.tp_mult * 1.08)
            reason   = f"Extraction {avg_extraction:.1%} ✅ + runs have room — extending TP"
        elif p60_run < self.tp_mult * 0.85:
            # Most runs don't reach our TP — dial it in
            new_mult = max(MIN_TP_MULT, p60_run * 0.95)
            reason   = f"60th pct run ({p60_run:.2f}x) < TP mult — calibrating"
        else:
            return {
                "adapted":         False,
                "reason":          "TP well-calibrated — no change needed",
                "tp_mult":         round(self.tp_mult, 3),
                "avg_extraction":  round(avg_extraction, 4),
                "trades_learned":  n
            }

        self.tp_mult = round(new_mult, 3)
        self.version += 1

        logger.info(f"AdaptiveTP v{self.version}: TP mult {old_tp_mult:.3f} → {self.tp_mult:.3f} | {reason}")

        return {
            "adapted":         True,
            "version":         self.version,
            "old_tp_mult":     round(old_tp_mult, 3),
            "new_tp_mult":     round(self.tp_mult, 3),
            "avg_extraction":  round(avg_extraction, 4),
            "avg_run_ratio":   round(avg_run_ratio, 4),
            "p60_run":         round(p60_run, 4),
            "reason":          reason,
            "trades_learned":  n
        }

    # ── Status & persistence ──────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "tp_mult":          self.tp_mult,
            "sl_mult":          self.sl_mult,
            "run_ratios":       self.run_ratios,
            "extraction_rates": self.extraction_rates,
            "version":          self.version
        }

    def status(self) -> dict:
        avg_extraction = statistics.mean(self.extraction_rates) if self.extraction_rates else 0
        avg_run        = statistics.mean(self.run_ratios)       if self.run_ratios       else 0
        mature         = avg_extraction >= TARGET_EXTRACTION and len(self.extraction_rates) >= MIN_TRADES_TO_LEARN
        return {
            "tp_mult":           round(self.tp_mult, 3),
            "sl_mult":           round(self.sl_mult, 3),
            "avg_extraction_pct": round(avg_extraction * 100, 2),
            "target_pct":        TARGET_EXTRACTION * 100,
            "avg_run_atr_mult":  round(avg_run, 3),
            "learning_version":  self.version,
            "wins_learned_from": len(self.extraction_rates),
            "is_mature":         mature,
            "status_emoji":      "🧠✅" if mature else "🧠🔄"
        }

    def format_for_telegram(self) -> str:
        s = self.status()
        bar_filled = int(s['avg_extraction_pct'] / 5)  # out of 20 blocks
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        return (
            f"🧠 *Adaptive TP Engine — v{s['learning_version']}*\n\n"
            f"TP Multiplier: `{s['tp_mult']}x ATR`\n"
            f"SL Multiplier: `{s['sl_mult']}x ATR`\n\n"
            f"Avg Extraction: `{s['avg_extraction_pct']:.1f}%` (target: 30%)\n"
            f"`[{bar}]`\n\n"
            f"Wins Learned From: `{s['wins_learned_from']}`\n"
            f"Avg Price Run: `{s['avg_run_atr_mult']}x ATR`\n"
            f"Status: {s['status_emoji']} {'*Mature — hitting target!*' if s['is_mature'] else '*Still learning...*'}"
        )
