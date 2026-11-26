"""Config do projeto: tudo que é limiar vem de config.toml, nada hardcoded."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Location:
    name: str
    state: str
    latitude: float
    longitude: float
    cptec_city_code: int
    timezone: str

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True)
class Thresholds:
    precip_mm_max: float
    precip_prob_max_pct: float
    vpd_min_kpa: float
    vpd_night_min_kpa: float
    gust_max_kmh: float
    day_start_hour: int
    day_end_hour: int
    day_window_min_hours: int
    night_window_min_hours: int
    dry_day_hours_target: float
    water_retained_mm: float
    fallback_dry_rate_k: float
    fallback_dry_rate_cap: float
    overnight_carry_hour: int
    cptec_lock_penalty: float
    gust_penalty: float
    label_good_min: float
    label_short_min: float
    rain_alert_prob_pct: float
    rain_alert_mm: float
    rain_alert_horizon_hours: int
    rain_alert_shift_hours: int


@dataclass(frozen=True)
class CptecRules:
    rain_codes: tuple[str, ...]
    morning_codes: tuple[str, ...]
    afternoon_codes: tuple[str, ...]
    night_codes: tuple[str, ...]
    afternoon_start_hour: int


@dataclass(frozen=True)
class ReportWindow:
    start_hour: int
    end_hour: int
    evening_start_hour: int
    evening_end_hour: int


@dataclass(frozen=True)
class Config:
    location: Location
    thresholds: Thresholds
    cptec: CptecRules
    report: ReportWindow
    retention_days: int


def config_path() -> Path:
    override = os.environ.get("CLOTHESLINE_CONFIG")
    if override:
        return Path(override)
    for candidate in (Path.cwd() / "config.toml", REPO_ROOT / "config.toml"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "config.toml não encontrado (defina CLOTHESLINE_CONFIG ou rode da raiz do repo)"
    )


def load_config(path: Path | None = None) -> Config:
    with open(path or config_path(), "rb") as fh:
        raw = tomllib.load(fh)
    cptec_raw = raw["cptec"]
    return Config(
        location=Location(**raw["location"]),
        thresholds=Thresholds(**raw["thresholds"]),
        cptec=CptecRules(
            rain_codes=tuple(cptec_raw["rain_codes"]),
            morning_codes=tuple(cptec_raw["morning_codes"]),
            afternoon_codes=tuple(cptec_raw["afternoon_codes"]),
            night_codes=tuple(cptec_raw["night_codes"]),
            afternoon_start_hour=int(cptec_raw["afternoon_start_hour"]),
        ),
        report=ReportWindow(**raw["report"]),
        retention_days=int(raw["retention"]["forecast_days"]),
    )