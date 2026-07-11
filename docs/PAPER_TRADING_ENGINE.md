# Paper Trading Engine — Design Spec

The `PaperBroker` is the heart of the free-first plan: it must be **pessimistic enough that live trading is a pleasant surprise, not a shock**. Every design choice below errs against the strategy.

---

## 1. Position in the architecture

```
Strategy → Risk Manager → Broker (interface)
                             ├── PaperBroker   (this doc)
                             └── KiteBroker    (Phase 4, same interface)
```

The `Broker` interface: `place_order()`, `modify_order()`, `cancel_order()`, `get_positions()`, `get_orders()`, `get_margins()`. Strategies and the risk layer never know which implementation is behind it. `PaperBroker` also runs *inside the backtester* (fed by historical events) — one fill/cost model everywhere.

## 2. Data honesty

Free live-ish data is delayed and sparse. The engine must never pretend otherwise:

- Every quote carries `asof` (exchange time) and `received_at` (local). If `received_at − asof > 60 s`, the quote is **stale**: fills against it are refused and the strategy is told the market is unavailable — exactly what a defensive live system would do.
- yfinance intraday (delayed ~15 min) is acceptable for paper **only because both the signal and the fill see the same delayed clock** — the simulation is internally consistent, just time-shifted. Record this in reports: "paper session on delayed feed."
- From day one, run a **quote recorder** during market hours (nsepython, throttled) to build a private 1-minute archive. After a few weeks, paper sessions can replay *your own* recorded data with true timestamps — better than yfinance's limits.
- Circuit breaker: any gap > 3 min in the feed while a position is open → square off the paper position at last good quote **plus penalty slippage** (see §4). Live would do the same.

## 3. Order and margin model

- Product types: MIS only (intraday). Enforce NSE price bands and tick size (₹0.05); reject orders outside circuit limits.
- Margin: replicate broker rules — equity MIS ≈ 5x leverage (20% margin, more for volatile stocks — use a conservative flat 5x cap), long options require full premium. Margin insufficient → rejection, same error shape Kite would return.
- Auto square-off at 15:20 with **extra slippage** (the broker's RMS square-off is a market order in a busy tape). Our own square-off begins 15:15.
- Order types: LIMIT (default), MARKET (discouraged), SL and SL-M for stops. Trigger logic: stop fires when last-traded price crosses trigger; the resulting order then fills per §4 — a stop is **not** a guaranteed fill at trigger price (gap-through risk is real and must be simulated: fill at the worse of trigger or next quote).

## 4. Fill model (pessimistic by design)

Free feeds give last price, sometimes best bid/ask, no depth. Model:

| Situation | Fill rule |
|---|---|
| LIMIT buy, `limit ≥ ask` (marketable) | Fill at `ask + slippage`; if no bid/ask, `last + spread_est/2 + slippage` |
| LIMIT buy, `limit < ask` | Rests; fills only when quote trades **through** the limit (`last < limit`), not merely touches it — touch-fills are the classic paper-trading lie |
| MARKET | `last + spread_est/2 + 2 × slippage` |
| SL/SL-M triggered | Worse of (trigger price, next observed quote) + slippage |
| Partial fills | Order size > 2% of the bar's yfinance volume → fill proportionally over subsequent bars (with ₹1k–10k capital this rarely binds on Nifty-100 names, but it stops fantasy scaling on illiquid picks) |

- `slippage`: per-instrument config, default 0.05% equities / 0.5% (min 1 tick) options premium. Recalibrate against real fills in Phase 4 and feed back.
- `spread_est`: rolling estimate per instrument from observed bid/ask when available; default 0.05% large-caps, 0.5–2% options by moneyness.
- Options extra: quotes for illiquid strikes can be minutes old — restrict the tradable universe to strikes with (observed) spread < 3% of premium.

## 5. Indian cost model — charged on every simulated trade

Configurable table (rates drift; review quarterly, keep in `config/costs.yaml`):

| Charge | Intraday equity (MIS) | Index options (buy side) |
|---|---|---|
| Brokerage | min(0.03%, ₹20) per executed order | flat ₹20 per executed order |
| STT | 0.025% on **sell** value | 0.1% on **sell** premium |
| Exchange txn | ~0.00297% (NSE) on turnover | ~0.035% on premium |
| SEBI charges | ₹10/crore | ₹10/crore |
| Stamp duty | 0.003% on **buy** value | 0.003% on buy premium |
| GST | 18% on (brokerage + exchange txn + SEBI) | same |

Worked examples the engine's test suite must reproduce to the paisa:

- **₹5,000 equity round trip** → ≈ ₹5–6 total ≈ **0.11%** of turnover. Survivable, but a 0.11% haircut on every trade compounds: 3 trades/day ≈ 0.33%/day of drag.
- **₹1,000 option premium round trip** → ₹40 brokerage + ₹1 STT + ₹0.7 txn + ~₹7.3 GST + misc ≈ **₹49 ≈ 4.9% of the position**. This single number is why option *buying* on a ₹1,000 account has a brutal hurdle, and why the engine must always charge it.

Also model: DP charges are avoided by MIS (no delivery), but if a position ever converts to CNC, charge ₹15.34/scrip on sell.

## 6. Ledger & reports

- Double-entry style ledger in SQLite: cash, positions, blocked margin, realized/unrealized P&L, cumulative costs — every fill writes balanced entries. An invariant check (`cash + margin + MTM = equity`) runs after every event; violation = bug = halt.
- Daily report (auto, 15:35): net P&L **after costs**, gross-vs-net gap (cost drag made visible daily), win rate, avg R, slippage charged, risk events, per-signal attribution.
- Rolling report: equity curve, max drawdown, Sharpe (net), profit factor, and **paper-vs-backtest drift** — the distribution of paper results overlaid on the backtest's expectation; sustained divergence means the simulator or the strategy is broken. Investigate before continuing.

## 7. Acceptance tests (engine is "done" when)

- [ ] Hand-computed 10-trade session (mixed equity + option, with a stop-out and a 15:20 force-square) matches engine ledger to the paisa
- [ ] Limit order at the touch does **not** fill until traded through
- [ ] Stale-feed scenario squares off with penalty and trips the breaker
- [ ] Gap-through stop fills at the gap price, not the trigger price
- [ ] Margin rejection matches Kite's error shape
- [ ] Ledger invariant fuzz test: 10,000 random valid events, invariant never breaks
- [ ] `--fantasy` (zero-cost) mode visibly watermarks every report it touches
