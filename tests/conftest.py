import pytest

from clothesline.config import load_config
from clothesline.score import Hour


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def make_hours(cfg):
    """Série sintética de Hour. `overrides` é indexado pelo offset a partir de start_hour."""

    def build(
        day,
        start_hour=0,
        count=24,
        *,
        vpd=1.0,
        et0=0.5,
        precip=0.0,
        prob=0.0,
        gust=10.0,
        wind=10.0,
        temp=25.0,
        rh=50.0,
        uv=5.0,
        overrides=None,
    ):
        th = cfg.thresholds
        tz = cfg.location.tz
        from datetime import datetime, timedelta, timezone

        base = datetime.combine(day, datetime.min.time(), tzinfo=tz) + timedelta(
            hours=start_hour
        )
        overrides = overrides or {}
        hours = []
        for offset in range(count):
            local = base + timedelta(hours=offset)
            fields = {
                "vpd": vpd,
                "et0": et0,
                "precip": precip,
                "prob": prob,
                "gust": gust,
                "wind": wind,
                "temp": temp,
                "rh": rh,
                "uv": uv,
            }
            fields.update(overrides.get(offset, {}))
            hours.append(
                Hour(
                    hour=local.astimezone(timezone.utc),
                    local=local,
                    temperature_c=fields["temp"],
                    humidity_pct=fields["rh"],
                    precip_mm=fields["precip"],
                    precip_prob_pct=fields["prob"],
                    wind_kmh=fields["wind"],
                    gust_kmh=fields["gust"],
                    uv_index=fields["uv"],
                    cloud_pct=10.0,
                    vpd_kpa=fields["vpd"],
                    et0_mm=fields["et0"],
                    is_day=th.day_start_hour <= local.hour < th.day_end_hour,
                    sources=("met_no", "open_meteo"),
                )
            )
        return hours

    return build