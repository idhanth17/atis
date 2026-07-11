"""Indian transaction cost stack — charged on every simulated trade, on by
default, cannot be disabled in reported results (SECURITY.md §8).

Worked examples from docs/PAPER_TRADING_ENGINE.md §5 are locked in by
tests/test_costs.py and must reproduce to the paisa.
"""

from __future__ import annotations

from dataclasses import dataclass

from atis.config import CostConfig, SegmentCosts
from atis.models import Kind, Side

CRORE = 1e7


@dataclass
class CostBreakdown:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi: float
    stamp: float
    gst: float

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange_txn + self.sebi + self.stamp + self.gst

    def as_dict(self) -> dict:
        return {
            "brokerage": round(self.brokerage, 4),
            "stt": round(self.stt, 4),
            "exchange_txn": round(self.exchange_txn, 4),
            "sebi": round(self.sebi, 4),
            "stamp": round(self.stamp, 4),
            "gst": round(self.gst, 4),
            "total": round(self.total, 4),
        }


class CostEngine:
    def __init__(self, cfg: CostConfig):
        self._cfg = cfg

    def _compute(self, seg: SegmentCosts, side: Side, value: float) -> CostBreakdown:
        if seg.brokerage_flat is not None:
            brokerage = seg.brokerage_flat
        else:
            brokerage = min(seg.brokerage_pct * value, seg.brokerage_cap)
        stt = seg.stt_sell_pct * value if side is Side.SELL else 0.0
        exchange_txn = seg.exchange_txn_pct * value
        sebi = value / CRORE * seg.sebi_per_crore
        stamp = seg.stamp_buy_pct * value if side is Side.BUY else 0.0
        gst = seg.gst_pct * (brokerage + exchange_txn + sebi)
        return CostBreakdown(brokerage, stt, exchange_txn, sebi, stamp, gst)

    def for_fill(self, kind: Kind, side: Side, value: float) -> CostBreakdown:
        """Costs for one executed order of the given traded value (₹)."""
        seg = self._cfg.equity_intraday if kind is Kind.EQUITY else self._cfg.index_option
        return self._compute(seg, side, value)

    def round_trip(self, kind: Kind, buy_value: float, sell_value: float | None = None) -> float:
        sell_value = buy_value if sell_value is None else sell_value
        return (
            self.for_fill(kind, Side.BUY, buy_value).total
            + self.for_fill(kind, Side.SELL, sell_value).total
        )
