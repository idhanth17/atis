# ATIS — AI Trading Intelligence System (India Edition) · v2

### Target: NSE/BSE · Capital: ₹1,000–₹10,000 · Free-first (no broker fees until go-live)

This is the revised master plan. It supersedes the v1 draft. The three biggest changes:

1. **Free-first development.** No Zerodha Kite Connect subscription until the system has *earned* the right to trade real money. Phases 0–3 run entirely on free data (`yfinance`, `nsepython`, official NSE bhavcopy) and a custom paper-trading engine. A `DataProvider` / `Broker` abstraction makes Kite a drop-in later.
2. **Security and safety as a first-class module**, not an afterthought. See [docs/SECURITY.md](docs/SECURITY.md). Real money only flows after every control in that document exists and is tested.
3. **Honesty about the odds.** Several claims in v1 were stale or wrong (SGX Nifty, lot sizes, leverage, return targets). Corrections below and in [docs/COMPLIANCE_AND_RISK.md](docs/COMPLIANCE_AND_RISK.md).

---

## ⚠️ Reality check (read before writing any code)

- **SEBI's own studies**: ~93% of individual F&O traders lost money over FY22–FY24 (average loss ≈ ₹2 lakh); ~7 in 10 intraday equity traders lose money. An ML system does not exempt you from these base rates — it has to beat them *after costs*.
- **Costs dominate small capital.** On a ₹1,000 option premium, a round trip costs ≈ ₹45–50 (flat ₹20/order brokerage × 2 + STT + exchange charges + GST) ≈ **4–5% of the position**. At 1:1 reward:risk you need a ~53%+ win rate just to break even. Every backtest and paper trade in ATIS must charge the full Indian cost stack (modelled in [docs/PAPER_TRADING_ENGINE.md](docs/PAPER_TRADING_ENGINE.md)).
- **The v1 return table (4–30%/month) is fantasy.** Sustained 8%/month is world-class-hedge-fund territory. The honest goal for this project: **demonstrate positive expectancy after costs over ≥ 6 months of realistic simulation**. Rupee targets come later, if ever.
- **The real product of Phases 0–3 is the measurement machinery** — a backtester and paper trader you can trust. Most retail algo projects fail because their simulator lies to them (look-ahead bias, free fills, no costs), not because their model is weak.

## 🇮🇳 Corrected India facts (v1 was stale)

| v1 claim | Reality (as of 2026) |
|---|---|
| "SGX Nifty premium at 9:00 AM" | SGX Nifty no longer exists — it migrated to **GIFT Nifty** (NSE IX, GIFT City) in July 2023. Use GIFT Nifty for the pre-market gap signal. |
| "MIS gives 5x–20x leverage" | SEBI peak-margin rules (2021) cap intraday equity leverage at **~5x max** (20% margin). Many brokers give less on volatile stocks. |
| "Nifty & BankNifty weekly options" | **BankNifty weekly expiries were discontinued in Nov 2024.** Each exchange gets one weekly index expiry: **Nifty (NSE)** and **Sensex (BSE)**. BankNifty is monthly only. |
| Small lots for small capital | Lot sizes were raised: **Nifty 65, BankNifty 35, FINNIFTY 65, MIDCPNIFTY 140** (post Dec 30, 2025 revision). One lot of even a ₹15 OTM Nifty option ≈ ₹975 premium — a single trade can consume all of a ₹1,000 account. With ₹1,000–₹3,000, option buying is lottery-ticket sizing, not a strategy. |
| "Kite Connect ₹2,000/month" | Kite Connect order APIs have been **free for individual (personal) use since late 2023**; the *historical data* add-on is still paid — which we don't need, because history comes from free sources. Verify current pricing at signup. |
| Algo trading is a grey area | **SEBI's retail algo framework is fully mandatory since April 1, 2026**: static whitelisted IP for API trading, ≤ 10 orders/second treated as regular API use, anything above (or any marketed strategy) must be registered with the exchange via the broker. ATIS is designed to stay under 10 OPS with a hard rate limiter. Details in [docs/COMPLIANCE_AND_RISK.md](docs/COMPLIANCE_AND_RISK.md). |
| Twitter/X sentiment feed | X API now costs $100+/month for meaningful access. Dropped from the free-first plan; news RSS + NSE/BSE filings are sufficient (and higher quality) for sentiment. |

