import json
from pathlib import Path

import pytest

from clothesline import sources

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_open_meteo_parse():
    points = sources.parse_open_meteo(load("open_meteo.json"))
    assert len(points) == 72
    first = points[0]
    assert first.source == "open_meteo"
    assert first.hour.isoformat() == "2026-11-24T03:00:00+00:00"  # 00:00 local
    assert first.temperature_c == 21.3
    assert first.et0_mm is not None


def test_met_no_parse_converts_ms_to_kmh():
    points = sources.parse_met(load("met_no.json"))
    assert len(points) == 90
    first = points[0]
    assert first.source == "met_no"
    assert first.hour.isoformat() == "2026-11-25T21:00:00+00:00"
    assert first.wind_kmh == pytest.approx(2.7 * 3.6)
    assert first.symbol == "lightrain"
    assert first.precip_mm == pytest.approx(0.3)


def test_cptec_parse():
    days = sources.parse_cptec(load("cptec.json"))
    assert [d.day.isoformat() for d in days] == [
        "2026-11-26",
        "2026-11-27",
        "2026-11-28",
        "2026-11-29",
    ]
    assert days[0].condicao == "c"
    assert days[0].condicao_desc == "Chuva"
    assert (days[0].tmin_c, days[0].tmax_c) == (17.0, 22.0)


def test_out_of_range_field_is_dropped_not_zeroed():
    payload = load("open_meteo.json")
    payload["hourly"]["relative_humidity_2m"][0] = 250.0
    points = sources.parse_open_meteo(payload)
    assert points[0].humidity_pct is None
    assert points[1].humidity_pct is not None


def test_point_without_temperature_is_discarded():
    payload = load("open_meteo.json")
    payload["hourly"]["temperature_2m"][0] = None
    points = sources.parse_open_meteo(payload)
    assert len(points) == 71


def test_missing_hourly_block_raises():
    with pytest.raises(sources.SourceError):
        sources.parse_open_meteo({"hourly": {}})


def test_dedupe_keeps_latest_per_source_and_hour():
    points = sources.parse_open_meteo(load("open_meteo.json"))
    duplicate = points[0]
    twin = sources.HourlyPoint(source=duplicate.source, hour=duplicate.hour, temperature_c=99.0)
    deduped = sources.dedupe(points + [twin])
    assert len(deduped) == len(points)
    assert deduped[0].temperature_c == 99.0


def test_fetch_all_survives_one_source_failure(monkeypatch):
    good = sources.parse_open_meteo(load("open_meteo.json"))

    def boom(*args, **kwargs):
        raise sources.SourceError("fora do ar")

    monkeypatch.setattr(sources, "fetch_open_meteo", boom)
    monkeypatch.setattr(sources, "fetch_met", lambda lat, lon: good)
    monkeypatch.setattr(sources, "fetch_cptec", lambda code: [])
    hourly, daily, failures = sources.fetch_all(-23.53, -46.79, "America/Sao_Paulo", 3656)
    assert len(hourly) == 72
    assert daily == []
    assert failures == ["open_meteo"]  # cptec vazio não é falha


def test_fetch_all_aborts_without_hourly_sources(monkeypatch):
    def boom(*args, **kwargs):
        raise sources.SourceError("fora do ar")

    monkeypatch.setattr(sources, "fetch_open_meteo", boom)
    monkeypatch.setattr(sources, "fetch_met", boom)
    monkeypatch.setattr(sources, "fetch_cptec", lambda code: [])
    with pytest.raises(sources.SourceError):
        sources.fetch_all(-23.53, -46.79, "America/Sao_Paulo", 3656)