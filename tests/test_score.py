from datetime import date, datetime

import pytest

from clothesline import score
from clothesline.sources import DailyPoint, HourlyPoint

DAY = date(2026, 11, 25)


def test_combine_averages_available_sources(cfg):
    stamp = datetime(2026, 11, 25, 12, 0, tzinfo=cfg.location.tz)
    om = HourlyPoint(source="open_meteo", hour=stamp, temperature_c=20.0, et0_mm=0.5)
    met = HourlyPoint(source="met_no", hour=stamp, temperature_c=30.0, wind_kmh=10.0)
    hour = score.combine([om, met], cfg)[0]
    assert hour.temperature_c == pytest.approx(25.0)
    assert hour.sources == ("met_no", "open_meteo")
    assert hour.et0_mm == pytest.approx(0.5)


def test_vpd_falls_back_to_tetens(cfg):
    stamp = datetime(2026, 11, 25, 12, 0, tzinfo=cfg.location.tz)
    met = HourlyPoint(source="met_no", hour=stamp, temperature_c=25.0, humidity_pct=50.0)
    hour = score.combine([met], cfg)[0]
    assert hour.vpd_kpa == pytest.approx(score.vpd_from(25.0, 50.0), rel=1e-6)


def test_sunny_dry_day_scores_high(cfg, make_hours):
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.6)
    verdict = score.day_verdict(hours, DAY, cfg)
    assert verdict.score >= 70
    assert verdict.label == "bom dia de varal"
    assert verdict.good_day_hours == 13  # 06..18
    assert verdict.windows


def test_rainy_day_scores_low_and_cptec_locks(cfg, make_hours):
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.6, prob=90.0, precip=1.0)
    cptec = DailyPoint(city_code=3656, day=DAY, condicao="c", condicao_desc="Chuva")
    verdict = score.day_verdict(hours, DAY, cfg, cptec)
    assert verdict.score < 40
    assert verdict.cptec_lock is True
    assert verdict.label == "melhor não / secadora"
    assert verdict.windows == []


def test_front_at_15h_leaves_morning_window(cfg, make_hours):
    overrides = {i: {"prob": 85.0} for i in list(range(0, 8)) + list(range(15, 24))}
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.5, overrides=overrides)
    verdict = score.day_verdict(hours, DAY, cfg)
    assert verdict.cptec_lock is False
    assert len(verdict.windows) == 1
    window = verdict.windows[0]
    assert (window.start.hour, window.end.hour) == (8, 15)  # deadline = hora da chuva
    assert window.kind == "day"
    assert window.dry_eta_hours == pytest.approx(3.0)
    assert window.dry_at.hour == 11
    assert window.hang_by.hour == 12


def test_overnight_window_when_air_very_dry(cfg, make_hours):
    hours = make_hours(DAY, start_hour=20, count=17, vpd=1.0, et0=0.5,
                       overrides={i: {"et0": 0.05} for i in range(10)})
    verdict = score.day_verdict(hours, DAY, cfg)
    window = verdict.windows[0]
    assert window.kind == "night"
    assert window.start.hour == 20
    assert window.end.hour == 12
    assert window.dry_at is not None and 6 <= window.dry_at.hour <= 12


def test_humid_night_has_no_window(cfg, make_hours):
    hours = make_hours(DAY, start_hour=20, count=12, vpd=0.5, et0=0.05)
    verdict = score.day_verdict(hours, DAY, cfg)
    assert verdict.windows == []


def test_fallback_dry_rate_without_et0(cfg, make_hours):
    hour = make_hours(DAY, count=1, vpd=1.0, et0=None, wind=10.0)[0]
    assert score.dry_rate(hour, cfg.thresholds) == pytest.approx(
        cfg.thresholds.fallback_dry_rate_k * 1.0 * (0.5 + 0.06 * 10.0)
    )


def test_gust_penalty_applies(cfg, make_hours):
    calm = score.day_verdict(make_hours(DAY, count=24, vpd=1.2, et0=0.6), DAY, cfg)
    windy = score.day_verdict(
        make_hours(DAY, count=24, vpd=1.2, et0=0.6, overrides={15: {"gust": 45.0}}),
        DAY,
        cfg,
    )
    assert calm.score - windy.score == cfg.thresholds.gust_penalty
    assert windy.notes


def test_cptec_afternoon_lock_trims_windows(cfg, make_hours):
    overrides = {i: {"prob": 85.0} for i in list(range(0, 6)) + list(range(12, 24))}
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.5, overrides=overrides)
    cptec = DailyPoint(city_code=3656, day=DAY, condicao="ct", condicao_desc="Chuva a Tarde")
    verdict = score.day_verdict(hours, DAY, cfg, cptec)
    assert verdict.cptec_lock is True
    assert verdict.cptec_period == "tarde"
    assert [w.end.hour for w in verdict.windows] == [12]
    assert all(w.hours >= 3 for w in verdict.windows)


def test_cptec_lock_penalty(cfg, make_hours):
    overrides = {i: {"prob": 85.0} for i in range(0, 6)}
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.5, overrides=overrides)
    cptec = DailyPoint(city_code=3656, day=DAY, condicao="pnt", condicao_desc="Chuva a Noite")
    verdict = score.day_verdict(hours, DAY, cfg, cptec)
    assert verdict.score == 100 - int(cfg.thresholds.cptec_lock_penalty)


def test_missing_probability_uses_precipitation_amount(cfg, make_hours):
    hour = make_hours(DAY, count=1, prob=None, precip=0.9)[0]
    assert score.precip_prob(hour, cfg.thresholds) == 100.0
    assert score.good_hour(hour, cfg.thresholds) is False


def test_hourly_rows_carry_features(cfg, make_hours):
    rows = score.hourly_rows(make_hours(DAY, count=2), cfg.thresholds)
    assert set(rows[0]["features"]) >= {"vpd_kpa", "et0_mm", "is_day", "sources"}
    assert rows[0]["features"]["is_day"] is False


def test_new_rain_codes_lock_the_day(cfg, make_hours):
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.6)
    for code in ["pc", "ch", "cv", "t", "pm", "npm", "npt", "npn", "npp", "ppt", "ppm", "ppn"]:
        cptec = DailyPoint(city_code=3656, day=DAY, condicao=code)
        verdict = score.day_verdict(hours, DAY, cfg, cptec)
        assert verdict.cptec_lock is True, code
        assert verdict.score == 100 - int(cfg.thresholds.cptec_lock_penalty), code


def test_clear_codes_do_not_lock(cfg, make_hours):
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.6)
    for code in ["cl", "pn", "ps"]:
        cptec = DailyPoint(city_code=3656, day=DAY, condicao=code)
        assert score.day_verdict(hours, DAY, cfg, cptec).cptec_lock is False, code


def test_new_period_codes_map_and_prune(cfg, make_hours):
    assert score.cptec_period("pm", cfg) == (True, "manhã")
    assert score.cptec_period("npt", cfg) == (True, "tarde")
    assert score.cptec_period("npn", cfg) == (True, "noite")
    assert score.cptec_period("pc", cfg) == (True, None)
    overrides = {i: {"prob": 85.0} for i in list(range(0, 8)) + list(range(15, 24))}
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.5, overrides=overrides)
    cptec = DailyPoint(city_code=3656, day=DAY, condicao="npm")
    verdict = score.day_verdict(hours, DAY, cfg, cptec)
    assert verdict.windows == []  # janela 08–15 não cabe antes das 6h
    assert any("manhã" in n for n in verdict.notes)