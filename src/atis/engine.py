"""Trading engine: wires quotes → strategy → risk → broker.

One code path for backtest, paper, and live — only the injected DataProvider
and Broker differ.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from atis.audit import Audit, Category
from atis.broker.base import Broker
from atis.config import RiskLimits
from atis.mktcalendar import IST, NSECalendar
from atis.models import OrderRequest, OrderType, Quote, Signal, Side
from atis.risk import RiskManager
from atis.strategy.base import Strategy


def order_from_signal(sig: Signal, tick_size: float = 0.05) -> OrderRequest:
    """Signals become LIMIT orders at the entry price (never MARKET on entry)."""
    limit = round(round(sig.entry_price / tick_size) * tick_size, 2)
    return OrderRequest(
        client_order_id=f"ord-{sig.signal_id}",   # deterministic: 1 signal -> 1 order id
        signal_id=sig.signal_id,
        symbol=sig.symbol,
        kind=sig.kind,
        side=sig.action,
        qty=sig.qty,
        order_type=OrderType.LIMIT,
        limit_price=limit,
    )


class TradingEngine:
    def __init__(
        self,
        conn: sqlite3.Connection,
        strategy: Strategy,
        risk: RiskManager,
        broker: Broker,
        audit: Audit,
        calendar: NSECalendar,
        limits: RiskLimits,
    ):
        self._conn = conn
        self._strategy = strategy
        self._risk = risk
        self._broker = broker
        self._audit = audit
        self._calendar = calendar
        self._limits = limits
        self._squared_off: set[str] = set()  # days already squared off

    def _persist_signal(self, sig: Signal) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO signals (signal_id, ts, symbol, kind, action, "
            "confidence, entry_price, stop_loss, target_price, qty, model_version, "
            "features_hash, catalysts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sig.signal_id, sig.ts.isoformat(), sig.symbol, sig.kind.value,
             sig.action.value, sig.confidence, sig.entry_price, sig.stop_loss,
             sig.target_price, sig.qty, sig.model_version, sig.features_hash,
             json.dumps(sig.catalysts)),
        )
        self._conn.commit()

    def on_quote(self, quote: Quote, now: datetime) -> None:
        self._broker.on_quote(quote, now)

        # Session-driven square-off comes before anything else
        day = now.astimezone(IST).date().isoformat()
        if (
            day not in self._squared_off
            and self._calendar.square_off_due(now, self._limits.square_off_at)
            and self._broker.get_positions()
        ):
            self._audit.log(Category.SYSTEM, "eod_square_off", day=day)
            self._broker.square_off_all(now)
            self._squared_off.add(day)
            return

        for sig in self._strategy.on_quote(quote, now):
            self._persist_signal(sig)
            self._audit.log(
                Category.SIGNAL, "emitted",
                signal_id=sig.signal_id, symbol=sig.symbol, action=sig.action.value,
                qty=sig.qty, entry=sig.entry_price, stop=sig.stop_loss,
                confidence=sig.confidence, model_version=sig.model_version,
            )
            req = order_from_signal(sig)
            decision = self._risk.evaluate(
                req, quote, self._broker.get_positions(), now, stop_loss=sig.stop_loss
            )
            if not decision.approved:
                continue  # veto already audited by RiskManager
            result = self._broker.place_order(req, now)
            self._audit.log(
                Category.ORDER, "placed",
                client_order_id=result.client_order_id, status=result.status.value,
                reason=result.reason,
            )
