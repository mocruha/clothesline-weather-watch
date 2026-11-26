from datetime import date, datetime, timedelta

from clothesline import report, score
from clothesline.sources import DailyPoint

DAY = date(2026, 11, 25)


def tomorrow_verdict(cfg, make_hours):
    day = DAY + timedelta(days=1)
    hours = make_hours(day, count=24, vpd=1.2, et0=0.6)
    return score.day_verdict(hours, day, cfg)


def test_report_has_score_window_and_cptec_disagreement(cfg, make_hours):
    # modelos secos (prob 45% < 60%) mas CPTEC trava o dia: discordância visível
    overrides = {i: {"prob": 45.0} for i in list(range(0, 8)) + list(range(15, 24))}
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.5, overrides=overrides)
    cptec = DailyPoint(city_code=3656, day=DAY, condicao="ci", condicao_desc="Chuvas Isoladas")
    verdict = score.day_verdict(hours, DAY, cfg, cptec)
    tomorrow = tomorrow_verdict(cfg, make_hours)
    text = report.render_report(
        verdict,
        cfg,
        sources_ok=["open_meteo", "met_no", "cptec"],
        failures=[],
        tomorrow=tomorrow,
    )
    assert "Varal em Osasco" in text
    assert f"Score {verdict.score}/100" in text
    assert "janela 08h–15h" in text
    assert "CPTEC: Chuvas Isoladas" in text
    assert "CPTEC prevê chuva" in text
    assert "Open-Meteo, MET Norway, CPTEC/INPE" in text
    assert "(rajada 10 km/h)" in text
    assert "☀️ UV 5" in text and "UV 5,0" not in text
    assert text.count("💨 vento") == 2
    assert text.count("🌧️ chuva: prob máx") == 2
    assert text.count("💧 umidade") == 1
    assert "Amanhã" in text
    assert f"Score {tomorrow.score}/100" in text


def test_report_reports_missing_cptec_source(cfg, make_hours):
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.6)
    verdict = score.day_verdict(hours, DAY, cfg)
    text = report.render_report(
        verdict,
        cfg,
        sources_ok=["open_meteo", "met_no"],
        failures=["cptec"],
        tomorrow=tomorrow_verdict(cfg, make_hours),
    )
    assert "📡 CPTEC/INPE indisponível" in text
    assert "· fora do ar: CPTEC/INPE" in text


def test_report_without_tomorrow_data_says_so(cfg, make_hours):
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.6)
    verdict = score.day_verdict(hours, DAY, cfg)
    text = report.render_report(verdict, cfg, sources_ok=["open_meteo"], failures=[])
    assert "Amanhã: sem dados" in text
    assert "ℹ️ CPTEC não traz previsão para hoje" in text


def test_evening_report_drops_windows_that_already_ended(cfg, make_hours):
    overrides = {i: {"prob": 45.0} for i in list(range(0, 8)) + list(range(15, 24))}
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.5, overrides=overrides)
    verdict = score.day_verdict(hours, DAY, cfg)
    now_local = datetime(2026, 11, 25, 16, 30, tzinfo=cfg.location.tz)
    text = report.render_report(
        verdict, cfg, sources_ok=["open_meteo"], failures=[], evening=True, now_local=now_local
    )
    assert "fim de tarde" in text
    assert "nenhuma janela boa no resto do dia" in text
    assert "08h–15h" not in text


def test_evening_report_keeps_window_that_still_runs(cfg, make_hours):
    overrides = {i: {"prob": 45.0} for i in list(range(0, 17)) + list(range(20, 24))}
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.5, overrides=overrides)
    verdict = score.day_verdict(hours, DAY, cfg)
    now_local = datetime(2026, 11, 25, 16, 30, tzinfo=cfg.location.tz)
    text = report.render_report(
        verdict, cfg, sources_ok=["open_meteo"], failures=[], evening=True, now_local=now_local
    )
    assert "fim de tarde" in text
    assert "janela 17h–20h" in text


def test_report_renders_midnight_window_end_as_24h(cfg, make_hours):
    # dia bom: 24h boas, eta 3h -> janela 00h->00h, pendure 21h, seca 03h
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.5)
    verdict = score.day_verdict(hours, DAY, cfg)
    text = report.render_report(verdict, cfg, sources_ok=["open_meteo"], failures=[])
    assert "janela 00h–24h" in text
    assert "pendure 21h–24h" in text
    assert "seca ~03h" in text
    assert "00h–00h" not in text


def test_report_marks_drying_after_midnight_as_madrugada(cfg, make_hours):
    # janela noturna 21h->03h do dia seguinte, eta 6h -> seca 03h (madrugada)
    hours = make_hours(DAY, start_hour=21, count=6, vpd=1.2, et0=0.25)
    verdict = score.day_verdict(hours, DAY, cfg)
    text = report.render_report(verdict, cfg, sources_ok=["open_meteo"], failures=[])
    assert "21h–03h" in text
    assert "seca ~03h (madrugada)" in text


def test_report_keeps_uv_decimal_when_not_zero(cfg, make_hours):
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.5, uv=5.5)
    verdict = score.day_verdict(hours, DAY, cfg)
    text = report.render_report(verdict, cfg, sources_ok=["open_meteo"], failures=[])
    assert "☀️ UV 5,5" in text


def test_alert_names_first_rain_and_last_good_hour(cfg, make_hours):
    hours = make_hours(
        DAY, count=24, vpd=1.2, et0=0.5, overrides={15: {"prob": 85.0, "precip": 1.2}}
    )
    rain = next(h for h in hours if h.local.hour == 15)
    text = report.render_rain_alert(cfg=cfg, first_rain=rain, good_until=rain.local)
    assert "Recolha o varal" in text
    assert "15h" in text
    assert "85%" in text and "1,2 mm/h" in text
    assert "Janela boa até 15h — recolha antes." in text