---

## Architecture v2 — modular monolith first

The v1 stack (Postgres + TimescaleDB + Redis + Prefect + Grafana + Prometheus + microservice layers) is over-engineered for a ₹10,000 account and, more importantly, **more surface area to secure**. v2 is a single well-structured Python application; every heavyweight component is deferred until a measured need appears.

```
┌────────────────────────────────────────────────────────────┐
│  Dashboard (FastAPI + simple web UI, localhost-only)       │
└──────────────────────────┬─────────────────────────────────┘
┌──────────────────────────▼─────────────────────────────────┐
│  Orchestrator (APScheduler, IST market-calendar aware)     │
│  · owns the daily state machine: PRE_MKT → OPEN → SQUARE   │
│    _OFF → CLOSED → RETRAIN                                 │
│  · heartbeat file → dead-man's switch                      │
└───┬──────────────┬──────────────┬──────────────┬───────────┘
┌───▼─────┐  ┌────▼─────┐  ┌─────▼─────┐  ┌─────▼─────────┐
│ Data    │  │ Signals  │  │ Risk      │  │ Broker        │
│Providers│  │ (rules → │  │ Manager   │  │ interface     │
│(plugin) │  │ ML later)│  │ (VETO     │  │ (plugin)      │
│         │  │          │  │  power)   │  │               │
│yfinance │  │          │  │           │  │ PaperBroker ✅ │
│nsepython│  │          │  │           │  │ KiteBroker 🔒 │
│bhavcopy │  │          │  │           │  │ (Phase 4)     │
└───┬─────┘  └────┬─────┘  └─────┬─────┘  └─────┬─────────┘
┌───▼─────────────▼──────────────▼──────────────▼───────────┐
│  SQLite (WAL mode) — trades, signals, audit log, OHLCV    │
│  (migrate to Postgres only if/when SQLite measurably hurts)│
└────────────────────────────────────────────────────────────┘
```

Design rules that make this safe and testable:

- **Two hard seams**: `DataProvider` and `Broker` are abstract interfaces. Backtest, paper, and live modes differ *only* in which implementations are injected. Strategy code cannot tell (and must not know) which mode it's in — this is what makes paper results transferable.
- **The Risk Manager is a separate module with veto power.** Signals *propose*; risk *disposes*. No order reaches a broker (paper or real) without passing pre-trade checks. Risk limits live in config, are enforced in code, and are covered by unit tests — see [docs/SECURITY.md](docs/SECURITY.md) for the full list.
- **Append-only audit log.** Every signal, veto, order, fill, and error is written to an append-only table with timestamps and the config/model version that produced it. If you can't reconstruct why a trade happened, you can't debug the system or answer a broker/exchange query.
- **Event-driven core.** Internal components communicate via a simple in-process event bus (`MarketData`, `Signal`, `OrderRequest`, `Fill`, `RiskBreach` events). This is what lets the same engine replay historical events (backtest) or consume live ones (paper/live).
- **No cloud until Phase 4.** Runs on your own machine. Fewer credentials, no exposed ports, dashboard binds to 127.0.0.1.

## Technology stack v2

| Component | v2 choice | Deferred (adopt only when needed) |
|---|---|---|
| Language | Python 3.11+, `uv` for locked, hash-pinned deps | — |
| Storage | SQLite (WAL) | Postgres, TimescaleDB |
| Cache/queue | In-process (dict + asyncio queues) | Redis |
| Scheduling | APScheduler + `pandas_market_calendars` (NSE calendar) | Prefect |
| Backtesting | `vectorbt` for research scans + **own event-driven engine** for final validation (same code path as paper/live) | — |
| ML | scikit-learn, LightGBM | PyTorch/TFT, RL — Phase 3+, only if baseline shows edge |
| NLP | FinBERT (local, free) on news headlines | Paid LLM APIs |
| Dashboard | FastAPI + HTMX or a small Next.js app, localhost-only | Grafana/Prometheus |
| Broker | `PaperBroker` (custom) | Kite Connect (Phase 4), Upstox/Angel as alternates |
| Config/secrets | `.env` (gitignored) + OS keyring; pydantic-settings validation | Cloud secret manager |

## Free data sources (Phase 0–3)

