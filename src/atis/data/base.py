"""The DataProvider seam — the second hard interface (with Broker).

Every provider must record fetched_at + source, archive raw payloads before
parsing, and mark delayed data as delayed (README §Free data sources).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from atis.models import Quote


class DataProvider(ABC):
    name: str = "provider"

    @abstractmethod
    def daily_ohlcv(self, symbol: str, start: date, end: date) -> list[dict]:
        """Rows: {trade_date, open, high, low, close, volume}."""

    def live_quote(self, symbol: str) -> Quote | None:
        """Latest quote if this provider supports it; None otherwise.
        Implementations MUST set asof honestly (delayed feeds keep the
        delayed timestamp — the simulator depends on it)."""
        return None
