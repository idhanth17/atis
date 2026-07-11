"""ATIS command line.

  atis init-db        create/upgrade the SQLite schema
  atis gate-check     run the Phase 0 gate scenario and print the audit trail
  atis costs          print the worked cost examples (the whole lesson of Phase 1)
  atis kill "reason"  engage the kill switch
  atis resume         disengage the kill switch (deliberate human action)
  atis audit [-n N]   show the last N audit log entries
  atis fetch-bhavcopy --start YYYY-MM-DD --end YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta

from atis import config as cfg
from atis.audit import Audit
from atis.db import connect, init_db
from atis.killswitch import KillSwitch
from atis.mktcalendar import IST, NSECalendar


def _boot(settings: cfg.Settings):
    conn = connect(settings.db_path)
    init_db(conn)
    return conn, Audit(conn)


def cmd_init_db(settings: cfg.Settings, _args) -> int:
    conn, audit = _boot(settings)
    audit.log("SYSTEM", "init_db", db=str(settings.db_path))
    print(f"Database ready at {settings.db_path}")
    return 0


def cmd_costs(settings: cfg.Settings, _args) -> int:
    from atis.costs import CostEngine
    from atis.models import Kind

    engine = CostEngine(cfg.load_costs(settings.config_dir))
    eq = engine.round_trip(Kind.EQUITY, 5000.0)
    opt = engine.round_trip(Kind.OPTION, 1000.0)
    print("Worked cost examples (docs/PAPER_TRADING_ENGINE.md §5):")
    print(f"  ₹5,000 intraday equity round trip: ₹{eq:.2f}  ({eq / 5000:.3%} of position)")
    print(f"  ₹1,000 option premium round trip:  ₹{opt:.2f}  ({opt / 1000:.3%} of position)")
    print("\nThat second number is why option buying on a small account has a brutal hurdle.")
    return 0


def cmd_kill(settings: cfg.Settings, args) -> int:
    conn, audit = _boot(settings)
    ks = KillSwitch(settings.kill_file)
    ks.engage(args.reason)
    audit.log("KILL", "engaged", reason=args.reason)
    print(f"KILL switch engaged: {settings.kill_file} — no orders will be accepted.")
    return 0


def cmd_resume(settings: cfg.Settings, _args) -> int:
    conn, audit = _boot(settings)
    ks = KillSwitch(settings.kill_file)
    if not ks.engaged():
        print("Kill switch is not engaged.")
        return 0
    ks.disengage()
    audit.log("KILL", "disengaged")
    print("Kill switch disengaged.")
    return 0


def cmd_audit(settings: cfg.Settings, args) -> int:
    conn, audit = _boot(settings)
    for row in audit.tail(args.n):
        details = json.loads(row["details"])
        print(f"[{row['ts_utc']}] {row['category']:8s} {row['event']:16s} {details}")
    return 0


def cmd_gate_check(settings: cfg.Settings, _args) -> int:
    """Phase 0 exit gate, live: a dummy strategy emits an oversized signal,
    the Risk Manager vetoes it, and the veto is in the audit log."""
    from atis.breakers import CircuitBreakers
    from atis.broker.paper import PaperBroker
    from atis.costs import CostEngine
    from atis.engine import TradingEngine
    from atis.models import Kind, Quote
    from atis.ratelimit import OrderRateLimiter
    from atis.risk import RiskManager
    from atis.strategy.base import OversizedDummyStrategy

    conn, audit = _boot(settings)
    limits = cfg.load_risk(settings.config_dir)
    calendar = NSECalendar(cfg.load_holidays(settings.config_dir))
    ks = KillSwitch(settings.kill_file)
    breakers = CircuitBreakers(
        conn, audit, limits.daily_loss_limit_pct, limits.max_consecutive_losses,
        limits.error_rate_limit, limits.error_rate_window_seconds,
    )
    rate = OrderRateLimiter(conn, limits.orders_per_second, limits.orders_per_day)
    risk = RiskManager(conn, limits, calendar, ks, breakers, rate, audit)
    broker = PaperBroker(conn, audit, CostEngine(cfg.load_costs(settings.config_dir)),
                         cfg.load_paper(settings.config_dir), limits.capital)
    engine = TradingEngine(conn, OversizedDummyStrategy(), risk, broker, audit,
                           calendar, limits)

    # Simulate a mid-session quote on the next trading day at 10:00 IST
    d = datetime.now(IST).date()
    while not calendar.is_trading_day(d):
        d += timedelta(days=1)
    now = datetime(d.year, d.month, d.day, 10, 0, tzinfo=IST)
    quote = Quote(symbol="RELIANCE", last=2940.50, asof=now, received_at=now,
                  kind=Kind.EQUITY)
    engine.on_quote(quote, now)

    vetoed = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE category='VETO'"
    ).fetchone()[0]
    placed = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    print("Phase 0 gate check:")
    print(f"  signals emitted : 1")
    print(f"  vetoes audited  : {vetoed}")
    print(f"  orders placed   : {placed}")
    if vetoed >= 1 and placed == 0:
        print("  GATE: GREEN — oversized signal was vetoed and audited; nothing reached the broker.")
        print("\nLast audit entries:")
        for row in audit.tail(4):
            print(f"  [{row['category']}] {row['event']}: {row['details'][:140]}")
        return 0
    print("  GATE: RED — safety rails did not hold. Do not proceed.")
    return 1


def cmd_backtest(settings: cfg.Settings, args) -> int:
    import yaml

    from atis.backtest import run_backtest
    from atis.strategy.gap_and_go import GapAndGoDaily

    conn, _ = _boot(settings)
    limits = cfg.load_risk(settings.config_dir)
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        with open(settings.config_dir / "universe.yaml", encoding="utf-8") as f:
            symbols = yaml.safe_load(f)["symbols"]
    strategy = GapAndGoDaily(capital=limits.capital)
    report = run_backtest(
        conn, strategy, settings.config_dir, symbols,
        date.fromisoformat(args.start), date.fromisoformat(args.end),
        kill_path=settings.kill_file,
    )
    print(json.dumps(report, indent=2))
    gross, net = report["gross_pnl"], report["net_pnl"]
    if gross != 0:
        print(f"\nCost drag: gross ₹{gross:,.2f} → net ₹{net:,.2f} "
              f"(₹{report['total_costs']:,.2f} in charges)")
    return 0


def cmd_fetch_bhavcopy(settings: cfg.Settings, args) -> int:
    from atis.data.bhavcopy import BhavcopyProvider

    conn, audit = _boot(settings)
    calendar = NSECalendar(cfg.load_holidays(settings.config_dir))
    provider = BhavcopyProvider(conn, settings.data_dir / "raw" / "bhavcopy")
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    print(f"Fetching NSE bhavcopy {start} → {end} (throttled, archived to raw/)...")
    summary = provider.sync_range(start, end, calendar)
    audit.log("SYSTEM", "bhavcopy_sync", **summary)
    print(f"Loaded {summary['days_loaded']} days, {summary['rows']} rows.")
    if summary["missing"]:
        print(f"Missing (holiday/unpublished/error): {', '.join(summary['missing'])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles often default to cp1252, which can't print ₹
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="atis", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("gate-check")
    sub.add_parser("costs")
    p_kill = sub.add_parser("kill")
    p_kill.add_argument("reason", nargs="?", default="manual kill")
    sub.add_parser("resume")
    p_audit = sub.add_parser("audit")
    p_audit.add_argument("-n", type=int, default=20)
    p_bhav = sub.add_parser("fetch-bhavcopy")
    p_bhav.add_argument("--start", required=True)
    p_bhav.add_argument("--end", required=True)
    p_bt = sub.add_parser("backtest")
    p_bt.add_argument("--start", required=True)
    p_bt.add_argument("--end", required=True)
    p_bt.add_argument("--symbols", default=None,
                      help="comma-separated; defaults to config/universe.yaml")

    args = parser.parse_args(argv)
    settings = cfg.Settings()
    handlers = {
        "init-db": cmd_init_db,
        "gate-check": cmd_gate_check,
        "costs": cmd_costs,
        "kill": cmd_kill,
        "resume": cmd_resume,
        "audit": cmd_audit,
        "fetch-bhavcopy": cmd_fetch_bhavcopy,
        "backtest": cmd_backtest,
    }
    return handlers[args.cmd](settings, args)


if __name__ == "__main__":
    sys.exit(main())
