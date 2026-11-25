CREATE TABLE IF NOT EXISTS forecast_hourly (
  source text NOT NULL,             -- 'open_meteo' | 'met_no'
  target_hour timestamptz NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  temperature_c real, humidity_pct real, precip_mm real, precip_prob_pct real,
  wind_kmh real, gust_kmh real, uv_index real, cloud_pct real,
  vpd_kpa real, et0_mm real, radiation_wm2 real,
  PRIMARY KEY (source, target_hour, fetched_at)
);

CREATE TABLE IF NOT EXISTS forecast_daily_cptec (
  city_code int NOT NULL,
  day date NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  condicao text, condicao_desc text,
  tmin_c real, tmax_c real, indice_uv real,
  PRIMARY KEY (city_code, day, fetched_at)
);

CREATE TABLE IF NOT EXISTS hourly_score (
  hour timestamptz PRIMARY KEY,
  computed_at timestamptz NOT NULL DEFAULT now(),
  good boolean NOT NULL,
  dry_rate_mm_h real,
  vpd_kpa real,
  precip_prob_pct real,
  features jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS day_verdict (
  day date PRIMARY KEY,
  computed_at timestamptz NOT NULL DEFAULT now(),
  score int NOT NULL,
  label text NOT NULL,
  windows jsonb NOT NULL,
  cptec_lock boolean NOT NULL,
  owner_feedback text              -- 'sequei' | 'molhou' | 'nao_lavei', futuro
);

CREATE TABLE IF NOT EXISTS notifications (
  key text PRIMARY KEY,            -- 'report:2026-11-25' | 'rain:2026-11-25T14'
  sent_at timestamptz NOT NULL DEFAULT now(),
  kind text NOT NULL,
  payload jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fh_target ON forecast_hourly (target_hour);