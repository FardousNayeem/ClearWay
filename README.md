# Clearway

Air quality you can act on. A PM2.5 nowcast built from the monitoring stations
nearest you, a 24-hour forecast that corrects the global physics model using
what those stations actually recorded, and a scorecard that grades itself every
day against what happened.

FastAPI + PostgreSQL + scikit-learn behind a statically generated Next.js site.
Every data source is open, and only one of them needs a key.

---

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
  hours, against a threshold chosen for your sensitivity.
- **Scorecard**: how every model version has performed against three baselines,
  per forecast horizon, on predictions written before the outcome existed.

## Data sources

| Source | Role | Key |
|---|---|---|
| [OpenAQ v3](https://openaq.org) | Ground truth, station observations | Free |
| [WAQI](https://aqicn.org/api/) | Second ground-truth network, far better Asian coverage | Free |
| [Open-Meteo Air Quality](https://open-meteo.com/en/docs/air-quality-api) | CAMS PM2.5. Baseline **and** feature | None |
| [Open-Meteo Forecast, Archive, Geocoding, Elevation](https://open-meteo.com) | Weather, place search, terrain | None |

With no key at all, Clearway runs in **CAMS-only mode**: forecasts still work
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

## Layout

```
backend/
  app/
    api/            routers. Parse, delegate, serialise. No rules
    services/       use cases: ingestion, nowcast, forecasting, guidance,
                    scoring, training
    repositories/   SQL, one aggregate each
    providers/      the only place httpx appears
    ml/             features, baselines, trainer, predictor, evaluation
    domain/         pure functions. AQI maths, geo maths, time features
    db/             tables
    jobs/           scheduler and its entry point
    cli.py          operator commands
  migrations/       Alembic
  tests/            105 tests
web/
  src/app/          two routes, both statically prerendered
  src/components/   ui primitives, then air / model / map features
  src/lib/          api client, error envelope, AQI tokens, formatters
```

The dependency rule: `api → services → repositories → db`, and `domain/`
imports nothing from the layers above it, which is why its tests need neither a
database nor a network.

## Checks

```bash
make check   # ruff, mypy, pytest with coverage, tsc, next lint
```

CI additionally proves migrations apply from empty, that no model change is
missing a migration, and that both container images build.

## Deliberate limits

- **No turn-by-turn exposure routing.** It needs a routing engine, a fourth
  external dependency, and adds little over "go out at 06:00 instead of 18:00".
  Recorded as v2.
- **No deep learning.** Tree ensembles win on this problem at this data volume,
  train in seconds on a CPU, and handle the gaps that station data is full of.
  A transformer here would cost hours and lose accuracy.
- **WAQI cannot backfill.** It exposes only the latest reading per station, so
  it improves coverage going forward but cannot fill history.
- **Seven cities.** Coverage is an explicit list so ingestion stays inside free
  rate limits and the database fits on a laptop. Place search still works
  anywhere in the world, falling back to CAMS.
