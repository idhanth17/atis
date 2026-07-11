"""Strategy seam. Strategies see quotes and emit signals — nothing else.

They cannot place orders, cannot see which broker is behind the seam, and
cannot tell backtest from paper from live. Signals propose; risk disposes.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime

from atis.models import Kind, Quote, Side, Signal


class Strategy(ABC):
    name: str = "strategy"
    model_version: str = "baseline-0"

    @abstractmethod
    def on_quote(self, quote: Quote, now: datetime) -> list[Signal]: ...


class OversizedDummyStrategy(Strategy):
    """Phase 0 gate fixture: emits one deliberately oversized signal per symbol.

    Its only job is to be VETOED by the Risk Manager and leave an audit trail.
    If an order from this strategy ever reaches a broker, the safety rails are
    broken and CI must fail.
    """

    name = "oversized-dummy"
    model_version = "gate-check-0"

    def __init__(self) -> None:
        self._emitted: set[str] = set()

    def on_quote(self, quote: Quote, now: datetime) -> list[Signal]:
        if quote.symbol in self._emitted:
            return []
        self._emitted.add(quote.symbol)
        return [
            Signal(
                signal_id=f"dummy-{uuid.uuid4().hex[:12]}",
                ts=now,
                symbol=quote.symbol,
                kind=quote.kind,
                action=Side.BUY,
                confidence=0.99,
                entry_price=quote.last,
                stop_loss=quote.last * 0.98,          # 2% stop...
                target_price=quote.last * 1.04,
                qty=max(int(1_000_000 / quote.last), 1),  # ...on a ₹10 lakh position
                catalysts=["phase-0 gate check: this MUST be vetoed"],
                model_version=self.model_version,
            )
        ]
