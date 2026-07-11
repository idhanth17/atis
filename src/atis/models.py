"""Core domain models shared by every layer.

Strategies, risk, and brokers speak only in these types — that is what keeps
backtest / paper / live interchangeable (README §Architecture, "two hard seams").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    SL = "SL"      # stop-loss limit
    SL_M = "SL-M"  # stop-loss market


class Product(str, Enum):
    # MIS only in the automated path — the broker's forced intraday square-off
    # is the ultimate dead-man backstop (SECURITY.md §3.5). Never NRML/CNC here.
    MIS = "MIS"


class Kind(str, Enum):
    EQUITY = "EQUITY"
    OPTION = "OPTION"


class OrderStatus(str, Enum):
    NEW = "NEW"
    SENT = "SENT"
    ACK = "ACK"
    OPEN = "OPEN"          # resting in the (simulated) book
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


# Strict order state machine (SECURITY.md §7). Unknown transitions halt trading.
ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.NEW: {OrderStatus.SENT, OrderStatus.REJECTED},
    OrderStatus.SENT: {OrderStatus.ACK, OrderStatus.REJECTED},
    OrderStatus.ACK: {OrderStatus.OPEN, OrderStatus.PARTIAL, OrderStatus.FILLED,
                      OrderStatus.REJECTED, OrderStatus.CANCELLED},
    OrderStatus.OPEN: {OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED},
    OrderStatus.PARTIAL: {OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED},
    OrderStatus.FILLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.CANCELLED: set(),
}


class IllegalTransition(RuntimeError):
    """An order tried a transition outside ORDER_TRANSITIONS — halt and investigate."""


def check_transition(current: OrderStatus, new: OrderStatus) -> None:
    if new not in ORDER_TRANSITIONS[current]:
        raise IllegalTransition(f"order state {current} -> {new} is not allowed")


@dataclass
class Quote:
    symbol: str
    last: float
    asof: datetime          # exchange time of the quote
    received_at: datetime   # when we received it locally
    bid: float | None = None
    ask: float | None = None
    kind: Kind = Kind.EQUITY

    def age_seconds(self, now: datetime) -> float:
        """Staleness of the market data at decision time."""
        return (now - self.asof).total_seconds()


@dataclass
class Signal:
    signal_id: str
    ts: datetime
    symbol: str
    kind: Kind
    action: Side
    confidence: float
    entry_price: float
    stop_loss: float
    target_price: float | None = None
    qty: int = 0
    time_horizon: str = "intraday"
    catalysts: list[str] = field(default_factory=list)
    model_version: str = "baseline-0"
    features_hash: str = ""


@dataclass
class OrderRequest:
    client_order_id: str    # idempotency key: one signal -> at most one order, ever
    signal_id: str
    symbol: str
    kind: Kind
    side: Side
    qty: int
    order_type: OrderType
    limit_price: float | None = None
    trigger_price: float | None = None
    product: Product = Product.MIS


@dataclass
class Fill:
    client_order_id: str
    symbol: str
    side: Side
    qty: int
    price: float
    ts: datetime
    costs_total: float


@dataclass
class Position:
    symbol: str
    kind: Kind
    qty: int            # signed: positive long, negative short
    avg_price: float
    margin_blocked: float = 0.0


@dataclass
class RiskDecision:
    approved: bool
    reasons: list[str] = field(default_factory=list)
