"""PaperBroker — pessimistic by design (docs/PAPER_TRADING_ENGINE.md).

Every design choice errs against the strategy:
- limit orders at the touch do NOT fill; price must trade through
- stops fill at the worse of trigger and the next observed quote (gap-through)
- stale quotes refuse fills, like a defensive live system would
- the full Indian cost stack is charged on every fill, no off switch
- a ledger invariant runs after every fill; violation = bug = halt

State (cash, positions, ledger) lives in SQLite so a restart resumes rather
than resets.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from atis.audit import Audit, Category
from atis.broker.base import Broker, OrderResult
from atis.config import PaperConfig
from atis.costs import CostEngine
from atis.models import (
    Fill,
    Kind,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Quote,
    Side,
    check_transition,
)


def r2(x: float) -> float:
    """Round to the paisa, deterministically (half-up, not banker's)."""
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class LedgerInvariantError(RuntimeError):
    """cash-from-ledger != running cash. A bug — halt everything."""


@dataclass
class _WorkingOrder:
    req: OrderRequest
    status: OrderStatus
    triggered: bool = False


class PaperBroker(Broker):
    def __init__(
        self,
        conn: sqlite3.Connection,
        audit: Audit,
        cost_engine: CostEngine,
        cfg: PaperConfig,
        starting_cash: float,
    ):
        self._conn = conn
        self._audit = audit
        self._costs = cost_engine
        self._cfg = cfg
        self._working: dict[str, _WorkingOrder] = {}
        self._quotes: dict[str, Quote] = {}

        row = conn.execute("SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM ledger").fetchone()
        if row[1] == 0:
            self.cash = r2(starting_cash)
            self._ledger("SEED", self.cash, "opening capital")
        else:
            self.cash = r2(row[0])
        self._positions: dict[str, Position] = {}
        for p in conn.execute("SELECT * FROM positions WHERE qty != 0").fetchall():
            self._positions[p["symbol"]] = Position(
                symbol=p["symbol"], kind=Kind(p["kind"]), qty=p["qty"],
                avg_price=p["avg_price"], margin_blocked=p["margin_blocked"],
            )

    # ------------------------------------------------------------------
    # ledger / accounting
    # ------------------------------------------------------------------
    def _ledger(self, entry_type: str, amount: float, ref: str) -> None:
        amount = r2(amount)
        self._conn.execute(
            "INSERT INTO ledger (ts, entry_type, amount, ref) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), entry_type, amount, ref),
        )
        self._conn.commit()

    def _cash_delta(self, entry_type: str, amount: float, ref: str) -> None:
        amount = r2(amount)
        self.cash = r2(self.cash + amount)
        self._ledger(entry_type, amount, ref)

    def _check_invariant(self) -> None:
        total = self._conn.execute("SELECT COALESCE(SUM(amount), 0) FROM ledger").fetchone()[0]
        if abs(r2(total) - self.cash) > 0.005:
            raise LedgerInvariantError(
                f"ledger sum {total:.2f} != running cash {self.cash:.2f}"
            )

    def _save_position(self, pos: Position) -> None:
        self._conn.execute(
            "INSERT INTO positions (symbol, kind, qty, avg_price, margin_blocked) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET qty=excluded.qty, "
            "avg_price=excluded.avg_price, margin_blocked=excluded.margin_blocked",
            (pos.symbol, pos.kind.value, pos.qty, pos.avg_price, pos.margin_blocked),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # market model helpers
    # ------------------------------------------------------------------
    def _slippage(self, kind: Kind, price: float) -> float:
        if kind is Kind.EQUITY:
            return self._cfg.slippage_equity_pct * price
        return max(self._cfg.slippage_option_pct * price, self._cfg.tick_size)

    def _half_spread(self, quote: Quote) -> float:
        if quote.bid is not None and quote.ask is not None and quote.ask > quote.bid:
            return (quote.ask - quote.bid) / 2
        pct = (
            self._cfg.spread_est_equity_pct
            if quote.kind is Kind.EQUITY
            else self._cfg.spread_est_option_pct
        )
        return pct * quote.last / 2

    def _valid_tick(self, price: float) -> bool:
        paise = round(price * 100)
        tick_paise = round(self._cfg.tick_size * 100)
        return abs(price * 100 - paise) < 1e-6 and paise % tick_paise == 0

    def _quote_ok(self, symbol: str, now: datetime) -> Quote | None:
        q = self._quotes.get(symbol)
        if q is None or q.age_seconds(now) > self._cfg.stale_quote_seconds:
            return None
        return q

    # ------------------------------------------------------------------
    # order persistence
    # ------------------------------------------------------------------
    def _insert_order(self, req: OrderRequest, status: OrderStatus, now: datetime,
                      reason: str = "") -> None:
        self._conn.execute(
            "INSERT INTO orders (client_order_id, signal_id, symbol, kind, side, qty, "
            "order_type, limit_price, trigger_price, product, status, reject_reason, "
            "created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (req.client_order_id, req.signal_id, req.symbol, req.kind.value,
             req.side.value, req.qty, req.order_type.value, req.limit_price,
             req.trigger_price, req.product.value, status.value, reason or None,
             now.isoformat(), now.isoformat()),
        )
        self._conn.commit()

    def _set_status(self, order_id: str, current: OrderStatus, new: OrderStatus,
                    now: datetime, reason: str = "") -> None:
        check_transition(current, new)
        self._conn.execute(
            "UPDATE orders SET status = ?, reject_reason = ?, updated_ts = ? "
            "WHERE client_order_id = ?",
            (new.value, reason or None, now.isoformat(), order_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Broker interface
    # ------------------------------------------------------------------
    def place_order(self, req: OrderRequest, now: datetime) -> OrderResult:
        def reject(reason: str) -> OrderResult:
            self._insert_order(req, OrderStatus.REJECTED, now, reason)
            self._audit.log(Category.ORDER, "rejected",
                            client_order_id=req.client_order_id, reason=reason)
            return OrderResult(req.client_order_id, OrderStatus.REJECTED, reason)

        if req.qty <= 0:
            return reject("Quantity must be positive")
        for price in (req.limit_price, req.trigger_price):
            if price is not None and not self._valid_tick(price):
                return reject(f"Price {price} not a multiple of tick size "
                              f"{self._cfg.tick_size}")
        if req.order_type in (OrderType.LIMIT, OrderType.SL) and req.limit_price is None:
            return reject("Limit price required for LIMIT/SL orders")
        if req.order_type in (OrderType.SL, OrderType.SL_M) and req.trigger_price is None:
            return reject("Trigger price required for SL/SL-M orders")

        quote = self._quote_ok(req.symbol, now)
        if quote is None:
            return reject("Market data unavailable or stale for symbol")  # defensive live behaviour

        pos = self._positions.get(req.symbol)
        is_exit = pos is not None and pos.qty != 0 and (
            (pos.qty > 0 and req.side is Side.SELL) or (pos.qty < 0 and req.side is Side.BUY)
        )
        if is_exit and req.qty > abs(pos.qty):
            return reject("Order would flip position direction; close first, then enter")
        if req.kind is Kind.OPTION and req.side is Side.SELL and not is_exit:
            return reject("Option writing (short) is not supported")

        if not is_exit:
            ref_price = req.limit_price or quote.last
            value = req.qty * ref_price
            if req.kind is Kind.EQUITY:
                required = value / self._cfg.equity_mis_leverage
            else:
                required = value  # long options: full premium
            est_costs = self._costs.for_fill(req.kind, req.side, value).total
            if required + est_costs > self.cash:
                return reject(
                    f"Insufficient funds. Required margin ₹{required + est_costs:,.2f}, "
                    f"available ₹{self.cash:,.2f}"
                )

        self._insert_order(req, OrderStatus.NEW, now)
        self._set_status(req.client_order_id, OrderStatus.NEW, OrderStatus.SENT, now)
        self._set_status(req.client_order_id, OrderStatus.SENT, OrderStatus.ACK, now)

        if req.order_type in (OrderType.SL, OrderType.SL_M):
            # Stops always rest until triggered
            self._working[req.client_order_id] = _WorkingOrder(req, OrderStatus.OPEN)
            self._set_status(req.client_order_id, OrderStatus.ACK, OrderStatus.OPEN, now)
            return OrderResult(req.client_order_id, OrderStatus.OPEN)

        fill_price = self._try_immediate_fill(req, quote)
        if fill_price is not None:
            fill = self._execute_fill(req, fill_price, now, from_status=OrderStatus.ACK)
            return OrderResult(req.client_order_id, OrderStatus.FILLED, fills=[fill])

        # Rests in the simulated book
        self._working[req.client_order_id] = _WorkingOrder(req, OrderStatus.OPEN)
        self._set_status(req.client_order_id, OrderStatus.ACK, OrderStatus.OPEN, now)
        return OrderResult(req.client_order_id, OrderStatus.OPEN)

    def _try_immediate_fill(self, req: OrderRequest, quote: Quote) -> float | None:
        """Fill price for a marketable order, or None if it should rest."""
        slip = self._slippage(req.kind, quote.last)
        half_spread = self._half_spread(quote)

        if req.order_type is OrderType.MARKET:
            if req.side is Side.BUY:
                return quote.last + half_spread + 2 * slip
            return max(quote.last - half_spread - 2 * slip, self._cfg.tick_size)

        # LIMIT
        assert req.limit_price is not None
        if req.side is Side.BUY:
            if quote.ask is not None:
                if req.limit_price >= quote.ask:
                    return min(req.limit_price, quote.ask + slip)
                return None
            candidate = quote.last + half_spread + slip
            return candidate if candidate <= req.limit_price else None
        else:
            if quote.bid is not None:
                if req.limit_price <= quote.bid:
                    return max(req.limit_price, quote.bid - slip)
                return None
            candidate = quote.last - half_spread - slip
            return candidate if candidate >= req.limit_price else None

    def on_quote(self, quote: Quote, now: datetime) -> None:
        self._quotes[quote.symbol] = quote
        for order_id in list(self._working):
            wo = self._working.get(order_id)
            if wo is None or wo.req.symbol != quote.symbol:
                continue
            req = wo.req

            if req.order_type in (OrderType.SL, OrderType.SL_M):
                assert req.trigger_price is not None
                if not wo.triggered:
                    fired = (
                        quote.last >= req.trigger_price
                        if req.side is Side.BUY
                        else quote.last <= req.trigger_price
                    )
                    if not fired:
                        continue
                    wo.triggered = True
                slip = self._slippage(req.kind, quote.last)
                if req.order_type is OrderType.SL_M:
                    # Worse of trigger and the observed quote — gap-through is real
                    if req.side is Side.BUY:
                        price = max(req.trigger_price, quote.last) + slip
                    else:
                        price = max(min(req.trigger_price, quote.last) - slip,
                                    self._cfg.tick_size)
                    del self._working[order_id]
                    self._execute_fill(req, price, now, from_status=OrderStatus.OPEN)
                else:
                    # SL (limit leg): behaves as a resting limit after trigger
                    if self._limit_traded_through(req, quote):
                        del self._working[order_id]
                        self._execute_fill(req, req.limit_price, now,
                                           from_status=OrderStatus.OPEN)
                continue

            # Resting LIMIT: fills only when traded THROUGH, never on a touch
            if self._limit_traded_through(req, quote):
                del self._working[order_id]
                self._execute_fill(req, req.limit_price, now, from_status=OrderStatus.OPEN)

    @staticmethod
    def _limit_traded_through(req: OrderRequest, quote: Quote) -> bool:
        assert req.limit_price is not None
        if req.side is Side.BUY:
            return quote.last < req.limit_price
        return quote.last > req.limit_price

    def _execute_fill(self, req: OrderRequest, price: float, now: datetime,
                      from_status: OrderStatus) -> Fill:
        price = r2(price)
        value = req.qty * price
        costs = self._costs.for_fill(req.kind, req.side, value)
        pos = self._positions.get(req.symbol)
        is_exit = pos is not None and pos.qty != 0 and (
            (pos.qty > 0 and req.side is Side.SELL) or (pos.qty < 0 and req.side is Side.BUY)
        )
        ref = req.client_order_id

        if not is_exit:
            if req.kind is Kind.EQUITY:
                margin = value / self._cfg.equity_mis_leverage
                self._cash_delta("MARGIN_BLOCK", -margin, ref)
            else:
                margin = 0.0
                self._cash_delta("PREMIUM", -value, ref)
            if pos is None or pos.qty == 0:
                pos = Position(req.symbol, req.kind, 0, 0.0, 0.0)
            new_qty = req.qty if req.side is Side.BUY else -req.qty
            total_qty = pos.qty + new_qty
            pos.avg_price = (abs(pos.qty) * pos.avg_price + req.qty * price) / abs(total_qty)
            pos.qty = total_qty
            pos.margin_blocked = r2(pos.margin_blocked + margin)
            self._positions[req.symbol] = pos
        else:
            assert pos is not None
            frac = req.qty / abs(pos.qty)
            released = r2(pos.margin_blocked * frac)
            direction = 1 if pos.qty > 0 else -1
            realized = direction * (price - pos.avg_price) * req.qty
            if req.kind is Kind.EQUITY:
                self._cash_delta("MARGIN_RELEASE", released, ref)
                self._cash_delta("REALIZED_PNL", realized, ref)
            else:
                self._cash_delta("PROCEEDS", value, ref)
            pos.qty -= direction * req.qty
            pos.margin_blocked = r2(pos.margin_blocked - released)
            if pos.qty == 0:
                pos.avg_price = 0.0
                pos.margin_blocked = 0.0
            self._positions[req.symbol] = pos

        self._cash_delta("COSTS", -costs.total, ref)
        self._save_position(self._positions[req.symbol])
        self._set_status(req.client_order_id, from_status, OrderStatus.FILLED, now)
        self._conn.execute(
            "INSERT INTO fills (client_order_id, symbol, side, qty, price, ts, costs) "
            "VALUES (?,?,?,?,?,?,?)",
            (req.client_order_id, req.symbol, req.side.value, req.qty, price,
             now.isoformat(), json.dumps(costs.as_dict())),
        )
        self._conn.commit()
        self._audit.log(Category.FILL, "filled",
                        client_order_id=req.client_order_id, symbol=req.symbol,
                        side=req.side.value, qty=req.qty, price=price,
                        costs=costs.as_dict())
        self._check_invariant()
        return Fill(req.client_order_id, req.symbol, req.side, req.qty, price, now,
                    r2(costs.total))

    def cancel_order(self, client_order_id: str, now: datetime) -> OrderResult:
        wo = self._working.pop(client_order_id, None)
        if wo is None:
            return OrderResult(client_order_id, OrderStatus.REJECTED,
                               "Order not open (already filled, cancelled, or unknown)")
        self._set_status(client_order_id, wo.status, OrderStatus.CANCELLED, now)
        self._audit.log(Category.ORDER, "cancelled", client_order_id=client_order_id)
        return OrderResult(client_order_id, OrderStatus.CANCELLED)

    def get_positions(self) -> dict[str, Position]:
        return {s: p for s, p in self._positions.items() if p.qty != 0}

    def get_margins(self) -> dict[str, float]:
        blocked = sum(p.margin_blocked for p in self._positions.values())
        unrealized = 0.0
        option_value = 0.0
        for p in self._positions.values():
            if p.qty == 0:
                continue
            q = self._quotes.get(p.symbol)
            mark = q.last if q else p.avg_price
            if p.kind is Kind.EQUITY:
                unrealized += (mark - p.avg_price) * p.qty
            else:
                option_value += mark * p.qty
        return {
            "cash": self.cash,
            "blocked_margin": r2(blocked),
            "equity": r2(self.cash + blocked + unrealized + option_value),
        }

    def square_off_all(self, now: datetime, penalty: bool = False) -> list[OrderResult]:
        """Close everything at market. Never blocked by risk/rate limits —
        an exit must always be possible. Penalty mode = broker RMS square-off."""
        results: list[OrderResult] = []
        for order_id in list(self._working):
            results.append(self.cancel_order(order_id, now))
        for symbol, pos in list(self.get_positions().items()):
            q = self._quotes.get(symbol)  # deliberately allow stale here: last good quote
            if q is None:
                self._audit.log(Category.ERROR, "squareoff_no_quote", symbol=symbol)
                continue
            slip = self._slippage(pos.kind, q.last)
            if penalty:
                slip *= self._cfg.squareoff_penalty_multiplier
            side = Side.SELL if pos.qty > 0 else Side.BUY
            price = q.last - slip if side is Side.SELL else q.last + slip
            price = max(round(price / self._cfg.tick_size) * self._cfg.tick_size,
                        self._cfg.tick_size)
            req = OrderRequest(
                client_order_id=f"sqoff-{symbol}-{now.strftime('%Y%m%d-%H%M%S')}",
                signal_id=f"sqoff-{symbol}-{now.strftime('%Y%m%d-%H%M%S')}",
                symbol=symbol, kind=pos.kind, side=side, qty=abs(pos.qty),
                order_type=OrderType.MARKET,
            )
            self._insert_order(req, OrderStatus.NEW, now)
            self._set_status(req.client_order_id, OrderStatus.NEW, OrderStatus.SENT, now)
            self._set_status(req.client_order_id, OrderStatus.SENT, OrderStatus.ACK, now)
            fill = self._execute_fill(req, price, now, from_status=OrderStatus.ACK)
            results.append(OrderResult(req.client_order_id, OrderStatus.FILLED,
                                       fills=[fill]))
        return results
