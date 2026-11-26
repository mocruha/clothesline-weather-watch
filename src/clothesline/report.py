"""Rendering das mensagens PT-BR (relatório diário e alerta de chuva)."""

from __future__ import annotations

from datetime import datetime

from clothesline.config import Config
from clothesline.score import DayVerdict, Hour, Window, precip_prob

WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
SOURCE_LABELS = {
    "open_meteo": "Open-Meteo",
    "met_no": "MET Norway",
    "cptec": "CPTEC/INPE",
}


def _n(value: float | None, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "—"
    text = f"{value:.{digits}f}".replace(".", ",")
    return text + suffix


def _clock(value: datetime) -> str:
    return value.strftime("%Hh")


def _span(window: Window) -> str:
    return f"{_clock(window.start)}–{_clock(window.end)}"


def _best_windows(windows: list[Window], limit: int = 2) -> list[Window]:
    ordered = sorted(
        windows,
        key=lambda w: (w.kind == "night", -w.hours),
    )
    return ordered[:limit]


def _window_line(window: Window, th) -> str:
    head = "🌙 janela noturna" if window.kind == "night" else "🧺 janela"
    if window.dry_at is None:
        return f"{head} {_span(window)} · não seca dentro da janela"
    hang = (
        f"pendure {_clock(window.hang_by)}–{_clock(window.end)}"
        if window.hang_by and window.hang_by > window.start
        else f"pendure até {_clock(window.hang_by)}"
    )
    return f"{head} {_span(window)} · {hang}, seca ~{_clock(window.dry_at)}"


def _headline(verdict: DayVerdict, th) -> str:
    if verdict.cptec_lock or (verdict.precip_prob_max or 0) >= th.rain_alert_prob_pct:
        return "🌧️"
    if verdict.score >= th.label_good_min:
        return "☀️"
    return "🌦️"


def _disagreement(verdict: DayVerdict, th, failures: list[str]) -> str:
    if verdict.cptec_desc is None:
        if "cptec" in failures:
            return "⚖️ CPTEC/INPE indisponível neste run"
        return "⚖️ CPTEC não traz previsão para hoje"
    models_rain = (verdict.precip_prob_max or 0) >= th.rain_alert_prob_pct or (
        verdict.precip_mm_max or 0
    ) >= th.rain_alert_mm
    if verdict.cptec_lock and models_rain:
        return "⚖️ CPTEC e modelos horários concordam: chuva"
    if verdict.cptec_lock:
        return "⚖️ CPTEC prevê chuva; modelos horários mostram quase nada"
    if models_rain:
        return "⚖️ modelos horários apontam chuva; CPTEC não trava o dia"
    return "⚖️ CPTEC e modelos horários concordam: sem chuva"


def _tomorrow_block(tomorrow: DayVerdict | None, th) -> list[str]:
    if tomorrow is None:
        return ["🔮 Amanhã: sem dados dos modelos horários"]
    best = _best_windows(tomorrow.windows, limit=1)
    lines = [
        f"🔮 *Amanhã — {WEEKDAYS[tomorrow.day.weekday()]} {tomorrow.day:%d/%m}*",
        f"Score {tomorrow.score}/100 · {tomorrow.label}",
        f"🌡️ {_n(tomorrow.tmin_c)}–{_n(tomorrow.tmax_c)} °C · "
        f"🌧️ prob máx {_n(tomorrow.precip_prob_max)}% · "
        f"pico {_n(tomorrow.precip_mm_max, 1)} mm/h · "
        f"💨 rajada {_n(tomorrow.gust_max_kmh)} km/h",
        _window_line(best[0], th) if best else "🧺 nenhuma janela boa amanhã",
    ]
    if tomorrow.cptec_desc:
        period = f" ({tomorrow.cptec_period})" if tomorrow.cptec_period else ""
        lines.append(f"📊 CPTEC: {tomorrow.cptec_desc}{period}")
    return lines


def render_report(
    verdict: DayVerdict,
    cfg: Config,
    *,
    sources_ok: list[str],
    failures: list[str],
    tomorrow: DayVerdict | None = None,
    evening: bool = False,
    now_local: datetime | None = None,
) -> str:
    th = cfg.thresholds
    city = cfg.location.name
    when = f"{WEEKDAYS[verdict.day.weekday()]} {verdict.day:%d/%m}"
    lines = [
        f"{_headline(verdict, th)} *Varal em {city} — "
        f"{f'fim de tarde {when}' if evening else when}*",
        f"Score {verdict.score}/100 · {verdict.label}",
        "",
        f"🌡️ {_n(verdict.tmin_c)}–{_n(verdict.tmax_c)} °C · "
        f"💧 umidade {_n(verdict.humidity_mean)}% · "
        f"💨 vento {_n(verdict.wind_max_kmh)} km/h "
        f"(rajada {_n(verdict.gust_max_kmh)}) · "
        f"☀️ UV {_n(verdict.uv_max, 1)}",
        f"🌧️ chuva: prob máx {_n(verdict.precip_prob_max)}% · "
        f"pico {_n(verdict.precip_mm_max, 1)} mm/h",
        "",
    ]

    candidates = verdict.windows
    if evening and now_local is not None:
        candidates = [w for w in candidates if w.end > now_local]
    best = _best_windows(candidates)
    if best:
        lines.extend(_window_line(window, th) for window in best)
    elif evening:
        lines.append("🧺 nenhuma janela boa no resto do dia")
    else:
        lines.append("🧺 nenhuma janela boa hoje — melhor não pendurar")

    if verdict.cptec_desc:
        period = f" ({verdict.cptec_period})" if verdict.cptec_period else ""
        lines.append(f"📊 CPTEC: {verdict.cptec_desc}{period}")
    lines.append(_disagreement(verdict, th, failures))
    lines.append("")
    lines.extend(_tomorrow_block(tomorrow, th))

    labels = [SOURCE_LABELS.get(s, s) for s in sources_ok]
    if failures:
        labels.append("fora: " + ", ".join(SOURCE_LABELS.get(f, f) for f in failures))
    lines.append("🔌 fontes: " + ", ".join(labels))
    for note in verdict.notes:
        lines.append(f"⚠️ {note}")
    return "\n".join(lines)


def render_rain_alert(
    *,
    cfg: Config,
    first_rain: Hour,
    good_until: datetime | None,
) -> str:
    th = cfg.thresholds
    prob = precip_prob(first_rain, th)
    lines = [
        "🌧️ *Recolha o varal — chuva a caminho*",
        f"Primeira chuva às {_clock(first_rain.local)} "
        f"(prob {_n(prob)}%, {_n(first_rain.precip_mm, 1)} mm/h).",
    ]
    if good_until:
        lines.append(f"Janela boa até {_clock(good_until)}.")
    return "\n".join(lines)