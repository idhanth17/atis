# ATIS Security & Safety Design

Money systems fail in two ways: someone steals from you, or your own code trades you into a hole. This document covers both. **Nothing in Phase 4 (live trading) starts until every checklist item here is implemented and drilled.**

---

## 1. Threat model

| # | Threat | Vector | Impact |
|---|---|---|---|
| T1 | Credential theft | API key/token in git history, logs, `.env` synced to cloud, malware on dev machine | Attacker trades/withdraws via your broker account |
| T2 | Runaway algorithm | Bug in signal/order loop, retry storm, stale-data feedback loop | Rapid capital loss; SEBI OPS breach; broker penalty |
| T3 | Supply-chain attack | Typosquatted or compromised pip package (finance packages are actively targeted) | Key exfiltration, order tampering |
| T4 | Data poisoning / injection | Scraped news/RSS/filings contain adversarial text; if an LLM ever processes it, embedded instructions | Manipulated sentiment → bad trades; LLM tool abuse |
| T5 | Silent simulator fraud (self-inflicted) | Look-ahead bias, free fills, missing costs | False confidence → real losses at go-live |
| T6 | Dashboard exposure | Web UI bound to 0.0.0.0, no auth, port-forwarded | Anyone on network can see positions or trigger actions |
| T7 | State divergence | Local DB disagrees with broker (missed fill, partial fill, manual trade) | Doubled positions, unhedged exposure |
| T8 | Machine/process death mid-session | Power loss, crash, network outage with open MIS positions | Positions unmanaged until broker auto-square-off |

## 2. Secrets & credentials (T1)

- **No secret ever in code, git, or logs.** `.env` is gitignored from commit #1; `detect-secrets`/`gitleaks` runs in pre-commit **and** CI.
- Store the Kite `api_secret` and daily `access_token` in the **OS keyring** (Windows Credential Manager / `keyring` lib), not plaintext files. `.env` holds only non-secret config.
- **Kite specifics (Phase 4):** access tokens expire daily — this is a feature; a stolen token dies in < 24 h. Do the login redirect manually each morning (or semi-automated with *your own* TOTP, never a stored password). Enable full 2FA on the Zerodha account itself.
- **Least privilege / blast-radius cap:** the trading account holds **only** the allocated capital (₹1k–₹10k). No linked bank auto-sweep. Withdrawal to bank requires Zerodha's own 2FA path, which the API cannot perform — keep it that way.
- Log scrubbing: a logging filter redacts anything matching token/key patterns before write. Test it.
- Rotation drill: documented, practiced procedure to revoke the API key from the Kite dashboard in < 2 minutes.

## 3. Runaway-algorithm containment (T2) — the layered kill chain

Defense in depth; each layer works even if the layers above are buggy:

1. **Pre-trade checks (Risk Manager, vetoes every order):**
   - max risk per trade ≤ 1.0% of capital (tighter than v1's 1.5% — small accounts can't average their way out)
   - max 3 simultaneous positions; no instrument > 30% of capital
   - price sanity: reject order if limit price deviates > 3% from last quote, or quote is stale > 60 s
   - market-hours + trading-calendar check; no entries after 15:00
   - duplicate-order guard: idempotent `client_order_id` per signal; a signal can spawn at most one order, ever
2. **Rate limiter (also SEBI compliance):** hard token bucket at **≤ 2 orders/second, ≤ 25 orders/day** — far under SEBI's 10 OPS retail threshold. Counter persists in SQLite so a crash-restart can't reset it.
3. **Circuit breakers (halt all new orders for the day):**
   - daily loss ≥ 3% of capital
   - 3 consecutive losing trades
   - any reconciliation mismatch (see §7)
   - any provider serving stale data while positions are open
   - error-rate breaker: > 5 order rejections or API errors in 5 minutes
4. **Kill switch:** a file (`KILL`) and a dashboard button. When present: cancel all open orders, square off all positions with limit-then-market escalation, refuse to start. Checked at the top of *every* order path. **Drill it monthly in paper, once in live with a tiny position.**
5. **Dead-man's switch (T8):** the orchestrator writes a heartbeat every 30 s. An independent watchdog process (separate PID, could even be a separate cron) that sees a stale heartbeat while positions are open triggers square-off. Ultimate backstop: all positions are MIS, so the broker force-squares at ~15:20 even if your machine is off — never use CNC/NRML product types in the automated path.
6. **Human backstop:** phone alert (ntfy/Telegram bot with a *send-only* token) on every circuit-breaker trip, kill-switch activation, or reconciliation failure.

