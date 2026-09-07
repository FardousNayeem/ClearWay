# Updates

A record of what changed, why it needed changing, and how it was checked.
Forward-looking work lives in [`model-improvement-plan.md`](model-improvement-plan.md);
this file is the log of what has actually been done.

---

## 2026-09-07 — Spatial autocorrelation in the forecaster, and local time in the UI

Two changes, one to the model and one to the interface. Both were cases of
information the system already had and was throwing away.

### 1. The forecaster could not see sideways

**Before.** Every feature described one station's own past: its lags at
t-1 to t-24, its rolling mean and standard deviation, CAMS at the target hour,
weather at the target hour, the calendar. Stations were forecast in complete
isolation from each other. Serving matched: `ForecastService` built a panel
containing a single station and forecast from that.

That leaves a real gap. The whole premise of the project is that CAMS error is
**systematic rather than random** — a 40 km grid cell cannot see a junction, a
brick kiln or a street canyon. But systematic error is not only systematic in
*time*, it is systematic in *space*: an unresolved source biases every station
near it at once. A station's own history cannot reveal that, and nothing else
in the feature set was looking.

**After.** Four features describing what the neighbours are saying, computed
per city per hour:

| Feature | What it is |
|---|---|
| `spatial_bias_krige` | Ordinary kriging of the live `observed − CAMS` bias field, from **other** stations in the city, evaluated at this station's own location |
| `spatial_bias_krige_var` | The kriging variance of that estimate — high when neighbours are few, far, or disagreeing |
| `spatial_neighbours` | How many stations actually fed it |
| `spatial_bias_clustering` | Moran's I of the same field, centred on its own null expectation |

**How it works.** `app/domain/spatial.py` holds the geostatistics as pure
functions — an isotropic exponential covariance solved as an ordinary-kriging
system with a Lagrange multiplier, plus Moran's I. No pandas, no I/O, so it is
unit-testable without a database and reusable by the virtual-sensor
interpolator the README describes. `_spatial_features` in `app/ml/features.py`
does the orchestration: it builds each city's distance matrix once (station
coordinates do not change), then indexes into it per hour.

Four decisions inside it that carry weight:

- **Leave-one-out.** A station's spatial features are built only from *other*
  stations. A station cannot corroborate itself, and leave-one-out is also
  what will let the same code serve a virtual sensor, which never has its own
  reading to begin with.
- **Kriging as a feature, not as a correction.** Classical regression kriging
  fits a trend, krieges the residual and *adds* the two. Here the kriged field
  is handed to the gradient-boosted model as one more covariate and the model
  decides what it is worth — including learning to discount it when the
  variance is large. More flexible than a fixed additive correction, and it
  fits the existing "one model, `horizon` as a feature" design instead of
  bolting a second stage onto it.
- **Moran's I is centred on chance, not on zero.** Under spatial randomness
  Moran's I averages `−1/(n−1)`, which is −0.5 at three stations. A raw-I
  feature would therefore move every time a sensor dropped in or out, for
  reasons that have nothing to do with the air. This was caught by a test that
  failed on the first run, not by inspection.
- **Shifted by one hour**, like every other observation-derived feature. This
  is operational rather than theoretical: station data for the current hour
  has usually not landed when the hourly job runs, so a feature built on `t`
  would be dense in training, where the backfill is complete, and sparse in
  production — the classic way a feature quietly stops meaning the same thing
  at serving time.

**Serving had to change to match.** `ForecastService` now builds **one panel
per city**, holding every station in it, and forecasts each station from that
shared panel — one query and one panel per city rather than either per station,
so the hourly job did not get more expensive. Without this change the spatial
features would have been served as missing while the model expected them, and
the symptom would have been silent degradation rather than an error.

**Does it work?** Measured, not asserted: the same estimator, the same rows,
the same chronological split, with and without the four columns.

