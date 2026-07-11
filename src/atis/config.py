"""Configuration: env settings + YAML config files, validated with pydantic.

Secrets never live here — non-secret config only (SECURITY.md §2).
"""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ATIS_", env_file=".env", extra="ignore")

    mode: str = "paper"                      # paper | backtest | live (live: Phase 4 only)
    db_path: Path = Path("data/atis.db")
    data_dir: Path = Path("data")
    config_dir: Path = Path("config")
    kill_file: Path = Path("KILL")


class RiskLimits(BaseModel):
    capital: float = Field(gt=0)
    max_risk_per_trade_pct: float = Field(gt=0, le=0.02)
    max_open_positions: int = Field(gt=0, le=5)
    max_instrument_concentration_pct: float = Field(gt=0, le=1)
    max_price_deviation_pct: float = Field(gt=0)
    max_quote_age_seconds: float = Field(gt=0)
    no_new_entries_after: time
    square_off_at: time
    daily_loss_limit_pct: float = Field(gt=0)
    max_consecutive_losses: int = Field(gt=0)
    error_rate_limit: int = Field(gt=0)
    error_rate_window_seconds: float = Field(gt=0)
    orders_per_second: int = Field(gt=0, le=10)   # SEBI retail threshold is 10 OPS — hard ceiling
    orders_per_day: int = Field(gt=0)


class SegmentCosts(BaseModel):
    stt_sell_pct: float
    exchange_txn_pct: float
    sebi_per_crore: float
    stamp_buy_pct: float
    gst_pct: float
    # equity uses pct+cap; options use flat
    brokerage_pct: float | None = None
    brokerage_cap: float | None = None
    brokerage_flat: float | None = None


class CostConfig(BaseModel):
    equity_intraday: SegmentCosts
    index_option: SegmentCosts


class PaperConfig(BaseModel):
    slippage_equity_pct: float
    slippage_option_pct: float
    spread_est_equity_pct: float
    spread_est_option_pct: float
    tick_size: float
    equity_mis_leverage: float = Field(gt=0, le=5.0)  # never above SEBI peak-margin reality
    stale_quote_seconds: float
    squareoff_penalty_multiplier: float = 2.0


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_risk(config_dir: Path) -> RiskLimits:
    return RiskLimits(**_load_yaml(config_dir / "risk.yaml"))


def load_costs(config_dir: Path) -> CostConfig:
    return CostConfig(**_load_yaml(config_dir / "costs.yaml"))


def load_paper(config_dir: Path) -> PaperConfig:
    return PaperConfig(**_load_yaml(config_dir / "paper.yaml"))


def load_holidays(config_dir: Path) -> dict[int, set[date]]:
    raw = _load_yaml(config_dir / "nse_holidays.yaml")
    out: dict[int, set[date]] = {}
    for year, days in raw["years"].items():
        out[int(year)] = {d if isinstance(d, date) else date.fromisoformat(str(d)) for d in days}
    return out
