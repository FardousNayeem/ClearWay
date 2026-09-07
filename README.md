# ClearWay

Air quality you can act on. ClearWay tells you when it's safe to go outside with air quality you can trust. A PM2.5 nowcast built from the monitoring stations
nearest you, a 24-hour forecast that corrects the global physics model using
what those stations actually recorded, and a scorecard that grades itself every
day against what happened. Every data source is open.

FastAPI + PostgreSQL + scikit-learn behind a statically generated Next.js site.


## The question it answers

CAMS, the Copernicus atmospheric model, already forecasts PM2.5 for the whole
planet. It runs on a 40 km global grid, 11 km over Europe. At that resolution it
cannot see a junction, a brick kiln or a street canyon, so its error at any one
monitoring station is **systematic rather than random**.

> Can a cheap gradient-boosted model, given CAMS output plus local weather plus
> a station's own history, beat CAMS at that station?

That is **Model Output Statistics**: the statistical post-processing layer
national weather services run on top of physical models. Framing it that way is
the difference between "I trained a regressor on a CSV" and "I implemented the
standard operational pattern and measured whether it worked".

## What it does

- **Nowcast** at any coordinate, inverse-distance weighted from nearby fresh
  station readings. Every response says which stations contributed, how far
  away and how old they were.
- **24-hour forecast** with an 80% interval, shown alongside raw CAMS so the
  correction is visible.
- **Virtual sensor**: an estimate for places with no monitor of their own.
- **Guidance**: the cleanest hours ahead and any run of consecutive clear
  hours, against a threshold chosen for your sensitivity. Every hour on screen
  is in the **local time of the place you picked**, labelled with its offset —
  "clear at 04:00" is only actionable if 04:00 means 04:00 there.
- **Scorecard**: how every model version has performed against three baselines,
  per forecast horizon, on predictions written before the outcome existed.

## The models

Two, both small.

### Forecaster - bias-corrected PM2.5, horizons 1 to 24 hours

- **Estimator:** `HistGradientBoostingRegressor` (scikit-learn). Trains in seconds on
  CPU, handles missing values natively, which matters because station data has gaps.
- **Design:** one model, with `horizon` as a feature, rather than 24 separate models.
  Simpler to train, ship and reason about.
- **Target:** observed PM2.5 at `t + horizon`.
- **Features:**
  - CAMS forecast for the target hour (the physical prior)
  - Station lags at t, t-1, t-2, t-3, t-6, t-12, t-24
  - Rolling mean and standard deviation over 6 and 24 hours
  - Weather at the target hour: temperature, humidity, wind speed and direction
    (as sin/cos), precipitation, pressure, boundary-layer height
  - Calendar: hour of day and day of week, both encoded cyclically
  - Station identity as a categorical
  - **What the neighbouring stations are saying** — see below
  - `horizon` itself
- **Spatial autocorrelation:** every feature above describes one station's own
  past. Four more describe the stations *around* it, because the CAMS error
  field is spatially structured: an unresolved source biases every station near
  it at once, and no amount of one station's history reveals that.
  - `spatial_bias_krige` — ordinary kriging of the live `observed − CAMS` bias
    field from **other** stations in the city, evaluated at this station's own
    location. Leave-one-out: a station cannot corroborate itself
  - `spatial_bias_krige_var` — the kriging variance, so the model can learn to
    discount the estimate when neighbours are few, far, or disagreeing
  - `spatial_neighbours` — how many stations fed it
  - `spatial_bias_clustering` — Moran's I of that field, centred on its own
    null expectation of `−1/(n−1)`, so "0 means spatially random" stays true
    whether three stations reported or ten

  Worth **−12.9% MAE** on a holdout where the bias field is spatially
  structured, and about +1.9% where it is pure noise. Both numbers, and the
  ablation that produced them, are in
  [`docs/model-improvement-plan.md`](docs/model-improvement-plan.md).
- **Uncertainty:** two extra quantile models (10th and 90th percentile) using the
  same estimator with `loss="quantile"`. A forecast without a band is a forecast that
  cannot be trusted.

### Interpolator - the virtual sensor

Estimates PM2.5 where no station exists.

- **Honest evaluation:** leave-one-station-out. Train to predict a station's value
  using *only other stations* plus CAMS plus weather, then test on the held-out
  station. This measures exactly the thing the feature claims to do.
- **Features:** inverse-distance-weighted values of the k nearest other stations,
  distance to each, CAMS at the target point, weather, elevation.
- **Baseline to beat:** plain inverse-distance weighting, and raw CAMS.

## Baselines - the part that makes the numbers mean something

Every claim is measured against all four:

