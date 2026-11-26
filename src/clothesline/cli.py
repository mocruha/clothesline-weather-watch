"""Orquestração: ingest → persistência → decisão → envio."""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from clothesline import db, notify, report, score, sources
from clothesline.config import Config, load_config

log = logging.getLogger(__name__)


class Store(Protocol):
    def has(self, key: str) -> bool: ...
    def keys(self, prefix: str) -> list[str]: ...
    def record(self, key: str, kind: str, payload: dict) -> None: ...


class MemoryStore:
    """Store em memória: usado no --dry-run e nos testes."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def has(self, key: str) -> bool:
        return key in self.rows

    def keys(self, prefix: str) -> list[str]:
        return [key for key in self.rows if key.startswith(prefix)]

    def record(self, key: str, kind: str, payload: dict) -> None:
        self.rows[key] = {"kind": kind, "payload": payload}


class DbStore:
    def __init__(self, conn) -> None:
        self.conn = conn

    def has(self, key: str) -> bool:
        return db.notification_sent(self.conn, key)

    def keys(self, prefix: str) -> list[str]:
        return db.keys_with_prefix(self.conn, prefix)

    def record(self, key: str, kind: str, payload: dict) -> None:
        db.record_notification(self.conn, key, kind, payload)


@dataclass(slots=True)
class Message:
    key: str
    kind: str
    text: str
    payload: dict


@dataclass(slots=True)
class Decision:
    messages: list[Message]
    verdict: score.DayVerdict


def morning_due(now_local: datetime, cfg: Config) -> bool:
    """Manhã devida a partir de start_hour; catch-up até o início da tarde."""
    return cfg.report.start_hour <= now_local.hour < cfg.report.evening_start_hour


def evening_due(now_local: datetime, cfg: Config) -> bool:
    """Tarde devida a partir de evening_start_hour; catch-up até o fim do dia."""
    return cfg.report.evening_start_hour <= now_local.hour


def rain_event(hours: list[score.Hour], now_local: datetime, cfg: Config) -> score.Hour | None:
    """Primeira hora com chuva relevante nas próximas rain_alert_horizon_hours."""
    th = cfg.thresholds
    horizon = now_local + timedelta(hours=th.rain_alert_horizon_hours)
    for hour in sorted(hours, key=lambda h: h.hour):
        if hour.local + timedelta(hours=1) <= now_local:
            continue
        if hour.local > horizon:
            break
        if (
            score.precip_prob(hour, th) >= th.rain_alert_prob_pct
            or (hour.precip_mm or 0.0) >= th.rain_alert_mm
        ):
            return hour
    return None


def _alerted_recently(
    store: Store, prefix_day: str, rain_at: datetime, cfg: Config
) -> bool:
    """Não repete alerta se o início da chuva já avisado está a < shift horas."""
    prefix = f"rain:{prefix_day}T"
    for key in store.keys(prefix):
        try:
            previous = int(key.rsplit("T", 1)[1])
        except (IndexError, ValueError):
            continue
        if abs(previous - rain_at.hour) < cfg.thresholds.rain_alert_shift_hours:
            return True
    return False


def plan(
    cfg: Config,
    hours: list[score.Hour],
    dailies: list[sources.DailyPoint],
    now_local: datetime,
    store: Store,
    *,
    force_report: bool = False,
    failures: list[str] | None = None,
) -> Decision:
    failures = failures or []
    today = now_local.date()
    cptec_today = next((d for d in dailies if d.day == today), None)
    verdict = score.day_verdict(hours, today, cfg, cptec_today)
    tomorrow = today + timedelta(days=1)
    tomorrow_verdict = None
    if any(h.local.date() == tomorrow for h in hours):
        cptec_tomorrow = next((d for d in dailies if d.day == tomorrow), None)
        tomorrow_verdict = score.day_verdict(hours, tomorrow, cfg, cptec_tomorrow)
    messages: list[Message] = []
    sources_ok = sorted({s for h in hours if h.local.date() == today for s in h.sources})
    if cptec_today is not None:
        sources_ok.append("cptec")

    if morning_due(now_local, cfg) or force_report:
        key = f"report:{today.isoformat()}"
        if not store.has(key):
            messages.append(
                Message(
                    key=key,
                    kind="report",
                    text=report.render_report(
                        verdict,
                        cfg,
                        sources_ok=sources_ok,
                        failures=failures,
                        tomorrow=tomorrow_verdict,
                    ),
                    payload={
                        "day": today.isoformat(),
                        "score": verdict.score,
                        "label": verdict.label,
                        "windows": len(verdict.windows),
                        "tomorrow_score": (
                            tomorrow_verdict.score if tomorrow_verdict else None
                        ),
                    },
                )
            )

    if evening_due(now_local, cfg) or force_report:
        key = f"report-evening:{today.isoformat()}"
        if not store.has(key):
            messages.append(
                Message(
                    key=key,
                    kind="report",
                    text=report.render_report(
                        verdict,
                        cfg,
                        sources_ok=sources_ok,
                        failures=failures,
                        tomorrow=tomorrow_verdict,
                        evening=True,
                        now_local=now_local,
                    ),
                    payload={
                        "day": today.isoformat(),
                        "score": verdict.score,
                        "label": verdict.label,
                        "windows": len(verdict.windows),
                        "tomorrow_score": (
                            tomorrow_verdict.score if tomorrow_verdict else None
                        ),
                    },
                )
            )

    rain = rain_event(hours, now_local, cfg)
    if rain is not None:
        # só alerta se o dia recomendou varal antes da chuva
        active = any(window.start < rain.local for window in verdict.windows)
        day_key = rain.local.date().isoformat()
        key = f"rain:{day_key}T{rain.local:%H}"
        if active and not store.has(key) and not _alerted_recently(
            store, day_key, rain.local, cfg
        ):
            good_until = None
            for hour in sorted(hours, key=lambda h: h.hour):
                if hour.local >= rain.local:
                    break
                if score.good_hour(hour, cfg.thresholds):
                    good_until = hour.local + timedelta(hours=1)
            messages.append(
                Message(
                    key=key,
                    kind="rain",
                    text=report.render_rain_alert(
                        cfg=cfg, first_rain=rain, good_until=good_until
                    ),
                    payload={
                        "rain_at": rain.local.isoformat(),
                        "prob": score.precip_prob(rain, cfg.thresholds),
                        "mm": rain.precip_mm,
                    },
                )
            )

    return Decision(messages=messages, verdict=verdict)


def run_once(
    cfg: Config | None = None,
    *,
    dry_run: bool = False,
    force_report: bool = False,
    now: datetime | None = None,
    sender: Callable[[str, str, str], None] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    cfg = cfg or load_config()
    now_utc = now or datetime.now(timezone.utc)
    now_local = now_utc.astimezone(cfg.location.tz)
    loc = cfg.location

    raw_points, dailies, failures = sources.fetch_all(
        loc.latitude, loc.longitude, loc.timezone, loc.cptec_city_code
    )
    points = sources.dedupe(raw_points)
    hours = score.combine(points, cfg)
    log.info(
        "ingestão: %s horas de %s, %s dias CPTEC, falhas: %s",
        len(hours),
        ", ".join(sorted({p.source for p in points})),
        len(dailies),
        ", ".join(failures) or "nenhuma",
    )

    if dry_run:
        decision = plan(
            cfg,
            hours,
            dailies,
            now_local,
            MemoryStore(),
            force_report=True,
            failures=failures,
        )
        for message in decision.messages:
            out(f"--- {message.kind} [{message.key}]")
            out(message.text)
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    send = sender or notify.send
    with db.connect() as conn:
        db.apply_migrations(conn)
        db.upsert_hourly(conn, points)
        db.upsert_daily(conn, dailies)
        db.upsert_scores(conn, score.hourly_rows(hours, cfg.thresholds))
        store = DbStore(conn)
        decision = plan(
            cfg,
            hours,
            dailies,
            now_local,
            store,
            force_report=force_report,
            failures=failures,
        )
        db.upsert_verdict(
            conn,
            day=decision.verdict.day,
            score=decision.verdict.score,
            label=decision.verdict.label,
            windows=[_window_payload(w) for w in decision.verdict.windows],
            cptec_lock=decision.verdict.cptec_lock,
        )
        for message in decision.messages:
            send(token, chat_id, message.text)
            store.record(message.key, message.kind, message.payload)
            log.info("enviado %s", message.key)
        if not decision.messages:
            log.info("nada a enviar neste run")
        purged = db.purge_hourly(conn, cfg.retention_days)
        if purged:
            log.info("purge: %s linhas de forecast_hourly removidas", purged)
    return 0


def _window_payload(window: score.Window) -> dict:
    return {
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "kind": window.kind,
        "dry_eta_hours": window.dry_eta_hours,
        "hang_by": window.hang_by.isoformat() if window.hang_by else None,
        "dry_at": window.dry_at.isoformat() if window.dry_at else None,
    }


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # o httpx loga a URL completa no INFO, e a URL do Telegram carrega o token
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="watch", description="Relatório de varal a partir de 3 APIs abertas"
    )
    parser.add_argument("--once", action="store_true", help="um ciclo e sai")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ingere de verdade e imprime a mensagem; não toca DB nem Telegram",
    )
    parser.add_argument(
        "--force-report",
        action="store_true",
        help="envia os relatórios (manhã e tarde) mesmo fora das janelas (teste manual)",
    )
    args = parser.parse_args(argv)
    configure_logging()
    if not args.once:
        parser.error("apenas --once é suportado; o agendamento é o cron do Actions")
    try:
        return run_once(None, dry_run=args.dry_run, force_report=args.force_report)
    except sources.SourceError as exc:
        log.error("ingestão abortada: %s", exc)
        return 1
    except notify.NotifyError as exc:
        log.error("envio falhou: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())