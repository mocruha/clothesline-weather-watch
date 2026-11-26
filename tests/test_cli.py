import json
from datetime import date, datetime, timezone
from pathlib import Path

from clothesline import cli, sources
from clothesline.cli import MemoryStore, plan

DAY = date(2026, 11, 25)
FIXTURES = Path(__file__).parent / "fixtures"


def at(cfg, hour, minute=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=cfg.location.tz)


def test_report_is_sent_once_per_day(cfg, make_hours):
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.6)
    store = MemoryStore()
    first = plan(cfg, hours, [], at(cfg, 9, 30), store)
    assert [m.kind for m in first.messages] == ["report"]
    assert first.messages[0].key == "report:2026-11-25"

    for message in first.messages:
        store.record(message.key, message.kind, message.payload)
    again = plan(cfg, hours, [], at(cfg, 9, 55), store)
    assert again.messages == []


def test_no_report_before_morning_window(cfg, make_hours):
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.6)
    assert plan(cfg, hours, [], at(cfg, 8, 59), MemoryStore()).messages == []


def test_morning_report_catches_up_until_evening(cfg, make_hours):
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.6)
    store = MemoryStore()
    decision = plan(cfg, hours, [], at(cfg, 15, 0), store)
    assert [m.key for m in decision.messages] == ["report:2026-11-25"]

    for message in decision.messages:
        store.record(message.key, message.kind, message.payload)
    assert plan(cfg, hours, [], at(cfg, 15, 30), store).messages == []


def test_morning_report_expires_at_evening_start(cfg, make_hours):
    hours = make_hours(DAY, count=48, vpd=1.2, et0=0.6)
    decision = plan(cfg, hours, [], at(cfg, 16, 0), MemoryStore())
    assert [m.key for m in decision.messages] == ["report-evening:2026-11-25"]


def test_evening_report_catches_up_late(cfg, make_hours):
    hours = make_hours(DAY, count=48, vpd=1.2, et0=0.6)
    decision = plan(cfg, hours, [], at(cfg, 22, 30), MemoryStore())
    assert [m.key for m in decision.messages] == ["report-evening:2026-11-25"]


def test_morning_report_includes_tomorrow(cfg, make_hours):
    hours = make_hours(DAY, count=48, vpd=1.2, et0=0.6)
    decision = plan(cfg, hours, [], at(cfg, 9, 30), MemoryStore())
    assert [m.key for m in decision.messages] == ["report:2026-11-25"]
    assert isinstance(decision.messages[0].payload["tomorrow_score"], int)
    assert "Amanhã" in decision.messages[0].text


def test_evening_report_is_sent_once_per_day(cfg, make_hours):
    hours = make_hours(DAY, count=48, vpd=1.2, et0=0.6)
    store = MemoryStore()
    first = plan(cfg, hours, [], at(cfg, 16, 30), store)
    assert [m.kind for m in first.messages] == ["report"]
    assert first.messages[0].key == "report-evening:2026-11-25"
    assert "fim de tarde" in first.messages[0].text

    for message in first.messages:
        store.record(message.key, message.kind, message.payload)
    assert plan(cfg, hours, [], at(cfg, 16, 55), store).messages == []


def test_evening_key_is_independent_from_morning_key(cfg, make_hours):
    hours = make_hours(DAY, count=48, vpd=1.2, et0=0.6)
    store = MemoryStore()
    store.record("report:2026-11-25", "report", {})
    decision = plan(cfg, hours, [], at(cfg, 16, 30), store)
    assert [m.key for m in decision.messages] == ["report-evening:2026-11-25"]


def test_evening_report_survives_without_tomorrow_hours(cfg, make_hours):
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.6)
    decision = plan(cfg, hours, [], at(cfg, 16, 30), MemoryStore())
    assert [m.key for m in decision.messages] == ["report-evening:2026-11-25"]
    assert decision.messages[0].payload["tomorrow_score"] is None
    assert "Amanhã: sem dados" in decision.messages[0].text


def test_rain_alert_fires_when_day_recommended_varal(cfg, make_hours):
    good = {i: {} for i in range(24)}
    good[12] = {"prob": 85.0}
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.6, overrides=good)
    store = MemoryStore()
    store.record("report:2026-11-25", "report", {})  # manhã já saiu; só o alerta interessa
    decision = plan(cfg, hours, [], at(cfg, 11, 0), store)
    assert [m.kind for m in decision.messages] == ["rain"]
    assert decision.messages[0].key == "rain:2026-11-25T12"
    assert "Recolha o varal" in decision.messages[0].text
    assert "até 12h" in decision.messages[0].text


def test_rain_alert_is_not_repeated_but_moves_if_rain_shifts(cfg, make_hours):
    store = MemoryStore()
    store.record("report:2026-11-25", "report", {})  # manhã já saiu; só o alerta interessa

    def with_rain(hour):
        return make_hours(
            DAY, count=24, vpd=1.2, et0=0.6, overrides={hour: {"prob": 85.0}}
        )

    first = plan(cfg, with_rain(12), [], at(cfg, 11, 0), store)
    assert [m.key for m in first.messages] == ["rain:2026-11-25T12"]
    for message in first.messages:
        store.record(message.key, message.kind, message.payload)

    shifted_1h = plan(cfg, with_rain(13), [], at(cfg, 11, 0), store)
    assert shifted_1h.messages == []

    shifted_2h = plan(cfg, with_rain(14), [], at(cfg, 11, 0), store)
    assert [m.key for m in shifted_2h.messages] == ["rain:2026-11-25T14"]


def test_no_alert_when_day_never_recommended_varal(cfg, make_hours):
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.6, prob=90.0, precip=1.0)
    store = MemoryStore()
    store.record("report:2026-11-25", "report", {})  # manhã já saiu; nada mais a enviar
    decision = plan(cfg, hours, [], at(cfg, 11, 0), store)
    assert decision.messages == []


def test_dry_run_prints_message_without_touching_db(cfg, monkeypatch):
    points = sources.parse_open_meteo(
        json.loads((FIXTURES / "open_meteo.json").read_text(encoding="utf-8"))
    )
    monkeypatch.setattr(sources, "fetch_all", lambda *a, **k: (points, [], []))

    def forbidden(*args, **kwargs):
        raise AssertionError("--dry-run não pode tocar o banco")

    monkeypatch.setattr(cli.db, "connect", forbidden)
    printed: list[str] = []
    code = cli.run_once(
        cfg,
        dry_run=True,
        now=datetime(2026, 11, 25, 12, 30, tzinfo=timezone.utc),  # 09:30 local
        out=printed.append,
    )
    text = "\n".join(printed)
    assert code == 0
    assert "Varal em Osasco" in text
    assert "Score" in text
    assert "fontes:" in text
    assert "fim de tarde" in text