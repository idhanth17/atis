"""Fallback quotes via yfinance — DELAYED (~15 min) and proud of it.

The asof timestamp comes from the last 1-minute bar Yahoo serves, so the
delay is visible to everything downstream instead of hidden. Acceptable for
recording and for paper trading only because the simulator sees the same
delayed clock (docs/PAPER_TRADING_ENGINE.md §2).
"""

from __future__ import annotations


from atis.mktcalendar import IST
from atis.models import Kind, Quote


class YFinanceDelayedProvider:
    name = "yfinance_delayed"
    delayed = True

    def __init__(self, suffix: str = ".NS"):
        self.suffix = suffix

    def live_quote(self, symbol: str) -> Quote | None:
        from datetime import datetime

        try:
            import yfinance as yf

            hist = yf.Ticker(symbol + self.suffix).history(
                period="1d", interval="1m", auto_adjust=False
            )
            if hist.empty:
                return None
            ts = hist.index[-1].to_pydatetime().astimezone(IST)
            last = float(hist["Close"].iloc[-1])
        except Exception:
            return None
        if last <= 0:
            return None
        return Quote(symbol=symbol, last=last, asof=ts,
                     received_at=datetime.now(IST), kind=Kind.EQUITY)

    def intraday_bars(self, symbol: str, period: str = "1d") -> list[dict]:
        """Full 1-minute bars for the most recent session(s). Yahoo keeps
        ~7 days of 1m history — the EOD archiver must run daily; missed days
        beyond that window are gone. Rows: {ts, open, high, low, close, volume}."""
        try:
            import yfinance as yf

            hist = yf.Ticker(symbol + self.suffix).history(
                period=period, interval="1m", auto_adjust=False
            )
        except Exception:
            return []
        out = []
        for ts, row in hist.iterrows():
            t = ts.to_pydatetime().astimezone(IST)
            out.append({
                "ts": t.isoformat(),
                "open": float(row["Open"]), "high": float(row["High"]),
                "low": float(row["Low"]), "close": float(row["Close"]),
                "volume": int(row["Volume"] or 0),
            })
        return out