| Need | Source | Notes / failure modes |
|---|---|---|
| Daily OHLCV history (equities + indices) | **Official NSE bhavcopy** (daily EOD files) — canonical; `yfinance` (`.NS`/`.BO` suffix) as convenience | yfinance is *unofficial* and breaks occasionally; bhavcopy is the source of truth. Store raw downloads immutably, parse into SQLite. |
| Intraday bars | `yfinance` (1m limited to ~last 7–30 days; 15m-delayed quotes), `nsepython` live quotes | **Delayed/limited — fine for paper trading if the simulator timestamps honestly.** Build the intraday archive from day one: record live quotes daily so you accumulate your own tick/1-min history. |
| F&O chain, OI, PCR | `nsepython` (NSE option-chain API) | Fragile unofficial API: throttle politely (1 req/2–3 s), rotate user-agent honestly, cache aggressively, expect breakage — wrap in a provider with circuit breaker + stale-data flag. |
| FII/DII daily activity | NSE FII/DII provisional data (free, EOD) | Publishes after market close — it's a T+1 feature, never a same-day one (leakage trap). |
| Pre-market gap | GIFT Nifty level (public quote pages), previous US close via yfinance | — |
| News sentiment | RSS: Economic Times Markets, MoneyControl, Business Standard; NSE/BSE corporate announcements | Scraped text is **untrusted input** — see security doc §6 (injection into NLP/LLM pipeline). |
| India VIX, USD/INR | NSE / yfinance (`^INDIAVIX`, `INR=X`) | — |
| Corporate actions (splits/bonus/dividends) | NSE/BSE announcements | **Must-have** for backtest correctness — unadjusted prices produce phantom signals. |

Rules: every provider records `fetched_at`, `source`, and `is_delayed`; raw payloads are archived before parsing; a nightly data-quality job checks for gaps, duplicate candles, unadjusted splits, and zero-volume anomalies. Respect each site's terms of service and rate limits — polite scraping only, no auth-wall bypassing.

## Signals & ML — earn complexity

The v1 plan jumps straight to a TFT + XGBoost + PPO + meta-learner ensemble. That's four ways to overfit before you've proven a single edge exists. v2 sequence:

1. **Rule baselines first** (no ML): opening-range breakout, VWAP mean-reversion, gap-and-go with FII/DII filter. Cheap, interpretable, and they calibrate the backtester. If simple momentum can't get near breakeven after costs, ML on the same features rarely saves it.
2. **One model**: LightGBM classifier (up/down/flat over a fixed horizon) on technical + sentiment + macro features. Walk-forward validation only — expanding-window train, out-of-sample test, step forward. Report *net-of-cost* metrics only.
3. **Leakage checklist enforced in code** (a `FeatureSpec` declares each feature's availability timestamp): no same-day FII/DII, no EOD indicators mid-day, no future bars in rolling windows, corporate-action-adjusted prices, survivorship-bias-free universe (use point-in-time index constituents, not today's Nifty 100).
4. **TFT / RL / ensembles**: gated behind "the LightGBM baseline shows stable positive expectancy out-of-sample for 3+ months." Otherwise they stay on the shelf.

Signal output schema stays as in v1 (ticker, action, confidence, entry/target/stop, size, catalysts) — it's good — with two additions: `model_version` and `features_hash` so every live trade is reproducible.

## Roadmap v2 (gate-based, not calendar-based)

Each phase has an **exit gate**. You don't move on because a month passed; you move on because the gate is green.

**Phase 0 — Foundations & safety rails (1–2 weeks)**
Repo scaffold, `uv` lockfile, pre-commit (secret-scan + lint), config via pydantic-settings, SQLite schema, audit log, event bus, NSE market calendar, kill-switch mechanism, CI running tests.
*Gate: risk-limit unit tests pass; a dummy strategy can emit a signal that gets vetoed and audited.*

**Phase 1 — Data & backtester (3–5 weeks)**
Bhavcopy + yfinance + nsepython providers with archival and quality checks; corporate-action adjustment; indicator library (RSI, MACD, VWAP, BB, ATR); event-driven backtester with the full Indian cost model; the three rule baselines backtested over 2022–2025.
*Gate: backtester reproduces a hand-computed 10-trade P&L to the rupee, including all charges; a deliberately look-ahead-biased strategy is caught by the leakage tests.*