| Regime | MAE without | with | Change |
|---|---|---|---|
| Spatially structured bias field | 8.08 | **7.04** | **−12.9%** |
| Unstructured (each station's bias independent) | 7.64 | 7.79 | +1.9% |

Per horizon in the structured regime: −13.5% at h=1, −14.4% at h=6, −10.5% at
h=12. The gain barely decays with horizon, which is what a persistent bias
signal should look like, as opposed to a concentration signal that disperses.

The +1.9% is the other half of the answer and is stated rather than buried:
four extra columns are not free where there is no spatial signal to find. What
makes that acceptable is that the promotion gate already exists — a candidate
serves only if it beats every baseline and does not regress on the incumbent.

**Files.** `app/domain/spatial.py` (new), `app/ml/features.py`,
`app/services/forecasting.py`, `tests/test_spatial.py` (new),
`tests/test_ml.py`, `tests/test_services.py`.

### 2. The UI showed the wrong clock

**Before.** Every timestamp the API returns is UTC, and every timestamp on
screen was rendered with `Intl` defaults — meaning the zone of whatever machine
rendered it. A server rendering in UTC showed UTC to everyone; a browser in
Dhaka showed a Berlin forecast on Dhaka's clock. Nothing on the page said which
zone the numbers were in.

For a nowcast that is untidy. For guidance it is a defect: "the air is clear
between 04:00 and 07:00" is only actionable if 04:00 means 04:00 *there*.

**After.** Every hour rendered for a place is rendered in that place's zone,
and both panels say which zone that is (`Times in local time (GMT+6)`).

The data was already there and was being discarded at every step:
`PlaceSchema` already carried a `timezone` from Open-Meteo geocoding, `Station`
already stored one from its upstream network, and `format.ts` already accepted
an optional `timeZone` argument that **no caller ever passed**. So the change
is mostly plumbing:

- `City` (config) now carries an IANA zone, and `CitySchema` and
  `StationSchema` expose one.
- `Location` — the picked place — carries `timezone`, set from the city button,
  the search result, or the clicked station. A station whose network does not
  report a zone falls back to the zone already selected, since a station a few
  km away is in the same zone.
- The forecast chart, its table, its hover tooltip and the guidance panel all
  format against it.
- Half-hour zones (`Asia/Kolkata`) and daylight saving (`Europe/London` in
  summer) are correct, because the offset is resolved against the real instant
  rather than assumed.

The model page's "trained on" date is deliberately left in the viewer's own
zone: that is a fact about a model, not about a place.

**Files.** `app/core/config.py`, `app/schemas/common.py`,
`app/api/v1/stations.py`, `tests/test_api.py`, `web/src/lib/format.ts`,
`web/src/lib/types.ts`, `web/src/components/air/{AirDashboard,PlaceSearch,ForecastChart,GuidancePanel}.tsx`.

### 3. Research and roadmap

[`model-improvement-plan.md`](model-improvement-plan.md) is new: a ranked
roadmap with the literature positioning behind it. The short version — fit the
variogram rather than assuming 15 km; make the kriging wind-aware and
anisotropic, which is the strongest novelty angle and needs no new data;
calibrate the prediction intervals with conformal prediction driven by the
scorecard that already exists.

It also records what was considered and **rejected**, with reasons: a
spatiotemporal GNN (a 3–10 node graph is exactly where the GNN literature
reports the method struggling), Bayesian model averaging over several learners
(modest gains against the project's stated preference for two small models),
and CAMS ensemble spread (Open-Meteo's air-quality endpoint is deterministic;
only *meteorological* ensemble spread is actually available, which is a
different and cheaper feature).

On novelty, the plan is deliberately unflattering: kriging for air-quality
mapping is well established, gradient-boosted CAMS post-processing is well
established, and the contribution here is that those two literatures rarely
meet. **Novel in combination and in operational framing, not in its parts.**

### 4. GitHub Actions removed

`.github/workflows/ci.yml` and the `.github` folder are gone. Two of the checks
that lived only there were worth keeping, so they moved rather than
disappearing:

- **The migration drift check** is now `make drift`, and `make check` runs it.
  It applies the migrations and asks Alembic to autogenerate one; any operation
  in the result means a model change is missing a migration, and the target
  fails. This is a production-breaking class of bug and it should not depend on
  a hosted runner to be caught.
- **The image builds** are proved by `make up`, or by building the two
  Dockerfiles directly.

`make check` is now the single command that runs everything: ruff, mypy, the
drift check, pytest with coverage, `tsc`, and `next lint`.

---

## What this does *not* yet do

Stated here so nobody has to discover it later.

1. **The ablation is on synthetic data.** It proves the mechanism works when
   the structure it targets is present. It is not yet evidence that it helps in
   Dhaka, which needs an API key and 60–90 days of real backfill. That is the
   first thing to run.
2. **The variogram range is fixed at 15 km**, not fitted. Standard practice for
   sparse networks, and a real limitation. §2.1 of the plan removes it.
3. **The kriging is isotropic.** Real dispersion is not — a station downwind of
   a source shares its bias, a station crosswind does not. §2.2 of the plan.
4. **Neighbours are scoped to the configured city.** Two stations either side
   of a city boundary cannot see each other even if they are 5 km apart.
5. **A spatial snapshot is a single hour**, with no temporal covariance in the
   kriging. Space-time kriging is the principled version.
6. Everything else in the plan's §2 is documented, not built.

## Getting the benefit

The new features only take effect for a **newly trained** model:

```bash
make bootstrap          # or: python -m app.cli train
```

An existing artefact keeps serving without error — the predictor selects the
columns its own `feature_names` list asks for, and the live pipeline now
produces a superset of them. So this is backward compatible, but an old model
simply will not use the new information until it is retrained. The weekly
retrain will pick it up on its own; the promotion gate decides whether the
candidate deserves to serve.

## Verification

Everything `make check` runs, against Postgres 17:

| Check | Result |
|---|---|
| `ruff check .` | passes |
| `mypy app` | passes, 56 files |
| `alembic upgrade head` | applies cleanly |
| `make drift` | no missing migration — no schema changed |
| `pytest --cov=app --cov-fail-under=70` | **144 passed**, 77% coverage |
| `npm run typecheck` / `lint` / `build` | all pass, all 5 pages prerender |

21 of those 144 tests are new. The ones worth knowing about:

- `test_the_spatial_block_earns_its_place_when_the_bias_field_is_structured` —
  trains twice, with and without, and fails if the spatial columns buy less
  than 5%. The headline claim cannot silently rot.
- `test_a_station_gets_its_neighbours_bias_not_its_own` — the leave-one-out
  property, on a fixture where one station is wildly biased and the rest are
  not.
- `test_spatial_features_come_from_the_hour_before_the_issue_hour` — the
  leakage rule, extended to neighbours.
- `test_the_hourly_run_serves_the_features_the_model_was_trained_on` — trains a
  model, runs the real hourly job against a seeded city, and would catch a live
  panel built one station at a time.
- `test_a_forecast_is_still_issued_when_the_neighbours_go_quiet` — spatial
  features are an enrichment, not a dependency.
- `test_every_city_carries_the_zone_its_hours_should_be_shown_in` — every
  configured city has a real IANA zone, checked against `zoneinfo`.
