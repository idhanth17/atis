"""Event-driven backtester.

Replays historical daily bars as timestamped quotes through the SAME
TradingEngine, RiskManager, and PaperBroker used in paper/live mode — one
code path, one fill model, one cost model (SECURITY.md §8).

Structural anti-leakage: strategies receive data only as a forward-ordered
quote stream. There is no API that serves a future bar; the replayer emits
strictly non-decreasing timestamps and each quote's asof equals its emission
time (tests/test_backtest.py locks this in).

Daily bars are honest about their own poverty: each day is two observable
moments (open ~09:15, close ~14:30) plus the 15:15+ square-off. Intraday
stops and ORB/VWAP baselines need intraday data — Phase 2's quote recorder.

The trading calendar is derived from the data itself: a weekday with no bars
was a holiday. This keeps historical years working without hand-maintaining
old holiday lists (the fail-closed config calendar still governs paper/live).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from atis.audit import Audit, Category
from atis.breakers import CircuitBreakers
from atis.broker.paper import PaperBroker
from atis.config import load_costs, load_paper, load_risk
from atis.costs import CostEngine
from atis.db import connect, init_db
from atis.engine import TradingEngine
from atis.killswitch import KillSwitch
from atis.mktcalendar import IST, NSECalendar
from atis.models import Kind, Quote
from atis.ratelimit import OrderRateLimiter
from atis.risk import RiskManager
from atis.strategy.base import Strategy


def load_bars(
    data_conn: sqlite3.Connection, symbols: list[str], start: date, end: date
) -> dict[date, list[dict]]:
    """{trade_date: [bar, ...]} ordered by date then symbol."""
    marks = ",".join("?" * len(symbols))
    rows = data_conn.execute(
        f"SELECT symbol, trade_date, open, high, low, close, volume "
        f"FROM ohlcv_daily WHERE symbol IN ({marks}) AND trade_date BETWEEN ? AND ? "
        f"ORDER BY trade_date, symbol",
        (*symbols, start.isoformat(), end.isoformat()),
    ).fetchall()
    days: dict[date, list[dict]] = {}
    for r in rows:
        days.setdefault(date.fromisoformat(r["trade_date"]), []).append(dict(r))
    return days


def calendar_from_data(trading_days: set[date]) -> NSECalendar:
    """Weekdays absent from the data were holidays. Only years covered by the
    data are known; anything else still fails closed."""
    if not trading_days:
        return NSECalendar({})
    holidays: dict[int, set[date]] = {}
    for year in range(min(trading_days).year, max(trading_days).year + 1):
        holidays[year] = set()
        d = date(year, 1, 1)
        while d.year == year:
            if d.weekday() < 5 and d not in trading_days:
                holidays[year].add(d)
            d += timedelta(days=1)
    return NSECalendar(holidays)


def replay_quotes(bars_by_day: dict[date, list[dict]]):
    """Yield (Quote, now) forward in time: opens ~09:15, closes ~14:30, then a
    sentinel re-quote after 15:15 so the engine's square-off fires."""
    for d in sorted(bars_by_day):
        bars = bars_by_day[d]
        for phase, hour, minute, price_key in (
            ("open", 9, 15, "open"),
            ("close", 14, 30, "close"),
        ):
            for i, bar in enumerate(bars):
                t = datetime(d.year, d.month, d.day, hour, minute, tzinfo=IST) \
                    + timedelta(seconds=i)
                yield Quote(symbol=bar["symbol"], last=bar[price_key],
                            asof=t, received_at=t, kind=Kind.EQUITY), t
        last = bars[-1]
        t = datetime(d.year, d.month, d.day, 15, 16, tzinfo=IST)
        yield Quote(symbol=last["symbol"], last=last["close"],
                    asof=t, received_at=t, kind=Kind.EQUITY), t


