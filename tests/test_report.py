from datetime import date

from clothesline import report, score
from clothesline.sources import DailyPoint

DAY = date(2026, 11, 25)


def test_report_has_score_window_and_cptec_disagreement(cfg, make_hours):
    # modelos secos (prob 45% < 60%) mas CPTEC trava o dia: discordância visível
    overrides = {i: {"prob": 45.0} for i in list(range(0, 8)) + list(range(15, 24))}
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.5, overrides=overrides)
    cptec = DailyPoint(city_code=3656, day=DAY, condicao="ci", condicao_desc="Chuvas Isoladas")
    verdict = score.day_verdict(hours, DAY, cfg, cptec)
    text = report.render_report(
        verdict, cfg, sources_ok=["open_meteo", "met_no", "cptec"], failures=[]
    )
    assert "Varal em Osasco" in text
    assert f"Score {verdict.score}/100" in text
    assert "janela 08h–15h" in text
    assert "CPTEC: Chuvas Isoladas" in text
    assert "CPTEC prevê chuva" in text
    assert "Open-Meteo, MET Norway, CPTEC/INPE" in text


def test_report_reports_missing_cptec_source(cfg, make_hours):
    hours = make_hours(DAY, count=24, vpd=1.2, et0=0.6)
    verdict = score.day_verdict(hours, DAY, cfg)
    text = report.render_report(
        verdict, cfg, sources_ok=["open_meteo", "met_no"], failures=["cptec"]
    )
    assert "CPTEC/INPE indisponível" in text
    assert "fora: CPTEC/INPE" in text


def test_alert_names_first_rain_and_last_good_hour(cfg, make_hours):
    hours = make_hours(
        DAY, count=24, vpd=1.2, et0=0.5, overrides={15: {"prob": 85.0, "precip": 1.2}}
    )
    rain = next(h for h in hours if h.local.hour == 15)
    text = report.render_rain_alert(cfg=cfg, first_rain=rain, good_until=rain.local)
    assert "Recolha o varal" in text
    assert "15h" in text
    assert "85%" in text and "1,2 mm/h" in text