**Phase 2 — Paper trading engine (2–3 weeks + runs continuously thereafter)**
`PaperBroker` per [docs/PAPER_TRADING_ENGINE.md](docs/PAPER_TRADING_ENGINE.md): live-ish quotes, realistic fills (slippage, spread, partial fills, delayed-data honesty), full cost model, EOD 3:15 pm square-off, daily P&L report, dashboard.
*Gate: 20 consecutive trading days of unattended paper operation with zero crashes, zero risk-limit violations, and reconciled ledgers.*

**Phase 3 — Intelligence (6–10 weeks, overlaps Phase 2 operation)**
FinBERT sentiment on RSS/filings; FII/DII, VIX, GIFT-gap features; LightGBM with walk-forward; champion/challenger — new models paper-trade alongside the rule baseline, never replace it silently.
*Gate: net-of-cost walk-forward expectancy > 0 with Sharpe > 1 out-of-sample, and ≥ 60 paper trading days confirming the backtest distribution (paper results within the backtest's confidence interval — if paper is much worse, the simulator is lying somewhere; find it).*

**Phase 4 — Live, smallest possible (only after Phase 3 gate + [security checklist](docs/SECURITY.md) 100% complete)**
Kite Connect (free personal tier) behind the same `Broker` interface; SEBI-compliance items (static IP, OPS rate limiter, broker algo terms); live with ₹1,000–₹2,000 for 30+ days at minimum size; reconciliation loop against broker statements; only then consider scaling.
*Gate to scale capital: 30 live days where live fills/costs match paper model within tolerance, and all safety drills (kill switch, dead-man, network loss) pass.*

## Daily workflow (paper mode, IST)

```
08:45  Wake; fetch overnight news, GIFT Nifty, global closes; data-quality check
09:00  Pre-open analysis → watchlist + planned risk budget for the day
09:15  Market open — signals flow; Risk Manager gates every order
09:15–15:00  Monitor loop (30 s): quotes → mark-to-market → trailing stops
15:00  No new entries after 15:00
15:15  Square off everything (buffer before broker auto-square at ~15:20)
15:30  Close; write daily report (P&L net of modelled costs, win rate,
       slippage vs assumption, risk events); archive raw data
16:00  Nightly jobs: data QC, feature build, walk-forward refresh (not
       silent redeploy — champion/challenger)
```

## Running the code (Phase 0 — built)

```powershell
uv sync                  # core deps + tests
uv sync --extra data     # add requests/yfinance/pandas for data ingestion
uv run pytest            # 52 tests: risk vetoes, costs to the paisa, fill model, audit immutability
uv run atis gate-check   # Phase 0 exit gate: oversized signal → vetoed → audited
uv run atis costs        # the ₹49-on-₹1,000 lesson, computed live
uv run atis kill "why"   # engage the kill switch (creates ./KILL)
uv run atis resume       # disengage (deliberate human action)
uv run atis audit -n 20  # tail the append-only audit log
uv run atis fetch-bhavcopy --start 2026-07-06 --end 2026-07-10
```

Layout: `src/atis/` (config, db, audit, mktcalendar, killswitch, ratelimit,
breakers, costs, risk, engine, `broker/paper.py`, `data/bhavcopy.py`,
`strategy/`), `tests/`, `config/*.yaml` (risk limits, cost stack, simulator
params, NSE holidays — all enforced in code and validated on every test run).

## Where to start (this week)

1. Scaffold the repo in this folder (`src/atis/`, `tests/`, `docs/`, `config/`) with `uv init`.
2. Build Phase 0: config, SQLite schema, audit log, risk-limit config + tests, kill switch.
3. First data task: download and archive 3 years of NSE bhavcopy + index data; load into SQLite; write the corporate-action adjuster.
4. First strategy task: opening-range breakout baseline through the backtester with full costs — and look at how much the costs change the result. That number is the whole lesson of Phase 1.

**Companion docs:**
- [docs/SECURITY.md](docs/SECURITY.md) — threat model, controls, go-live security checklist
- [docs/PAPER_TRADING_ENGINE.md](docs/PAPER_TRADING_ENGINE.md) — simulator design, Indian cost model, fill model
- [docs/COMPLIANCE_AND_RISK.md](docs/COMPLIANCE_AND_RISK.md) — SEBI 2026 algo framework, tax notes, honest expectations, go-live gates
