"""Gap-and-go baseline (rule strategy #1, README §Signals & ML).

Daily-bar version: if a stock opens gapped up vs yesterday's close (within a
band — huge gaps are news events, not momentum), buy at the open with a limit
padded slightly above (an at-the-open limit on a rising tape never fills in a
pessimistic simulator), and let the engine's 15:15 square-off exit.

Sizing respects the risk limits by construction (qty from the 1% risk budget
AND the 30% concentration cap) — the Risk Manager still checks independently;
this strategy just doesn't waste signals it knows would be vetoed.

This is an engineering baseline to calibrate the backtester, not trading
advice. Intraday stops need intraday data (Phase 2 quote recorder); until
then the stop bounds position size but is only evaluated at the close.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time

from atis.mktcalendar import IST
from atis.models import Kind, Quote, Side, Signal
from atis.strategy.base import Strategy


class GapAndGoDaily(Strategy):
    name = "gap-and-go-daily"
    model_version = "rule-baseline-1"

    def __init__(
        self,
        capital: float,
        min_gap: float = 0.01,
        max_gap: float = 0.05,
        stop_pct: float = 0.01,
        entry_pad: float = 0.002,
        risk_pct: float = 0.01,
        concentration_pct: float = 0.30,
    ):
        self.capital = capital
        self.min_gap = min_gap
        self.max_gap = max_gap
        self.stop_pct = stop_pct
        self.entry_pad = entry_pad
        self.risk_pct = risk_pct
        self.concentration_pct = concentration_pct
        self._prev_close: dict[str, float] = {}
        self._traded_today: set[tuple[str, str]] = set()

    def on_quote(self, quote: Quote, now: datetime) -> list[Signal]:
        ist = now.astimezone(IST)
        day = ist.date().isoformat()

        # Afternoon quotes update the reference close; no trading decisions
        if ist.time() >= time(14, 0):
            self._prev_close[quote.symbol] = quote.last
            return []
        if ist.time() >= time(10, 0):
            return []

        prev = self._prev_close.get(quote.symbol)
        if prev is None or prev <= 0 or (quote.symbol, day) in self._traded_today:
            return []
        self._traded_today.add((quote.symbol, day))

        gap = quote.last / prev - 1.0
        if not (self.min_gap <= gap <= self.max_gap):
            return []

        entry = quote.last * (1 + self.entry_pad)
        stop = entry * (1 - self.stop_pct)
        per_unit_risk = entry - stop
        qty = min(
            int(self.capital * self.risk_pct / per_unit_risk),
            int(self.capital * self.concentration_pct / entry),
        )
        if qty < 1:
            return []
        return [Signal(
            signal_id=f"gng-{quote.symbol}-{day}-{uuid.uuid4().hex[:6]}",
            ts=now,
            symbol=quote.symbol,
            kind=Kind.EQUITY,
            action=Side.BUY,
            confidence=0.5,
            entry_price=entry,
            stop_loss=stop,
            target_price=None,
            qty=qty,
            catalysts=[f"gap up {gap:.2%} vs prev close {prev:.2f}"],
            model_version=self.model_version,
        )]