def run_backtest(
    data_conn: sqlite3.Connection,
    strategy: Strategy,
    config_dir: Path,
    symbols: list[str],
    start: date,
    end: date,
    kill_path: Path | str = "KILL",
    run_db: str = ":memory:",
) -> dict:
    limits = load_risk(config_dir)
    bars_by_day = load_bars(data_conn, symbols, start, end)
    if not bars_by_day:
        raise ValueError(f"No bars for {symbols} in {start}..{end} — run fetch-bhavcopy first")

    run_conn = connect(run_db)
    init_db(run_conn)
    audit = Audit(run_conn)
    calendar = calendar_from_data(set(bars_by_day))
    breakers = CircuitBreakers(
        run_conn, audit, limits.daily_loss_limit_pct, limits.max_consecutive_losses,
        limits.error_rate_limit, limits.error_rate_window_seconds,
    )
    risk = RiskManager(
        run_conn, limits, calendar, KillSwitch(kill_path), breakers,
        OrderRateLimiter(run_conn, limits.orders_per_second, limits.orders_per_day),
        audit,
    )
    broker = PaperBroker(run_conn, audit, CostEngine(load_costs(config_dir)),
                         load_paper(config_dir), limits.capital)
    engine = TradingEngine(run_conn, strategy, risk, broker, audit, calendar, limits)

    audit.log(Category.SYSTEM, "backtest_start", strategy=strategy.name,
              symbols=symbols, start=start.isoformat(), end=end.isoformat(),
              capital=limits.capital)

    equity_curve: list[tuple[date, float]] = []
    last_ledger_id = 0
    current_day: date | None = None
    day_start_equity = limits.capital

    def close_day(d: date) -> None:
        nonlocal last_ledger_id
        margins = broker.get_margins()
        # per-trade results for the consecutive-loss breaker
        for row in run_conn.execute(
            "SELECT id, amount FROM ledger WHERE entry_type='REALIZED_PNL' AND id > ?",
            (last_ledger_id,),
        ).fetchall():
            breakers.record_trade_result(d, row["amount"])
            last_ledger_id = max(last_ledger_id, row["id"])
        breakers.check_daily_loss(d, margins["equity"], day_start_equity)
        equity_curve.append((d, margins["equity"]))

    for quote, now in replay_quotes(bars_by_day):
        d = now.astimezone(IST).date()
        if current_day is not None and d != current_day:
            close_day(current_day)
            day_start_equity = broker.get_margins()["equity"]
        current_day = d
        engine.on_quote(quote, now)
    if current_day is not None:
        close_day(current_day)

    return _report(run_conn, broker, limits.capital, equity_curve, strategy, symbols,
                   start, end)


def _report(run_conn, broker, capital, equity_curve, strategy, symbols, start, end) -> dict:
    gross = run_conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE entry_type='REALIZED_PNL'"
    ).fetchone()[0]
    costs = -run_conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE entry_type='COSTS'"
    ).fetchone()[0]
    trades = run_conn.execute(
        "SELECT amount FROM ledger WHERE entry_type='REALIZED_PNL'"
    ).fetchall()
    wins = sum(1 for t in trades if t["amount"] > 0)
    end_equity = broker.get_margins()["equity"]

    peak, max_dd = -float("inf"), 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    n_signals = run_conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    n_vetoes = run_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE category='VETO'"
    ).fetchone()[0]
    n_fills = run_conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    breaker_trips = run_conn.execute(
        "SELECT key, value FROM breaker_state WHERE key LIKE 'tripped:%'"
    ).fetchall()

    return {
        "strategy": strategy.name,
        "model_version": strategy.model_version,
        "symbols": symbols,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": len(equity_curve),
        "capital": capital,
        "end_equity": round(end_equity, 2),
        "net_pnl": round(end_equity - capital, 2),
        "gross_pnl": round(gross, 2),
        "total_costs": round(costs, 2),
        "trades": len(trades),
        "wins": wins,
        "win_rate": round(wins / len(trades), 4) if trades else None,
        "max_drawdown_pct": round(max_dd, 4),
        "signals": n_signals,
        "vetoes": n_vetoes,
        "fills": n_fills,
        "breaker_trips": [f"{r['key']}={r['value']}" for r in breaker_trips],
    }
