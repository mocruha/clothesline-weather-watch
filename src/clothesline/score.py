"""Modelo de decisão: hora boa, janelas de varal e score do dia. Funções puras."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from math import exp
from typing import Iterable, Sequence

from clothesline.config import Config, Thresholds
from clothesline.sources import DailyPoint, HourlyPoint

__all__ = [
    "Hour",
    "Window",
    "DayVerdict",
    "combine",
    "dry_rate",
    "good_hour",
    "vpd_from",
    "windows",
    "day_verdict",
    "hourly_rows",
]


@dataclass(slots=True)
class Hour:
    hour: datetime  # UTC
    local: datetime  # tz do local
    temperature_c: float | None = None
    humidity_pct: float | None = None
    precip_mm: float | None = None
    precip_prob_pct: float | None = None
    wind_kmh: float | None = None
    gust_kmh: float | None = None
    uv_index: float | None = None
    cloud_pct: float | None = None
    vpd_kpa: float | None = None
    et0_mm: float | None = None
    is_day: bool = True
    sources: tuple[str, ...] = ()


@dataclass(slots=True)
class Window:
    start: datetime
    end: datetime  # exclusivo: última hora boa + 1h
    kind: str  # 'day' | 'night'
    dry_eta_hours: float | None
    hang_by: datetime | None
    dry_at: datetime | None

    @property
    def hours(self) -> int:
        return int((self.end - self.start).total_seconds() // 3600)


@dataclass(slots=True)
class DayVerdict:
    day: date
    score: int
    label: str
    windows: list[Window]
    cptec_lock: bool
    cptec_desc: str | None
    cptec_period: str | None
    tmin_c: float | None
    tmax_c: float | None
    humidity_mean: float | None
    wind_max_kmh: float | None
    gust_max_kmh: float | None
    uv_max: float | None
    precip_prob_max: float | None
    precip_mm_max: float | None
    good_day_hours: int
    sources: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)


def vpd_from(temperature_c: float, humidity_pct: float) -> float:
    """Tetens: es em kPa, VPD = es * (1 - RH/100)."""
    es = 0.6108 * exp(17.27 * temperature_c / (temperature_c + 237.3))
    return max(0.0, es * (1 - humidity_pct / 100.0))


def _mean(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def combine(points: Iterable[HourlyPoint], cfg: Config) -> list[Hour]:
    """Uma Hour por timestamp: média simples das fontes disponíveis naquela hora."""
    th = cfg.thresholds
    groups: dict[datetime, list[HourlyPoint]] = {}
    for point in points:
        groups.setdefault(point.hour, []).append(point)
    hours: list[Hour] = []
    for stamp in sorted(groups):
        group = groups[stamp]
        temperature_c = _mean(p.temperature_c for p in group)
        humidity_pct = _mean(p.humidity_pct for p in group)
        vpd = _mean(p.vpd_kpa for p in group)
        if vpd is None and temperature_c is not None and humidity_pct is not None:
            vpd = vpd_from(temperature_c, humidity_pct)
        local = stamp.astimezone(cfg.location.tz)
        hours.append(
            Hour(
                hour=stamp,
                local=local,
                temperature_c=temperature_c,
                humidity_pct=humidity_pct,
                precip_mm=_mean(p.precip_mm for p in group),
                precip_prob_pct=_mean(p.precip_prob_pct for p in group),
                wind_kmh=_mean(p.wind_kmh for p in group),
                gust_kmh=_mean(p.gust_kmh for p in group),
                uv_index=_mean(p.uv_index for p in group),
                cloud_pct=_mean(p.cloud_pct for p in group),
                vpd_kpa=vpd,
                et0_mm=_mean(p.et0_mm for p in group),
                is_day=th.day_start_hour <= local.hour < th.day_end_hour,
                sources=tuple(sorted(p.source for p in group)),
            )
        )
    return hours


def precip_prob(hour: Hour, th: Thresholds) -> float:
    """Sem probabilidade da fonte, deriva da quantidade — nunca assume 0%."""
    if hour.precip_prob_pct is not None:
        return hour.precip_prob_pct
    return 100.0 if (hour.precip_mm or 0.0) >= th.precip_mm_max else 0.0


def dry_rate(hour: Hour, th: Thresholds) -> float:
    """mm/h de água removida: ET0 Penman-Monteith, ou fórmula de reserva."""
    if hour.et0_mm is not None:
        return hour.et0_mm
    vpd = hour.vpd_kpa or 0.0
    wind = hour.wind_kmh or 0.0
    return min(th.fallback_dry_rate_cap, th.fallback_dry_rate_k * vpd * (0.5 + 0.06 * wind))


def good_hour(hour: Hour, th: Thresholds) -> bool:
    if (hour.precip_mm or 0.0) >= th.precip_mm_max:
        return False
    if precip_prob(hour, th) >= th.precip_prob_max_pct:
        return False
    if (hour.gust_kmh or 0.0) >= th.gust_max_kmh:
        return False
    vpd = hour.vpd_kpa or 0.0
    return vpd >= (th.vpd_min_kpa if hour.is_day else th.vpd_night_min_kpa)


def _runs(hours: Sequence[Hour]) -> list[list[Hour]]:
    runs: list[list[Hour]] = []
    current: list[Hour] = []
    for hour in hours:
        if current and hour.hour - current[-1].hour != timedelta(hours=1):
            runs.append(current)
            current = []
        current.append(hour)
    if current:
        runs.append(current)
    return runs


def _dry_eta(run: Sequence[Hour], th: Thresholds) -> float | None:
    """Horas acumuladas até evaporar water_retained_mm; None se a janela não basta."""
    accumulated = 0.0
    for index, hour in enumerate(run):
        rate = dry_rate(hour, th)
        if rate > 0 and accumulated + rate >= th.water_retained_mm:
            return index + (th.water_retained_mm - accumulated) / rate
        accumulated += rate
    return None


def windows(hours: Sequence[Hour], th: Thresholds) -> list[Window]:
    good = [h for h in sorted(hours, key=lambda h: h.hour) if good_hour(h, th)]
    result: list[Window] = []
    for run in _runs(good):
        day_hours = sum(1 for h in run if h.is_day)
        night_hours = len(run) - day_hours
        if day_hours < th.day_window_min_hours and night_hours < th.night_window_min_hours:
            continue
        start = run[0].local
        end = run[-1].local + timedelta(hours=1)
        eta = _dry_eta(run, th)
        hang_by = dry_at = None
        if eta is not None:
            dry_at = _round_hour(start + timedelta(hours=eta))
            hang_by = _round_hour(max(start, end - timedelta(hours=eta)))
        result.append(
            Window(
                start=start,
                end=end,
                kind="night" if night_hours > day_hours else "day",
                dry_eta_hours=round(eta, 1) if eta is not None else None,
                hang_by=hang_by,
                dry_at=dry_at,
            )
        )
    return result


def _round_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0) + (
        timedelta(hours=1) if value.minute >= 30 else timedelta(0)
    )


def cptec_period(code: str | None, cfg: Config) -> tuple[bool, str | None]:
    """(trava de chuva, período do dia da chuva prevista)."""
    if not code:
        return False, None
    code = code.strip().lower()
    rules = cfg.cptec
    if code not in rules.rain_codes:
        return False, None
    if code in rules.morning_codes:
        return True, "manhã"
    if code in rules.afternoon_codes:
        return True, "tarde"
    if code in rules.night_codes:
        return True, "noite"
    return True, None


def _period_start_hour(period: str | None, cfg: Config) -> int | None:
    rules = cfg.cptec
    if period == "manhã":
        return cfg.thresholds.day_start_hour
    if period == "tarde":
        return rules.afternoon_start_hour
    if period == "noite":
        return cfg.thresholds.day_end_hour
    return None


def day_verdict(
    hours: Sequence[Hour],
    day: date,
    cfg: Config,
    cptec: DailyPoint | None = None,
) -> DayVerdict:
    th = cfg.thresholds
    day_hours = [h for h in hours if h.local.date() == day]
    if not day_hours:
        raise ValueError(f"sem horas para {day}")

    lock, period = cptec_period(cptec.condicao if cptec else None, cfg)

    # janelas: o dia todo + a madrugada/manhã seguinte (caso "penduro 20h,
    # recolho 12h"), cortando no overnight_carry_hour
    day_start_local = datetime.combine(day, datetime.min.time(), tzinfo=cfg.location.tz)
    horizon_end = day_start_local + timedelta(days=1, hours=th.overnight_carry_hour)
    window_hours = [h for h in hours if day_start_local <= h.local < horizon_end]
    found = [w for w in windows(window_hours, th) if w.start.date() == day]

    if lock and period:
        limit = _period_start_hour(period, cfg)
        if limit is not None:
            kept = [
                w
                for w in found
                if w.end.date() == day and w.end.hour <= limit and w.hours >= 3
            ]
            found = kept

    good_day = sum(1 for h in day_hours if h.is_day and good_hour(h, th))
    score = min(100.0, 100.0 * good_day / th.dry_day_hours_target)
    if lock:
        score -= th.cptec_lock_penalty
    gust_max = _mean_max(h.gust_kmh for h in day_hours)
    if gust_max is not None and gust_max >= th.gust_max_kmh:
        score -= th.gust_penalty
    score_i = int(round(min(100.0, max(0.0, score))))
    label = (
        "bom dia de varal"
        if score_i >= th.label_good_min
        else "dá, com janela curta"
        if score_i >= th.label_short_min
        else "melhor não / secadora"
    )

    notes: list[str] = []
    if lock and period and not found:
        notes.append(f"CPTEC indica chuva pela {period} e nenhuma janela de 3h+ cabe antes")
    if gust_max is not None and gust_max >= th.gust_max_kmh:
        notes.append(f"rajadas de {gust_max:.0f} km/h atrapalham o varal")

    return DayVerdict(
        day=day,
        score=score_i,
        label=label,
        windows=found,
        cptec_lock=lock,
        cptec_desc=(cptec.condicao_desc or cptec.condicao) if cptec else None,
        cptec_period=period,
        tmin_c=_mean_min(h.temperature_c for h in day_hours),
        tmax_c=_mean_max(h.temperature_c for h in day_hours),
        humidity_mean=_mean(h.humidity_pct for h in day_hours),
        wind_max_kmh=_mean_max(h.wind_kmh for h in day_hours),
        gust_max_kmh=gust_max,
        uv_max=_mean_max(h.uv_index for h in day_hours),
        precip_prob_max=_mean_max(precip_prob(h, th) for h in day_hours),
        precip_mm_max=_mean_max(h.precip_mm for h in day_hours),
        good_day_hours=good_day,
        sources=tuple(sorted({s for h in day_hours for s in h.sources})),
        notes=notes,
    )


def _mean_max(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _mean_min(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


def hourly_rows(hours: Sequence[Hour], th: Thresholds) -> list[dict]:
    """Linhas para hourly_score; features jsonb é o que viabiliza ML futuro."""
    rows = []
    for h in hours:
        rate = dry_rate(h, th)
        rows.append(
            {
                "hour": h.hour,
                "good": good_hour(h, th),
                "dry_rate_mm_h": rate,
                "vpd_kpa": h.vpd_kpa,
                "precip_prob_pct": precip_prob(h, th),
                "features": {
                    "local_hour": h.local.isoformat(),
                    "is_day": h.is_day,
                    "sources": list(h.sources),
                    "temperature_c": h.temperature_c,
                    "humidity_pct": h.humidity_pct,
                    "precip_mm": h.precip_mm,
                    "precip_prob_pct": h.precip_prob_pct,
                    "wind_kmh": h.wind_kmh,
                    "gust_kmh": h.gust_kmh,
                    "uv_index": h.uv_index,
                    "cloud_pct": h.cloud_pct,
                    "vpd_kpa": h.vpd_kpa,
                    "et0_mm": h.et0_mm,
                },
            }
        )
    return rows