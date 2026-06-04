import logging
from typing import Optional

logger = logging.getLogger("FearlessFutures.Liquidity")

class LiquidityAnalyzer:
    """
    Deep liquidity analysis:
    - Bid/ask wall detection (large limit orders = liquidity magnets)
    - Spread quality check
    - Slippage estimation
    - Liquidity score (0–1)
    - Support/resistance levels from order book depth
    """

    def __init__(self, wall_threshold_pct: float = 0.03):
        """
        wall_threshold_pct: a price level is considered a 'wall' if its
        volume is >= wall_threshold_pct * total book volume (default 3%)
        """
        self.wall_threshold_pct = wall_threshold_pct

    def analyze(self, order_book_raw: dict, price: float, trade_size_usd: float = 500.0) -> dict:
        """
        order_book_raw: raw ccxt order book — {bids: [[price, qty], ...], asks: [[price, qty], ...]}
        price: current market price
        trade_size_usd: estimated notional size of trade (for slippage calc)
        """
        bids = order_book_raw.get('bids', [])
        asks = order_book_raw.get('asks', [])

        if not bids or not asks:
            return self._empty()

        # ── Core metrics ─────────────────────────────────────────────────────────
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        spread = best_ask - best_bid
        spread_pct = (spread / best_bid) * 100

        bid_vol_total = sum(b[1] for b in bids)
        ask_vol_total = sum(a[1] for a in asks)
        total_vol = bid_vol_total + ask_vol_total
        imbalance = (bid_vol_total - ask_vol_total) / total_vol if total_vol > 0 else 0

        # ── Wall detection ────────────────────────────────────────────────────────
        bid_walls = self._find_walls(bids, total_vol, price, side='bid')
        ask_walls = self._find_walls(asks, total_vol, price, side='ask')

        nearest_bid_wall = bid_walls[0] if bid_walls else None
        nearest_ask_wall = ask_walls[0] if ask_walls else None

        # ── Slippage estimate ────────────────────────────────────────────────────
        slippage_buy  = self._estimate_slippage(asks, trade_size_usd, price, side='buy')
        slippage_sell = self._estimate_slippage(bids, trade_size_usd, price, side='sell')

        # ── Liquidity score (0–1, higher = better for trading) ───────────────────
        score = self._liquidity_score(spread_pct, imbalance, slippage_buy, bid_vol_total, ask_vol_total)

        # ── Trap detection (thin book + big walls = potential stop hunt) ─────────
        trap_warning = self._detect_trap(spread_pct, bid_walls, ask_walls, imbalance)

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": round(spread, 6),
            "spread_pct": round(spread_pct, 4),
            "bid_volume": round(bid_vol_total, 4),
            "ask_volume": round(ask_vol_total, 4),
            "imbalance": round(imbalance, 4),
            "imbalance_label": self._imbalance_label(imbalance),
            "bid_walls": bid_walls[:3],   # top 3 closest
            "ask_walls": ask_walls[:3],
            "nearest_support": nearest_bid_wall['price'] if nearest_bid_wall else None,
            "nearest_resistance": nearest_ask_wall['price'] if nearest_ask_wall else None,
            "slippage_buy_pct": round(slippage_buy, 4),
            "slippage_sell_pct": round(slippage_sell, 4),
            "liquidity_score": round(score, 3),
            "liquidity_grade": self._grade(score),
            "trap_warning": trap_warning,
            "tradeable": score >= 0.4 and spread_pct < 0.15
        }

    # ── Helpers ──────────────────────────────────────────────────────────────────

    def _find_walls(self, levels: list, total_vol: float, price: float, side: str) -> list:
        threshold = total_vol * self.wall_threshold_pct
        walls = []
        for lvl in levels:
            p, v = lvl[0], lvl[1]
            if v >= threshold:
                dist_pct = abs(p - price) / price * 100
                walls.append({
                    "price": round(p, 6),
                    "volume": round(v, 4),
                    "dist_pct": round(dist_pct, 3),
                    "side": side
                })
        # Sort by closest to price
        walls.sort(key=lambda x: x['dist_pct'])
        return walls

    def _estimate_slippage(self, levels: list, trade_usd: float, price: float, side: str) -> float:
        """Walk the book to estimate average fill price vs best price."""
        remaining_usd = trade_usd
        cost = 0.0
        qty_filled = 0.0
        for p, v in levels:
            available_usd = p * v
            used_usd = min(remaining_usd, available_usd)
            qty = used_usd / p
            cost += qty * p
            qty_filled += qty
            remaining_usd -= used_usd
            if remaining_usd <= 0:
                break
        if qty_filled == 0:
            return 0.0
        avg_fill = cost / qty_filled
        best = levels[0][0] if levels else price
        return abs(avg_fill - best) / best * 100

    def _liquidity_score(self, spread_pct, imbalance, slippage, bid_vol, ask_vol) -> float:
        # Lower spread = better
        spread_score = max(0, 1 - spread_pct / 0.2)
        # Balanced book = better
        balance_score = 1 - abs(imbalance)
        # Low slippage = better
        slip_score = max(0, 1 - slippage / 0.1)
        # Volume depth
        total_vol = bid_vol + ask_vol
        vol_score = min(1.0, total_vol / 1000)
        return (spread_score * 0.35 + balance_score * 0.25 + slip_score * 0.25 + vol_score * 0.15)

    def _imbalance_label(self, imbalance: float) -> str:
        if imbalance > 0.3:   return "Strong BUY pressure 🟢"
        if imbalance > 0.1:   return "Mild BUY pressure 🟡"
        if imbalance < -0.3:  return "Strong SELL pressure 🔴"
        if imbalance < -0.1:  return "Mild SELL pressure 🟠"
        return "Balanced ⚪"

    def _grade(self, score: float) -> str:
        if score >= 0.8: return "A 🟢"
        if score >= 0.6: return "B 🟡"
        if score >= 0.4: return "C 🟠"
        return "D 🔴"

    def _detect_trap(self, spread_pct, bid_walls, ask_walls, imbalance) -> Optional[str]:
        """Detect potential stop-hunt or liquidity trap setups."""
        if spread_pct > 0.1 and not bid_walls and not ask_walls:
            return "⚠️ Thin book — high slippage risk, no clear walls"
        if imbalance > 0.5 and ask_walls:
            return "⚠️ Heavy bid side + ask wall above — possible bull trap"
        if imbalance < -0.5 and bid_walls:
            return "⚠️ Heavy ask side + bid wall below — possible bear trap"
        return None

    def _empty(self) -> dict:
        return {
            "spread_pct": None, "imbalance": 0, "imbalance_label": "Unknown",
            "bid_walls": [], "ask_walls": [], "nearest_support": None,
            "nearest_resistance": None, "slippage_buy_pct": None,
            "slippage_sell_pct": None, "liquidity_score": 0,
            "liquidity_grade": "N/A", "trap_warning": None, "tradeable": False
        }

    def format_for_telegram(self, analysis: dict, symbol: str) -> str:
        if not analysis.get('tradeable') and analysis.get('liquidity_score', 0) == 0:
            return f"💧 *{symbol} Liquidity* — No data"

        walls_str = ""
        for w in analysis.get('ask_walls', [])[:2]:
            walls_str += f"  🔴 Ask wall @ `${w['price']:,.4f}` ({w['dist_pct']:.2f}% away, vol {w['volume']:.1f})\n"
        for w in analysis.get('bid_walls', [])[:2]:
            walls_str += f"  🟢 Bid wall @ `${w['price']:,.4f}` ({w['dist_pct']:.2f}% away, vol {w['volume']:.1f})\n"

        trap = analysis.get('trap_warning', '')
        trap_str = f"\n{trap}" if trap else ""

        return (
            f"💧 *{symbol} Liquidity Analysis*\n\n"
            f"Grade: *{analysis['liquidity_grade']}* (score: {analysis['liquidity_score']:.2f})\n"
            f"Spread: `{analysis['spread_pct']:.4f}%`\n"
            f"Order Book: {analysis['imbalance_label']}\n"
            f"Slippage est. (buy/sell): `{analysis['slippage_buy_pct']:.4f}% / {analysis['slippage_sell_pct']:.4f}%`\n\n"
            f"*Walls Detected:*\n{walls_str or '  None detected'}"
            f"{trap_str}"
        )