| Baseline | Why it is there |
|---|---|
| **Persistence** | "It will be what it is now." Brutally strong at 1-3 hours. Any model that cannot beat it at short horizons is not useful |
| **Climatology** | Hour-of-day mean for that station. Catches daily cycles for free |
| **Raw CAMS** | The physical model, unmodified. The thing being improved on |
| **Ours** | Must beat all three, per horizon, or the project reports that honestly |

Metrics: MAE, RMSE, bias, and for the quantile models, empirical coverage of the
80% band. Plus AQI-category accuracy, because a user acts on the category, not the
microgram value.

## Architecture

Layered, with a strict dependency direction. Nothing points back up.

```
        HTTP
         │
    ┌────▼─────┐
    │   api/   │  routers. Parse, authorise, delegate, serialise. No logic.
    └────┬─────┘
    ┌────▼─────┐
    │ services/│  use cases. Orchestration and transactions.
    └────┬─────┘
    ┌────▼──────────────┬──────────────┐
    │  repositories/    │  providers/  │  data access, and outbound HTTP
    └────┬──────────────┴──────────────┘
    ┌────▼─────┐
    │   db/    │  SQLAlchemy tables
    └──────────┘

    domain/   pure functions. AQI maths, geo maths, geostatistics, time
              features. Imports nothing from the layers above. Fully unit
              testable.

    ml/       features, training, registry, prediction, evaluation.
              Depends on domain, never on api.
```

**Rules enforced by review:**

1. A router never touches a repository directly, and never contains a rule.
2. `domain/` has no imports from `app.*` other than other `domain` modules. It is
   pure, so its tests need no database and no network.
3. Providers are the only place `httpx` appears. They return domain objects, never
   raw JSON, so a provider can be swapped without touching a service.
4. One error envelope for the whole API, produced by a single exception handler.
5. Configuration is read once, into a typed settings object. No `os.environ` reads
   scattered through the codebase.

## Data sources

| Source | Role | Key |
|---|---|---|
| [OpenAQ v3](https://openaq.org) | Ground truth, station observations | Free |
| [WAQI](https://aqicn.org/api/) | Second ground-truth network, far better Asian coverage | Free |
| [Open-Meteo Air Quality](https://open-meteo.com/en/docs/air-quality-api) | CAMS PM2.5. Baseline **and** feature | None |
| [Open-Meteo Forecast, Archive, Geocoding, Elevation](https://open-meteo.com) | Weather, place search, terrain | None |

With no key at all, ClearWay runs in **CAMS-only mode**: forecasts still work
and are labelled as uncorrected, but there is no ground truth to train on or to
score against. Clone it and it runs.

Open-Meteo data is CC BY 4.0 and attributed in the site footer.

## Running it

```bash
make db          # Postgres 17 on 127.0.0.1:5436
make setup       # both toolchains, both .env files
make migrate
make api         # http://127.0.0.1:8000/docs
make web         # http://localhost:3000
```

That gets you a working site immediately, in CAMS-only mode.

To turn the learning loop on, put an OpenAQ or WAQI key in `backend/.env`, then:

```bash
make bootstrap   # discover stations, backfill 60 days, train, forecast
make scheduler   # hourly ingest and forecast, daily scoring, weekly retrain
```

Or the whole stack in Docker:

```bash
docker compose up --build -d
docker compose exec api python -m app.cli bootstrap --days 60
```

## The self-improving loop

Ground truth arrives on its own, so nothing here depends on a user rating
anything, and the scorecard cannot be gamed.

1. **Hourly** a forecast for t+1 to t+24 is written, tagged with the model
   version that produced it, alongside the persistence and CAMS baselines for
   the same rows.
2. **Hourly** real station observations land.
3. **Daily** a scoring job joins the two and records MAE, RMSE, bias and
   interval coverage per horizon per estimator.
4. **Weekly** a candidate is trained on the enlarged dataset. It is promoted
   **only if it beats every baseline** on held-out data.

## Baselines, and why they matter

A mean absolute error of "12 µg/m³" says nothing. "12 against persistence's 19"
says everything.

| Baseline | Why it is there |
|---|---|
| **Persistence** | The last observed value. Brutally strong at one to three hours, because pollution is highly autocorrelated |
| **Climatology** | The station's own hour-of-day mean. Captures the daily cycle for free |
| **CAMS** | The physics model, unmodified. The thing being improved on |

Persistence is expected to win at short range. That is stated up front rather
than discovered later, and it is why the promotion gate looks at longer horizons.

## Where it goes next

[`docs/updates.md`](docs/updates.md) is the log of what has changed and why.
[`docs/model-improvement-plan.md`](docs/model-improvement-plan.md) has the
roadmap: fitting the variogram rather than assuming it, wind-aware anisotropic
kriging, conformal prediction intervals calibrated off the live scorecard, and
an honest account of which fashionable ideas are worth it here and which are
not — with the literature positioning for each.


