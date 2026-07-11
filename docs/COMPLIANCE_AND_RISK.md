# Compliance, Taxes & Honest Risk Expectations

Not legal or investment advice — an engineering checklist of the rules that constrain the design, plus the honest math. Verify current rules with SEBI/NSE/your broker before go-live; they changed three times while this project was being planned.

---

## 1. SEBI retail algo framework (fully in force since April 1, 2026)

The framework (SEBI circular, Feb 2025; phased Oct 2025 → Apr 2026) governs any retail trading through broker APIs. What it means for ATIS:

| Rule | ATIS design response |
|---|---|
| Orders via API above **10 orders/second** (per exchange) are "algo" and require exchange registration through the broker | Hard rate limiter at ≤ 2 OPS (see SECURITY.md §3.2) keeps ATIS in the regular-API-user category. Self-developed, self-used strategies under the threshold don't need empanelment — but confirm the current interpretation with Zerodha at Phase 4 signup. |
| **Static whitelisted IP** mandatory for API trading; one primary (+ optional secondary) per client | Budget for a static IP from your ISP or a fixed egress (e.g., WireGuard to a VPS with a static address — the *orders* originate from the whitelisted IP). Arrange before Phase 4; it takes days, not hours. |
| Algo orders get an exchange identifier/tag; brokers responsible for API client conduct | Accept Zerodha's API/algo terms; keep the audit log — it's exactly what you'd need if the broker ever queries activity. |
| No unregistered third-party "strategy providers" | ATIS is self-built and self-used — fine. Never sell/share signals; that instantly changes your regulatory category. |

Also standing since 2021: **peak margin rules** cap intraday equity leverage at ~5x; brokers may give less. Design capital math at 3x conservative usage, per the README.

## 2. Broker & API compliance notes (Phase 4)

- Kite Connect personal use is free (verify at signup); historical-data add-on not needed (we have our own archive by then).
- Daily access-token expiry: manual morning login is a compliance-friendly ritual, not a nuisance. Don't automate password entry; storing broker passwords in a bot violates both security sense and typically the broker's terms.
- All automated positions are MIS → broker RMS force-squares by ~15:20, which is the system's ultimate physical backstop.
- Keep contract notes and the ATIS audit log aligned (nightly reconciliation) — this is also your tax record.

## 3. Taxes (affects expectancy math, so it belongs in the plan)

- **Intraday equity** profits = *speculative business income*, taxed at slab rate; losses offset only speculative gains (8-year carry-forward, must file on time).
- **F&O** = *non-speculative business income*: slab rate, losses offset most other income heads, carry-forward 8 years. Turnover-based audit thresholds can apply — keep clean books (the ledger gives you this for free).
- Practical effect: at a 20–30% slab, a gross edge of 0.3%/day is ~0.2%/day post-tax *before* you count your time. Put post-tax numbers in the rolling report so the goal stays honest.

## 4. Honest expectations (replaces v1's return table)

**The base rates:**
- SEBI (Sep 2024 study): **~93% of individual F&O traders lost money** across FY22–FY24; average loss ≈ ₹2 lakh over 3 years.
- SEBI (2023, intraday equity): ~7 in 10 intraday cash traders lose money; loss-makers skew young and small-capital.
- Costs are regressive: the smaller the account, the larger flat charges loom (₹40 option round trip = 4.9% of a ₹1,000 position, 0.049% of a ₹1 lakh position).

**What "success" means for ATIS, in order:**
1. A backtester that catches its own lies (leak canaries pass, costs always on).
2. Paper results that *match* backtest expectations for 60+ days — proving the measurement machinery.
3. Net-of-cost, net-of-slippage positive expectancy sustained out-of-sample. Any positive number here puts you in roughly the top decile of retail participants.
4. Only then: live capital, sized so a total loss is a tolerable tuition fee (₹1,000–₹2,000).

**Kill criteria** (decide them now, while calm): if after 6 months of honest measurement no strategy shows net positive expectancy, the correct output of this project is *"we proved these approaches don't clear costs at this capital"* — a genuinely valuable result that most people pay much more to learn. The infrastructure (data pipeline, backtester, risk engine) remains reusable for lower-frequency, higher-capital strategies where costs matter less.

**Where small capital actually has a fair fight** (design implications):
- Fewer, higher-conviction trades (cost drag scales with trade count — the daily-loss breaker and 25-orders/day cap are expectancy features, not just safety features).
- Intraday equity on liquid Nifty-100 names (percentage-based brokerage, tight spreads) rather than option buying (flat ₹20/order + wide spreads + theta).
- If/when capital grows past ~₹50k, revisit instrument choice — several strategies infeasible at ₹5k become viable purely because flat costs shrink in relative terms.

## 5. References

- SEBI circular — [Safer participation of retail investors in algorithmic trading (Feb 2025)](https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html)
- Zerodha — [SEBI's new rules for index derivatives](https://zerodha.com/z-connect/business-updates/sebis-new-rules-for-index-derivatives-heres-whats-changing)
- NSE — [lot-size revision circular effective Dec 30, 2025](https://nsearchives.nseindia.com/content/circulars/FAOP70616.pdf)
- Practical guides to the 2025–26 algo framework: [uTrade Algos](https://www.utradealgos.com/blog/decoding-sebis-new-algo-trading-rules-for-retail-investors-all-you-need-to-know), [AlgoBulls](https://algobulls.com/blog/industry-insights-and-updates/sebi-new-algotrading-regulations-for-retail-investors-2026)
