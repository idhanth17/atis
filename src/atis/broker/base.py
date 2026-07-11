"""The Broker seam. PaperBroker (now) and KiteBroker (Phase 4) implement this;
strategy and risk code never know which one is behind it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from atis.models import OrderRequest, OrderStatus, Position, Quote


@dataclass
class OrderResult:
    client_order_id: str
    status: OrderStatus
    reason: str = ""          # populated on rejection, Kite-style error message
    fills: list = field(default_factory=list)


class Broker(ABC):
    @abstractmethod
    def place_order(self, req: OrderRequest, now: datetime) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, client_order_id: str, now: datetime) -> OrderResult: ...

    @abstractmethod
    def on_quote(self, quote: Quote, now: datetime) -> None:
        """Feed a market quote; the broker updates marks and works resting orders."""

    @abstractmethod
    def get_positions(self) -> dict[str, Position]: ...

    @abstractmethod
    def get_margins(self) -> dict[str, float]:
        """Returns {'cash': .., 'blocked_margin': .., 'equity': ..}."""

    @abstractmethod
    def square_off_all(self, now: datetime, penalty: bool = False) -> list[OrderResult]: ...