## 4. Supply chain (T3)

- `uv` (or pip-tools) with a **lockfile and `--require-hashes`** installs; no floating versions.
- Adding a dependency requires: exact-name check on PyPI (typosquats: `kiteconnect` vs `kite-connect` style), > 1 year history or personal source review, `pip-audit` clean.
- CI runs `pip-audit` weekly; Dependabot/renovate alerts on.
- The live-trading host runs **only** ATIS: no dev experiments, browser extensions, or random notebooks on the same Python environment. A dedicated venv at minimum; a dedicated user account is better.

## 5. Untrusted-input handling (T4)

All scraped content (RSS, news pages, NSE/BSE filings, any social feed) is **data, never instructions**:

- Parse defensively: strip HTML/JS, length-cap, whitelist encodings; never `eval`/template-render scraped text.
- FinBERT is a classifier — safe by construction. If an LLM is ever added for filings analysis, its output is a *feature* (a score with confidence), **never a tool call or order trigger**; prompt-injected text can at worst skew one feature, which the ensemble and risk layer bound.
- Source-credibility weights are config, not learned from the feed itself (prevents a spam source from promoting itself).
- Anomaly guard: a sudden sentiment spike from a single source cannot move the aggregate more than a capped amount.

## 6. Dashboard & host (T6)

- FastAPI binds `127.0.0.1` only. Remote access, if ever needed, via Tailscale/WireGuard — never a port-forward, never "just 0.0.0.0 for a minute".
- Any mutating endpoint (kill switch, config change) requires an auth token even on localhost (browser-borne CSRF from a random webpage you visit is a real vector: use a header token, not a cookie).
- Host hygiene: disk encryption on (BitLocker), OS auto-updates, no RDP exposed, separate OS user for the live process in Phase 4.
- Config changes are audited: who/when/old→new written to the append-only log.

## 7. State reconciliation (T7)

- Every 60 s while positions are open (Phase 4): fetch broker positions/orders and diff against local state. **Any mismatch → circuit breaker + alert.** Never "auto-correct" silently — a mismatch means either a bug or something worse; a human looks first.
- Orders use a client order ID and a strict state machine: `NEW → SENT → ACK → PARTIAL → FILLED/REJECTED/CANCELLED`. Unknown transitions halt trading.
- Nightly: reconcile the day's ledger against the broker contract note (paper mode reconciles against the simulator's own ledger — the *habit* is the point).
- SQLite in WAL mode, nightly backup of the DB; the audit table is append-only (no UPDATE/DELETE grants in code paths; enforced by a trigger).

## 8. Simulator integrity (T5)

Self-deception is the most expensive attack. Mandatory controls:

- Event-driven backtest/paper/live share one code path; strategies receive data only through an interface that physically cannot serve future bars.
- `FeatureSpec` declares every feature's availability timestamp (e.g., FII/DII = T+1 08:00); the feature builder refuses earlier access.
- Costs and slippage per [PAPER_TRADING_ENGINE.md](PAPER_TRADING_ENGINE.md) are **on by default and cannot be disabled in reported results** — a "frictionless" run is allowed only with an explicit `--fantasy` flag that watermarks the output.
- A "leak canary" test suite: strategies with deliberately injected look-ahead must produce implausible Sharpe and be flagged by the leakage detector; if the canary passes quietly, CI fails.

## 9. Go-live security checklist (Phase 4 entry gate)

Every box checked, in writing, before the first real order:

- [ ] Secrets in keyring; gitleaks clean on full history; log-redaction test passes
- [ ] Zerodha account: 2FA on, only allocated capital in it, API key scopes reviewed
- [ ] SEBI compliance: static IP arranged and whitelisted with broker; broker's algo/API terms accepted; OPS limiter tested under a simulated order storm
- [ ] Rate limiter, circuit breakers, kill switch, dead-man watchdog: each **drilled**, not just unit-tested
- [ ] Reconciliation loop tested against a manually placed out-of-band order (it must catch it)
- [ ] Restore-from-backup drill done; power-loss-mid-position tabletop done (answer: MIS auto-square + watchdog + phone alert)
- [ ] 20+ consecutive clean unattended paper days immediately preceding go-live
- [ ] Written run-book: morning login, kill procedure, key-revocation procedure, "market went crazy" procedure
