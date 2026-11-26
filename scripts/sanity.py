"""Sanidade do banco de staging: output correto, não só CI verde."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from clothesline.db import connect

TZ = ZoneInfo("America/Sao_Paulo")


def main() -> int:
    now = datetime.now(TZ)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    failures = 0

    def check(ok: bool, msg: str) -> None:
        nonlocal failures
        print(("✅ " if ok else "❌ ") + msg)
        if not ok:
            failures += 1

    with connect() as conn, conn.cursor() as cur:
        # 1. verdict de hoje
        cur.execute("SELECT score, label FROM day_verdict WHERE day = %s", (today,))
        row = cur.fetchone()
        check(
            row is not None
            and row[0] is not None
            and row[1] is not None
            and 0 <= row[0] <= 100
            and bool(row[1].strip()),
            f"day_verdict de {today}: "
            + (f"score={row[0]} label={row[1]!r}" if row else "ausente"),
        )

        # 2. fontes frescas (últimas 3 h)
        cur.execute(
            "SELECT source, max(fetched_at) FROM forecast_hourly "
            "WHERE source IN ('open_meteo','met_no') GROUP BY source"
        )
        fresh = {s: t for s, t in cur.fetchall()}
        for source in ("open_meteo", "met_no"):
            t = fresh.get(source)
            ok = t is not None and (now - t.astimezone(TZ)) <= timedelta(hours=3)
            check(ok, f"{source}: último fetched_at={t.isoformat() if t else 'ausente'}")

        # 3. CPTEC hoje OU amanhã (clima[] começa em D+1 — esperado)
        cur.execute(
            "SELECT day FROM forecast_daily_cptec WHERE day IN (%s,%s) "
            "ORDER BY day DESC LIMIT 1",
            (today, tomorrow),
        )
        row = cur.fetchone()
        check(row is not None, f"forecast_daily_cptec: dia {row[0] if row else 'ausente'}")

        # 4. relatório da manhã (catch-up até 15:59; antes das 16h é só aviso)
        cur.execute("SELECT 1 FROM notifications WHERE key = %s", (f"report:{today}",))
        sent = cur.fetchone() is not None
        if now.hour >= 16:
            check(
                sent,
                f"notifications report:{today}: "
                + ("enviado" if sent else "AUSENTE após 16h (fim do catch-up)"),
            )
        else:
            print(
                f"⚠️ report:{today}: "
                + ("enviado" if sent else "ainda não (catch-up até 15:59)")
            )

        # 4b. relatório da tarde (catch-up até 23:59 — deadline não cabe no mesmo
        # dia, então é só aviso; o watchdog cobre a manhã, que é o crítico)
        cur.execute(
            "SELECT 1 FROM notifications WHERE key = %s",
            (f"report-evening:{today}",),
        )
        sent_evening = cur.fetchone() is not None
        print(
            ("✅ " if sent_evening else "⚠️ ")
            + f"notifications report-evening:{today}: "
            + ("enviado" if sent_evening else "ainda não (catch-up até 23:59)")
        )

        # 5. scores de hoje (hour é UTC; dia local via AT TIME ZONE)
        cur.execute(
            "SELECT count(*) FROM hourly_score "
            "WHERE (hour AT TIME ZONE 'America/Sao_Paulo')::date = %s",
            (today,),
        )
        n = cur.fetchone()[0]
        check(n > 0, f"hourly_score de {today}: {n} linhas")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())