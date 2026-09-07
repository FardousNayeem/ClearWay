# Clearway - plan

Air quality nowcast, forecast and exposure guidance, built on open data.

---

## 1. The question the project answers

CAMS, the European Copernicus atmospheric model, already forecasts PM2.5 everywhere
on earth. It is a physics model on a 40 km global grid (11 km over Europe). At that
resolution it cannot see a specific junction, a brick kiln, or a street canyon, so
its output is systematically wrong in ways that are *consistent per location*.

> **Can a cheap gradient-boosted model, given CAMS output plus local weather plus a
> station's own history, beat CAMS at that station?**

This is a known technique with a name: **Model Output Statistics**, the statistical
bias-correction layer that national weather services run on top of physical models.
Framing it that way matters, because it means the project is not "I trained a
regressor on a CSV" but "I implemented the standard operational post-processing
pattern and measured whether it worked".

It also keeps training cheap, which is a hard requirement here. No deep learning, no
GPU, no multi-hour runs.

## 2. Scope

### In

| Capability | What the user sees |
|---|---|
| **Nowcast** | Current PM2.5 and US AQI for a coordinate, from the nearest stations |
| **Forecast** | Next 24 hours, hourly, with an honest uncertainty band |
| **Virtual sensor** | An estimate for places with no monitoring station |
| **Best time to go out** | The cleanest hours in the next 24, given a chosen sensitivity |
| **Scorecard** | How every model version has performed against actuals, per horizon |

### Out, deliberately

- **Turn-by-turn exposure routing.** It needs a routing engine (a fourth external
  dependency) and the value over "best time to go out" is small. Recorded as v2.
- Deep learning. Tree ensembles reliably win on this problem at this data volume,
  and reaching for a transformer here would be a judgment error, not a flex.
- User accounts, notifications, mobile app, pollutants beyond PM2.5 and PM10.
- Global coverage. A configured list of cities keeps the database small and the
  ingestion inside free rate limits.

### The self-improving loop, concretely

1. Every hour, a forecast for t+1 … t+24 is written to `forecasts` with the model
   version that produced it.
2. Every hour, real station measurements arrive and are written to `measurements`.
3. Once a day, a scoring job joins forecasts to the actuals that have since landed
   and writes MAE, RMSE and bias per horizon per model version to `model_scores`.
4. Once a week, a retrain job trains a candidate on the enlarged dataset. It is
   promoted **only if it beats the incumbent** on held-out data.

The loop is honest because the ground truth arrives on its own. Nothing depends on a
user clicking "was this useful?".

## 3. Data sources

| Source | Role | Auth | Licence |
|---|---|---|---|
| **OpenAQ v3** | Ground truth. Station measurements, the labels | API key in `X-API-Key` | Open |
| **Open-Meteo Air Quality** | CAMS PM2.5/PM10 forecast. Baseline *and* feature | None | CC BY 4.0 |
| **Open-Meteo Forecast + Archive** | Meteorology. Wind, temperature, humidity, precipitation, boundary layer | None | CC BY 4.0 |

Only OpenAQ needs a key, and the app degrades to a CAMS-only mode without one, so a
reviewer can run it with zero signups.

Attribution for Open-Meteo is required by CC BY 4.0 and appears in the UI footer.

## 4. The models

Two, both small.

### 4.1 Forecaster - bias-corrected PM2.5, horizons 1 to 24 hours

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
  - `horizon` itself
- **Uncertainty:** two extra quantile models (10th and 90th percentile) using the
  same estimator with `loss="quantile"`. A forecast without a band is a forecast that
  cannot be trusted.

### 4.2 Interpolator - the virtual sensor

Estimates PM2.5 where no station exists.

- **Honest evaluation:** leave-one-station-out. Train to predict a station's value
  using *only other stations* plus CAMS plus weather, then test on the held-out
  station. This measures exactly the thing the feature claims to do.
- **Features:** inverse-distance-weighted values of the k nearest other stations,
  distance to each, CAMS at the target point, weather, elevation.
- **Baseline to beat:** plain inverse-distance weighting, and raw CAMS.

## 5. Baselines - the part that makes the numbers mean something

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

## 6. Architecture

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

    domain/   pure functions. AQI maths, geo maths, time features.
              Imports nothing from the layers above. Fully unit testable.

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

### Layout

