"""NSE bhavcopy (daily EOD file) downloader + loader.

The bhavcopy is the canonical free source for daily OHLCV. Raw zips are
archived immutably under data/raw/bhavcopy/ before parsing — if a parse bug
is found later, history can be rebuilt without refetching.

Polite client: one request per trading day, throttled, honest User-Agent.
Requires the 'data' extra: uv sync --extra data
"""

from __future__ import annotations

import csv
import io
import sqlite3
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from atis.mktcalendar import NSECalendar

# NSE switched bhavcopy formats in 2024: UDiFF for ~Jan 2024 onward,
# the legacy EQUITIES archive for older dates. Try UDiFF first, fall back.
URL_UDIFF = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
)
URL_LEGACY = (
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/"
    "{year}/{mon}/cm{day:02d}{mon}{year}bhav.csv.zip"
)
THROTTLE_SECONDS = 2.0
# Column mappings per format; the loader sniffs the header to pick one
COLS_UDIFF = {
    "symbol": "TckrSymb", "series": "SctySrs", "open": "OpnPric",
    "high": "HghPric", "low": "LwPric", "close": "ClsPric",
    "volume": "TtlTradgVol",
}
COLS_LEGACY = {
    "symbol": "SYMBOL", "series": "SERIES", "open": "OPEN",
    "high": "HIGH", "low": "LOW", "close": "CLOSE",
    "volume": "TOTTRDQTY",
}


class BhavcopyProvider:
    name = "nse_bhavcopy"

    def __init__(self, conn: sqlite3.Connection, raw_dir: Path):
        self._conn = conn
        self._raw_dir = raw_dir
        self._raw_dir.mkdir(parents=True, exist_ok=True)

    def _fetch_day(self, d: date) -> Path | None:
        """Download one day's zip to the archive (skip if already present).
        Tries the UDiFF endpoint first, then the legacy archive."""
        import requests  # deferred: 'data' extra

        out = self._raw_dir / f"bhavcopy_{d.isoformat()}.csv.zip"
        if out.exists():
            return out
        mon = d.strftime("%b").upper()
        udiff = URL_UDIFF.format(ymd=d.strftime("%Y%m%d"))
        legacy = URL_LEGACY.format(year=d.year, mon=mon, day=d.day)
        # UDiFF exists from ~Jan 2024; order by likelihood to avoid wasted 404s
        urls = [udiff, legacy] if d.year >= 2024 else [legacy, udiff]
        for url in urls:
            resp = requests.get(
                url,
                headers={"User-Agent": "ATIS-research/0.1 (personal research use)"},
                timeout=30,
            )
            time.sleep(THROTTLE_SECONDS)
            if resp.status_code == 200:
                tmp = out.with_suffix(".part")
                tmp.write_bytes(resp.content)
                tmp.rename(out)  # archive is atomic: no half-written files
                return out
        return None

    def _load_day(self, d: date, zip_path: Path, series: str = "EQ") -> int:
        with zipfile.ZipFile(zip_path) as zf:
            csv_name = zf.namelist()[0]
            text = zf.read(csv_name).decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        fields = [f.strip() for f in (reader.fieldnames or [])]
        cols = COLS_UDIFF if COLS_UDIFF["symbol"] in fields else COLS_LEGACY
        fetched_at = datetime.now(timezone.utc).isoformat()
        n = 0
        for row in reader:
            row = {(k or "").strip(): v for k, v in row.items()}
            if (row.get(cols["series"]) or "").strip() != series:
                continue
            self._conn.execute(
                "INSERT OR REPLACE INTO ohlcv_daily "
                "(symbol, trade_date, open, high, low, close, volume, source, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    row[cols["symbol"]].strip(),
                    d.isoformat(),
                    float(row[cols["open"]]),
                    float(row[cols["high"]]),
                    float(row[cols["low"]]),
                    float(row[cols["close"]]),
                    int(float(row[cols["volume"]] or 0)),
                    self.name,
                    fetched_at,
                ),
            )
            n += 1
        self._conn.commit()
        return n

    def sync_range(self, start: date, end: date, calendar: NSECalendar) -> dict:
        """Fetch + load every trading day in [start, end]. Returns a summary."""
        summary = {"days_loaded": 0, "rows": 0, "missing": []}
        d = start
        while d <= end:
            try:
                trading = calendar.is_trading_day(d)
            except Exception:
                trading = d.weekday() < 5  # historical years outside holiday file: weekday heuristic
            if trading:
                zip_path = self._fetch_day(d)
                if zip_path is None:
                    summary["missing"].append(d.isoformat())
                else:
                    summary["rows"] += self._load_day(d, zip_path)
                    summary["days_loaded"] += 1
            d += timedelta(days=1)
        return summary
