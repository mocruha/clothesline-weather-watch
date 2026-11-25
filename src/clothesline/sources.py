"""Fetch + parse das 3 fontes abertas. Parse é puro (testável com fixture)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

USER_AGENT = (
    "clothesline-weather-watch/0.1 github.com/mocruha/clothesline-weather-watch"
)
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
MET_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
CPTEC_URL = "https://brasilapi.com.br/api/cptec/v1/clima/previsao/{code}/4"
TIMEOUT_S = 20.0
RETRY_BACKOFF_S = (1.0, 3.0)

OPEN_METEO_HOURLY = (
    "temperature_2m,relative_humidity_2m,dew_point_2m,precipitation_probability,"
    "precipitation,wind_speed_10m,wind_gusts_10m,uv_index,shortwave_radiation,"
    "cloud_cover,vapour_pressure_deficit,et0_fao_evapotranspiration,is_day"
)

# Faixas físicas: fora delas o campo é descartado com warning (nunca vira zero).
RANGES = {
    "temperature_c": (-20.0, 55.0),
    "humidity_pct": (0.0, 100.0),
    "precip_mm": (0.0, 200.0),
    "precip_prob_pct": (0.0, 100.0),
    "wind_kmh": (0.0, 300.0),
    "gust_kmh": (0.0, 300.0),
    "uv_index": (0.0, 25.0),
    "cloud_pct": (0.0, 100.0),
    "vpd_kpa": (0.0, 10.0),
    "et0_mm": (0.0, 5.0),
    "radiation_wm2": (0.0, 1400.0),
}


class SourceError(RuntimeError):
    """Fonte indisponível ou resposta fora do contrato."""


@dataclass(slots=True)
class HourlyPoint:
    source: str
    hour: datetime  # tz-aware, UTC
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
    radiation_wm2: float | None = None
    symbol: str | None = None


@dataclass(slots=True)
class DailyPoint:
    city_code: int
    day: Any  # datetime.date
    condicao: str | None = None
    condicao_desc: str | None = None
    tmin_c: float | None = None
    tmax_c: float | None = None
    indice_uv: float | None = None


def _num(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _ranged(raw: Any, field: str, ctx: str) -> float | None:
    value = _num(raw)
    if value is None:
        return None
    lo, hi = RANGES[field]
    if not lo <= value <= hi:
        log.warning("%s: %s=%s fora de [%s, %s], descartado", ctx, field, value, lo, hi)
        return None
    return value


def _get(url: str, *, source: str, params: dict[str, Any] | None = None) -> Any:
    headers = {"User-Agent": USER_AGENT}
    attempts = len(RETRY_BACKOFF_S) + 1
    for attempt in range(attempts):
        retry = attempt < len(RETRY_BACKOFF_S)
        try:
            resp = httpx.get(
                url, params=params, headers=headers, timeout=TIMEOUT_S, follow_redirects=True
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            problem: Exception = exc
        else:
            if resp.status_code >= 500:
                problem = SourceError(f"HTTP {resp.status_code}")
            elif resp.status_code >= 400:
                raise SourceError(f"{source}: HTTP {resp.status_code} {resp.text[:200]}")
            else:
                try:
                    return resp.json()
                except ValueError as exc:
                    problem = SourceError(f"JSON inválido: {exc}")
        if retry:
            log.warning("%s: %s — retry em %ss", source, problem, RETRY_BACKOFF_S[attempt])
            time.sleep(RETRY_BACKOFF_S[attempt])
        else:
            raise SourceError(f"{source}: falhou após {attempts} tentativas ({problem})")
    raise AssertionError("inalcançável")


def _parse_local(ts: str, tz: ZoneInfo) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def parse_open_meteo(payload: dict[str, Any]) -> list[HourlyPoint]:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        raise SourceError("open_meteo: resposta sem hourly.time")
    tz = ZoneInfo(payload.get("timezone") or "UTC")

    def series(name: str) -> list[Any]:
        values = hourly.get(name) or []
        return list(values) + [None] * (len(times) - len(values))

    cols = {name: series(name) for name in hourly if name != "time"}
    points: list[HourlyPoint] = []
    for idx, ts in enumerate(times):
        ctx = f"open_meteo {ts}"
        et0 = _ranged(_at(cols, "et0_fao_evapotranspiration", idx), "et0_mm", ctx)
        point = HourlyPoint(
            source="open_meteo",
            hour=_parse_local(ts, tz),
            temperature_c=_ranged(_at(cols, "temperature_2m", idx), "temperature_c", ctx),
            humidity_pct=_ranged(_at(cols, "relative_humidity_2m", idx), "humidity_pct", ctx),
            precip_mm=_ranged(_at(cols, "precipitation", idx), "precip_mm", ctx),
            precip_prob_pct=_ranged(
                _at(cols, "precipitation_probability", idx), "precip_prob_pct", ctx
            ),
            wind_kmh=_ranged(_at(cols, "wind_speed_10m", idx), "wind_kmh", ctx),
            gust_kmh=_ranged(_at(cols, "wind_gusts_10m", idx), "gust_kmh", ctx),
            uv_index=_ranged(_at(cols, "uv_index", idx), "uv_index", ctx),
            cloud_pct=_ranged(_at(cols, "cloud_cover", idx), "cloud_pct", ctx),
            vpd_kpa=_ranged(_at(cols, "vapour_pressure_deficit", idx), "vpd_kpa", ctx),
            et0_mm=et0,
            radiation_wm2=_ranged(
                _at(cols, "shortwave_radiation", idx), "radiation_wm2", ctx
            ),
        )
        if point.temperature_c is None:
            log.warning("%s: sem temperatura, linha descartada", ctx)
            continue
        points.append(point)
    if not points:
        raise SourceError("open_meteo: nenhuma hora utilizável na resposta")
    return points


def _at(cols: dict[str, list[Any]], name: str, idx: int) -> Any:
    values = cols.get(name)
    return values[idx] if values is not None and idx < len(values) else None


def parse_met(payload: dict[str, Any]) -> list[HourlyPoint]:
    series = (payload.get("properties") or {}).get("timeseries") or []
    if not series:
        raise SourceError("met_no: resposta sem properties.timeseries")
    points: list[HourlyPoint] = []
    for entry in series:
        ts = entry.get("time")
        details = ((entry.get("data") or {}).get("instant") or {}).get("details") or {}
        if not ts:
            continue
        ctx = f"met_no {ts}"
        one = ((entry.get("data") or {}).get("next_1_hours") or {})
        six = ((entry.get("data") or {}).get("next_6_hours") or {})
        amount = _num((one.get("details") or {}).get("precipitation_amount"))
        if amount is None:
            # horas distantes só trazem next_6_hours: rateio linear é o melhor
            # palpite disponível e evita buraco na série de consenso
            six_amount = _num((six.get("details") or {}).get("precipitation_amount"))
            amount = six_amount / 6 if six_amount is not None else None
        wind_ms = _num(details.get("wind_speed"))
        gust_ms = _num(details.get("wind_speed_of_gust"))
        point = HourlyPoint(
            source="met_no",
            hour=_parse_local(ts, timezone.utc),
            temperature_c=_ranged(details.get("air_temperature"), "temperature_c", ctx),
            humidity_pct=_ranged(details.get("relative_humidity"), "humidity_pct", ctx),
            precip_mm=_ranged(amount, "precip_mm", ctx),
            wind_kmh=_ranged(wind_ms * 3.6 if wind_ms is not None else None, "wind_kmh", ctx),
            gust_kmh=_ranged(gust_ms * 3.6 if gust_ms is not None else None, "gust_kmh", ctx),
            cloud_pct=_ranged(
                details.get("cloud_area_fraction"), "cloud_pct", ctx
            ),
            symbol=(one.get("summary") or {}).get("symbol_code"),
        )
        if point.temperature_c is None:
            log.warning("%s: sem temperatura, linha descartada", ctx)
            continue
        points.append(point)
    if not points:
        raise SourceError("met_no: nenhuma hora utilizável na resposta")
    return points


def parse_cptec(payload: dict[str, Any]) -> list[DailyPoint]:
    rows = payload.get("clima") or []
    if not rows:
        raise SourceError("cptec: resposta sem clima[]")
    city = payload.get("cidade")
    points: list[DailyPoint] = []
    for row in rows:
        day_raw = row.get("data")
        if not day_raw:
            continue
        day = datetime.strptime(str(day_raw)[:10], "%Y-%m-%d").date()
        tmin = _num(row.get("min"))
        tmax = _num(row.get("max"))
        if tmin is not None and not -20 <= tmin <= 55:
            log.warning("cptec %s %s: min=%s fora de faixa, descartado", city, day, tmin)
            tmin = None
        if tmax is not None and not -20 <= tmax <= 55:
            log.warning("cptec %s %s: max=%s fora de faixa, descartado", city, day, tmax)
            tmax = None
        points.append(
            DailyPoint(
                city_code=0,  # preenchido pelo caller com config.location.cptec_city_code
                day=day,
                condicao=(row.get("condicao") or None),
                condicao_desc=(row.get("condicao_desc") or None),
                tmin_c=tmin,
                tmax_c=tmax,
                indice_uv=_num(row.get("indice_uv")),
            )
        )
    if not points:
        raise SourceError("cptec: nenhum dia utilizável na resposta")
    return points


def fetch_open_meteo(lat: float, lon: float, tz_name: str) -> list[HourlyPoint]:
    payload = _get(
        OPEN_METEO_URL,
        source="open_meteo",
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": OPEN_METEO_HOURLY,
            "forecast_days": 2,
            "past_days": 1,
            "timezone": tz_name,
        },
    )
    return parse_open_meteo(payload)


def fetch_met(lat: float, lon: float) -> list[HourlyPoint]:
    payload = _get(MET_URL, source="met_no", params={"lat": lat, "lon": lon})
    return parse_met(payload)


def fetch_cptec(city_code: int) -> list[DailyPoint]:
    payload = _get(CPTEC_URL.format(code=city_code), source="cptec")
    points = parse_cptec(payload)
    for point in points:
        point.city_code = city_code
    return points


def fetch_all(lat: float, lon: float, tz_name: str, city_code: int) -> tuple[
    list[HourlyPoint], list[DailyPoint], list[str]
]:
    """Cada fonte falha sozinha; só propaga erro se nenhuma fonte horária responder."""
    hourly: list[HourlyPoint] = []
    daily: list[DailyPoint] = []
    failures: list[str] = []
    for label, fn in (
        ("open_meteo", lambda: fetch_open_meteo(lat, lon, tz_name)),
        ("met_no", lambda: fetch_met(lat, lon)),
        ("cptec", lambda: fetch_cptec(city_code)),
    ):
        try:
            result = fn()
        except SourceError as exc:
            log.warning("fonte %s indisponível: %s", label, exc)
            failures.append(label)
            continue
        if label == "cptec":
            daily.extend(result)  # type: ignore[arg-type]
        else:
            hourly.extend(result)  # type: ignore[arg-type]
    if not hourly:
        raise SourceError(f"nenhuma fonte horária respondeu (falhas: {', '.join(failures)})")
    return hourly, daily, failures


def dedupe(points: Iterable[HourlyPoint]) -> list[HourlyPoint]:
    """Mantém o último valor por (fonte, hora)."""
    latest: dict[tuple[str, datetime], HourlyPoint] = {}
    for point in points:
        latest[(point.source, point.hour)] = point
    return sorted(latest.values(), key=lambda p: (p.hour, p.source))