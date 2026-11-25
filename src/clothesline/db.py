"""Neon Postgres: migrations idempotentes e upserts append-only."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Iterable

from clothesline.config import REPO_ROOT
from clothesline.sources import DailyPoint, HourlyPoint

log = logging.getLogger(__name__)
MIGRATIONS_DIR = REPO_ROOT / "migrations"

HOURLY_COLUMNS = (
    "source",
    "target_hour",
    "temperature_c",
    "humidity_pct",
    "precip_mm",
    "precip_prob_pct",
    "wind_kmh",
    "gust_kmh",
    "uv_index",
    "cloud_pct",
    "vpd_kpa",
    "et0_mm",
    "radiation_wm2",
)


def dsn_from_env() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL ausente no ambiente")
    return dsn


def connect(dsn: str | None = None):
    import psycopg

    return psycopg.connect(dsn or dsn_from_env())


def apply_migrations(conn) -> list[str]:
    """Roda migrations/*.sql em ordem; cada arquivo roda no máximo uma vez."""
    applied_now: list[str] = []
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        cur.execute("SELECT filename FROM schema_migrations")
        applied = {row[0] for row in cur.fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
            )
            applied_now.append(path.name)
    conn.commit()
    if applied_now:
        log.info("migrations aplicadas: %s", ", ".join(applied_now))
    return applied_now


def upsert_hourly(conn, points: Iterable[HourlyPoint]) -> int:
    rows = [
        (
            p.source,
            p.hour,
            p.temperature_c,
            p.humidity_pct,
            p.precip_mm,
            p.precip_prob_pct,
            p.wind_kmh,
            p.gust_kmh,
            p.uv_index,
            p.cloud_pct,
            p.vpd_kpa,
            p.et0_mm,
            p.radiation_wm2,
        )
        for p in points
    ]
    if not rows:
        return 0
    placeholders = ", ".join(["%s"] * len(HOURLY_COLUMNS))
    sql = (
        f"INSERT INTO forecast_hourly ({', '.join(HOURLY_COLUMNS)}) "
        f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    )
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def upsert_daily(conn, points: Iterable[DailyPoint]) -> int:
    rows = [
        (p.city_code, p.day, p.condicao, p.condicao_desc, p.tmin_c, p.tmax_c, p.indice_uv)
        for p in points
    ]
    if not rows:
        return 0
    sql = (
        "INSERT INTO forecast_daily_cptec "
        "(city_code, day, condicao, condicao_desc, tmin_c, tmax_c, indice_uv) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"
    )
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def upsert_scores(conn, rows: Iterable[dict[str, Any]]) -> int:
    """rows: hour, good, dry_rate_mm_h, vpd_kpa, precip_prob_pct, features"""
    payload = [
        (
            r["hour"],
            r["good"],
            r.get("dry_rate_mm_h"),
            r.get("vpd_kpa"),
            r.get("precip_prob_pct"),
            json.dumps(r["features"], default=_json_default),
        )
        for r in rows
    ]
    if not payload:
        return 0
    sql = (
        "INSERT INTO hourly_score "
        "(hour, good, dry_rate_mm_h, vpd_kpa, precip_prob_pct, features) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (hour) DO UPDATE SET computed_at = now(), good = excluded.good, "
        "dry_rate_mm_h = excluded.dry_rate_mm_h, vpd_kpa = excluded.vpd_kpa, "
        "precip_prob_pct = excluded.precip_prob_pct, features = excluded.features"
    )
    with conn.cursor() as cur:
        cur.executemany(sql, payload)
    conn.commit()
    return len(payload)


def upsert_verdict(
    conn,
    *,
    day: date,
    score: int,
    label: str,
    windows: Any,
    cptec_lock: bool,
) -> None:
    sql = (
        "INSERT INTO day_verdict (day, score, label, windows, cptec_lock) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (day) DO UPDATE SET computed_at = now(), score = excluded.score, "
        "label = excluded.label, windows = excluded.windows, "
        "cptec_lock = excluded.cptec_lock"
    )
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (day, score, label, json.dumps(windows, default=_json_default), cptec_lock),
        )
    conn.commit()


def notification_sent(conn, key: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM notifications WHERE key = %s", (key,))
        return cur.fetchone() is not None


def keys_with_prefix(conn, prefix: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT key FROM notifications WHERE key LIKE %s", (prefix + "%",))
        return [row[0] for row in cur.fetchall()]


def record_notification(conn, key: str, kind: str, payload: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notifications (key, kind, payload) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (key, kind, json.dumps(payload, default=_json_default)),
        )
    conn.commit()


def purge_hourly(conn, days: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM forecast_hourly WHERE fetched_at < now() - make_interval(days => %s)",
            (days,),
        )
        deleted = cur.rowcount
    conn.commit()
    return deleted


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return asdict(value) if hasattr(value, "__dataclass_fields__") else str(value)


def main(argv: list[str] | None = None) -> int:
    import sys

    args = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args[:1] != ["migrate"]:
        print("uso: python -m clothesline.db migrate", file=sys.stderr)
        return 2
    try:
        with connect() as conn:
            applied = apply_migrations(conn)
    except RuntimeError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    print(f"ok: {len(applied)} migration(s) aplicada(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())