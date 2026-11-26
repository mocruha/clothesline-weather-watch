"""Watchdog diário: cron do watch ocioso ou relatório da manhã ausente → exit 1."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import psycopg

from clothesline.db import connect

TZ = ZoneInfo("America/Sao_Paulo")
MIN_RUNS_24H = 12  # 2 crons/hora esperam 48; 12 tolera throttling forte


def main() -> int:
    now = datetime.now(TZ)
    today = now.date()
    failures = 0

    def check(ok: bool, msg: str) -> None:
        nonlocal failures
        print(("✅ " if ok else "❌ ") + msg)
        if not ok:
            failures += 1

    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{repo}/actions/workflows/watch.yml/runs",
            params={"event": "schedule", "per_page": 100},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        runs = [
            r
            for r in resp.json()["workflow_runs"]
            if datetime.fromisoformat(r["created_at"]) >= cutoff
        ]
        check(
            len(runs) >= MIN_RUNS_24H,
            f"watch: {len(runs)} runs schedule nas últimas 24h (mínimo {MIN_RUNS_24H})",
        )
    except httpx.HTTPError as exc:
        check(False, f"watch: API do GitHub inacessível ({exc})")
    except (KeyError, TypeError, ValueError) as exc:
        check(False, f"watch: resposta inesperada da API do GitHub ({exc!r})")

    key = f"report:{today}"
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM notifications WHERE key = %s", (key,))
            sent = cur.fetchone() is not None
    except (RuntimeError, psycopg.Error) as exc:
        check(False, f"notifications {key}: banco inacessível ({exc})")
    else:
        check(sent, f"notifications {key}: " + ("enviado" if sent else "AUSENTE (deadline 16h)"))

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())