```
backend/app/
  main.py             application factory, middleware, router mounting
  core/               config, logging, errors, database session, DI helpers
  domain/             aqi.py, geo.py, timeframes.py   (pure)
  db/                 models.py                        (SQLAlchemy)
  schemas/            pydantic request and response DTOs
  repositories/       stations, measurements, forecasts, scores
  services/           nowcast, forecast, ingestion, scoring, guidance
  providers/          openaq.py, openmeteo.py, base.py
  ml/                 features.py, baselines.py, trainer.py, registry.py,
                      predictor.py, evaluation.py
  jobs/               scheduler.py, tasks.py
  api/v1/             router.py + one module per resource
tests/                unit tests for domain and ml, API tests with a test database
```

### Choices, and why

| Decision | Reason |
|---|---|
| **PostgreSQL, no PostGIS** | The spatial work is "nearest few stations" over hundreds of rows. Haversine in Python is faster to write, faster to test, and one fewer extension to install |
| **APScheduler in-process, not Celery** | Four scheduled jobs, none long-running. Celery plus Redis would be two extra services for no gain |
| **Alembic migrations** | The database outlives the code. `create_all` is fine for a demo and wrong for anything else |
| **Model artefacts on disk, versioned in the database** | A `model_versions` row records metrics, feature list, training window and file path. The scorecard reads from there |
| **`httpx` with explicit timeouts and retries** | Both upstreams are free services. Failing gracefully is part of the design, not an afterthought |

## 7. API surface

```
GET  /healthz                          liveness plus a database round trip
GET  /api/v1/cities                    configured cities
GET  /api/v1/stations?city=&bbox=      stations with their latest reading
GET  /api/v1/air/now?lat=&lon=         nowcast at a point, with provenance
GET  /api/v1/air/forecast?lat=&lon=    24 hours, with the 80% band and CAMS alongside
GET  /api/v1/air/grid?bbox=&res=       virtual-sensor grid for the heatmap
GET  /api/v1/guidance/best-hours?...   cleanest hours, given a sensitivity profile
GET  /api/v1/models                    versions, training windows, status
GET  /api/v1/models/scores?...         MAE/RMSE per horizon per version, vs baselines
```

Every response that contains an estimate also carries **provenance**: which stations
contributed, how far away they were, how old the readings are, and which model
version produced it. A number with no provenance is not trustworthy.

## 8. Frontend

Next.js 15, App Router, TypeScript, Tailwind v4. Same design discipline as before:
tokens in CSS custom properties, one accent, light and dark from one palette, charts
validated for colour-vision separation.

| Route | Rendering | Content |
|---|---|---|
| `/` | Static shell, client data | Map, current AQI, 24-hour forecast, best hours |
| `/city/[slug]` | Static per configured city, revalidated | Station list, trends |
| `/model` | Static shell, client data | The scorecard: version history, per-horizon error, us against the three baselines |

`/model` is the portfolio page. It is where the loop is proved rather than claimed.

## 9. Build order

1. Scaffold, config, logging, error envelope, database session
2. `domain/`: AQI (2024 EPA breakpoints), haversine, cyclical time features, with tests
3. Tables and migrations
4. Providers: OpenAQ and Open-Meteo clients, with recorded fixtures for tests
5. Repositories and the backfill command
6. Feature builder and the three baselines
7. Trainer, registry, predictor
8. Services and the API
9. Scheduler and the scoring job
10. Frontend
11. Docker, CI, README

## 10. Definition of done

- The scorecard shows our model beating persistence, climatology and raw CAMS on MAE
  at horizons 6 through 24, **or** the README states plainly where it does not and why.
- The 80% band achieves close to 80% empirical coverage.
- A cold start works: clone, add an OpenAQ key, run one command, and the app
  backfills, trains and serves.
- The app still functions with no OpenAQ key, in CAMS-only mode.
- Domain logic has unit tests that touch neither network nor database.
- `make check` runs lint, types and tests for both halves.

## 11. Known risks

| Risk | Mitigation |
|---|---|
| Persistence beats us at 1-3 hours | Expected, and stated up front. The value is at 6-24 hours |
| Station data is sparse or stale in some cities | Provenance in every response; the UI shows reading age and degrades to CAMS |
| OpenAQ rate limits during backfill | Throttled client, resumable backfill, generous caching |
| Scope creep into routing | Recorded as v2 in this document and not started |
