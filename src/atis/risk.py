"""Risk Manager — signals propose, risk disposes.

No order reaches a broker (paper or real) without passing every check here.
Every veto is written to the audit log with all failed reasons
(SECURITY.md §3.1). The rate-limit token is only consumed after every other
check has passed, so vetoed orders don't burn the day's order budget.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from atis.audit import Audit, Category
from atis.breakers import CircuitBreakers
from atis.config import RiskLimits
from atis.killswitch import KillSwitch
from atis.mktcalendar import IST, NSECalendar
from atis.models import (
    Kind,
    OrderRequest,
    OrderType,
    Position,
    Quote,
    RiskDecision,
    Side,
)
from atis.ratelimit import OrderRateLimiter


class RiskManager:
    def __init__(
        self,
        conn: sqlite3.Connection,
        limits: RiskLimits,
        calendar: NSECalendar,
        killswitch: KillSwitch,
        breakers: CircuitBreakers,
        rate_limiter: OrderRateLimiter,
        audit: Audit,
    ):
        self._conn = conn
        self.limits = limits
        self._calendar = calendar
        self._kill = killswitch
        self._breakers = breakers
        self._rate = rate_limiter
        self._audit = audit

    def evaluate(
        self,
        req: OrderRequest,
        quote: Quote | None,
        positions: dict[str, Position],
        now: datetime,
        *,
        stop_loss: float | None = None,
        is_exit: bool = False,
    ) -> RiskDecision:
        """Gate one order request. `is_exit=True` relaxes entry-only checks —
        a breaker or the 15:00 cutoff must never block closing a position."""
        limits = self.limits
        reasons: list[str] = []
        ist_now = now.astimezone(IST)

        # 1. Kill switch — absolute, blocks entries AND algorithmic exits
        #    (when a human engages KILL, square-off runs via the kill procedure,
        #    not the normal signal path).
        if self._kill.engaged():
            reasons.append(f"kill switch engaged: {self._kill.reason()}")

        # 2. Circuit breaker — blocks new entries only
        if not is_exit:
            trip = self._breakers.is_tripped(ist_now.date())
            if trip:
                reasons.append(f"circuit breaker tripped: {trip}")

        # 3. Calendar / session
        if not self._calendar.is_market_open(now):
            reasons.append("market closed (calendar/session check)")
        elif not is_exit and not self._calendar.allows_new_entries(
            now, limits.no_new_entries_after
        ):
            reasons.append(
                f"no new entries after {limits.no_new_entries_after.strftime('%H:%M')} IST"
            )

        # 4. Quote availability and staleness
        if quote is None:
            reasons.append("no quote available for symbol")
        elif quote.age_seconds(now) > limits.max_quote_age_seconds:
            reasons.append(
                f"quote stale: {quote.age_seconds(now):.0f}s > {limits.max_quote_age_seconds:.0f}s"
            )

        # 5. Price sanity
        if quote is not None and req.limit_price is not None and quote.last > 0:
            deviation = abs(req.limit_price - quote.last) / quote.last
            if deviation > limits.max_price_deviation_pct:
                reasons.append(
                    f"limit price deviates {deviation:.2%} from last quote "
                    f"(max {limits.max_price_deviation_pct:.2%})"
                )

        # 6. Duplicate-order guard: one signal -> at most one order, ever
        dup = self._conn.execute(
            "SELECT 1 FROM orders WHERE client_order_id = ? OR signal_id = ?",
            (req.client_order_id, req.signal_id),
        ).fetchone()
        if dup:
            reasons.append(f"duplicate order for signal {req.signal_id}")

        # 7-9. Position-size checks (entries only)
        if not is_exit:
            open_positions = {s: p for s, p in positions.items() if p.qty != 0}
            if req.symbol not in open_positions and len(open_positions) >= limits.max_open_positions:
                reasons.append(f"max open positions ({limits.max_open_positions}) reached")

            ref_price = req.limit_price or (quote.last if quote else 0.0)
            exposure = req.qty * ref_price
            existing = open_positions.get(req.symbol)
            if existing:
                exposure += abs(existing.qty) * existing.avg_price
            max_exposure = limits.max_instrument_concentration_pct * limits.capital
            if exposure > max_exposure:
                reasons.append(
                    f"instrument concentration ₹{exposure:,.0f} > "
                    f"{limits.max_instrument_concentration_pct:.0%} of capital (₹{max_exposure:,.0f})"
                )

            if stop_loss is None:
                reasons.append("entry order without a stop loss is not allowed")
            elif ref_price > 0:
                per_unit_risk = (
                    ref_price - stop_loss if req.side is Side.BUY else stop_loss - ref_price
                )
                if per_unit_risk <= 0:
                    reasons.append("stop loss on the wrong side of entry price")
                else:
                    trade_risk = per_unit_risk * req.qty
                    max_risk = limits.max_risk_per_trade_pct * limits.capital
                    if trade_risk > max_risk:
                        reasons.append(
                            f"trade risk ₹{trade_risk:,.0f} > "
                            f"{limits.max_risk_per_trade_pct:.1%} of capital (₹{max_risk:,.0f})"
                        )

        # 10. Rate limiter last — only consume a token if everything else passed
        if not reasons:
            ok, deny = self._rate.try_acquire(now)
            if not ok:
                reasons.append(deny)

        decision = RiskDecision(approved=not reasons, reasons=reasons)
        if decision.approved:
            self._audit.log(
                Category.ORDER, "approved",
                client_order_id=req.client_order_id, signal_id=req.signal_id,
                symbol=req.symbol, side=req.side.value, qty=req.qty,
            )
        else:
            self._audit.log(
                Category.VETO, "order_vetoed",
                client_order_id=req.client_order_id, signal_id=req.signal_id,
                symbol=req.symbol, side=req.side.value, qty=req.qty,
                reasons=reasons,
            )
        return decision
