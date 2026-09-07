# Model improvement plan

What was added in this pass, what it bought, and what to do next — ranked by
what it would actually buy this project rather than by how interesting it
sounds.

The frame throughout: ClearWay is **Model Output Statistics**. CAMS runs a
physical model on a 40 km global grid (11 km over Europe) and gets a station's
concentration wrong in a way that is *systematic*, and the job is to learn that
error. Every proposal below is judged on whether it gives the model information
about that error that it does not already have.

---

## 1. What was implemented

### 1.1 Spatial autocorrelation in the forecaster

Until now every feature described one station's **own past**: its lags, its
rolling statistics, its CAMS value, its weather. Nothing told the model what
the stations *around* it were doing. That is a real gap, because the CAMS error
field is spatially structured — an unresolved source (a kiln cluster, a port, a
stretch of stop-start traffic) biases every station near it at once, and no
amount of one station's own history reveals that.

Four new features, in `app/domain/spatial.py` (the maths) and
`app/ml/features.py` (`_spatial_features`, the orchestration):

| Feature | What it is |
|---|---|
| `spatial_bias_krige` | Ordinary kriging of the live `observed − CAMS` bias field, from **other** stations in the same city, evaluated at this station's own location |
| `spatial_bias_krige_var` | The kriging variance of that estimate — high when neighbours are few, far, or disagree |
| `spatial_neighbours` | How many stations actually fed it |
| `spatial_bias_clustering` | Moran's I of the same field, **centred on its own null expectation** (see below) |

Four decisions inside that are worth keeping:

**Leave-one-out.** A station's spatial features are built only from *other*
stations. A station cannot corroborate itself, and leave-one-out is also what
lets the same function serve the virtual-sensor interpolator the README
describes, which by definition never has its own reading.

**Kriging as a feature, not as a correction.** Classical regression kriging
fits a trend, krieges the residual, and *adds* the two. Here the kriged field
is handed to the gradient-boosted model as one more covariate, and the model
decides what it is worth — including learning to discount it when
`spatial_bias_krige_var` is large. That is strictly more flexible than a fixed
additive correction, and it fits the existing "one model, `horizon` as a
feature" design rather than bolting a second stage onto it.

**Moran's I is centred on chance, not on zero.** Under pure spatial randomness
Moran's I averages `−1/(n−1)`, which for the three or four stations a city
typically has reporting is nowhere near zero — it is −0.5 at n=3. A raw-I
feature would therefore move every time a sensor dropped in or out, for reasons
that have nothing to do with the air. Subtracting the null expectation is what
makes "0 means spatially random" true regardless of how many stations reported.

**The features are shifted by one hour**, like every other observation-derived
feature. This is operational, not theoretical: station data for the current
hour has usually not landed when the hourly job runs, so a feature built on `t`
would be dense in training (where the backfill is complete) and sparse in
production. That is the classic way a feature quietly stops meaning the same
thing at serving time.

**Serving had to change to match.** A station used to be forecast from a panel
containing only itself. `ForecastService` now builds **one panel per city**,
holding every station in it, and forecasts each station from that. Without this
the spatial features would have been served as missing while the model expected
them — and the failure would have been silent degradation, not an error.
`tests/test_services.py::test_the_hourly_run_serves_the_features_the_model_was_trained_on`
is the guard.

#### Does it work?

Measured, not asserted. Same estimator, same rows, same chronological split,
with and without the four columns, on synthetic data in two regimes:

| Regime | Holdout MAE without | with | Change |
|---|---|---|---|
| **Spatially structured** bias field (an unresolved source, drifting) | 8.08 | **7.04** | **−12.9%** |
| **Unstructured** — each station's bias is independent noise | 7.64 | 7.79 | +1.9% |

Per horizon in the structured regime: −13.5% at h=1, −14.4% at h=6, −10.5% at
h=12. The gain does not decay much with horizon, which is what you would expect
from a bias signal that persists rather than a concentration signal that
disperses.

The +1.9% in the unstructured regime is the honest other half: these are four
extra columns, and where there is no spatial signal to find they cost a little.
Two things make that acceptable. First, real PM2.5 error fields *are* spatially
correlated — that is the entire premise, and it is well supported in the
land-use-regression and universal-kriging literature. Second, the promotion
gate already protects production: a candidate is promoted only if it beats
every baseline and does not regress on the incumbent, so a deployment where the
spatial block does not pay simply keeps serving the incumbent.

`tests/test_ml.py::test_the_spatial_block_earns_its_place_when_the_bias_field_is_structured`
pins the structured-regime claim at ≥5% so it cannot silently rot.

#### Known limits of what was built

1. **The variogram range is fixed at 15 km, not fitted.** An isotropic
   exponential covariance with a fixed range is a standard sparse-network
   simplification, and a city with four stations reporting cannot support a
   stable per-hour variogram fit. But it is a simplification, and item 2.1
   below is how to remove it.
2. **Isotropic.** Real dispersion is not: a station downwind of a source shares
   its bias, a station crosswind does not. Item 2.2.
3. **The spatial snapshot is a single hour**, with no temporal covariance in
   the kriging. Space-time kriging is the principled version.
4. **Cost.** The panel-wide loop is O(hours × stations) per city with a small
   linear solve inside. At the current scale (single-digit stations per city,
   90-day windows) this is seconds, and training still fits the "cheap" claim.
   At hundreds of stations per city it would need batching.

### 1.2 The UI showed the wrong clock

Every timestamp the API returns is UTC. Every timestamp on screen was rendered
with `Intl` defaults, meaning **the zone of whatever machine rendered it** —
so a user in Dhaka reading a Berlin forecast saw Berlin's air on Dhaka's clock,
and a server rendering in UTC showed UTC to everyone. "The air is clear at
04:00" is only actionable if 04:00 means 04:00 *there*.

The zone data already existed and was being thrown away: `PlaceSchema` carried
`timezone` from Open-Meteo geocoding, `Station` stored one from its upstream
network, and `format.ts` already accepted an optional `timeZone` argument that
no caller ever passed.

- `City` (config) now carries an IANA zone; `CitySchema` and `StationSchema`
  expose one.
- `Location` (the picked place) carries `timezone`, set from the city button,
  the search result, or the clicked station — falling back to the currently
  selected zone when a network does not report one, since a station a few km
  away is in the same zone.
- Every hour rendered in the forecast chart, its table, its tooltip, and the
  guidance panel is now formatted in that zone, and both panels say which zone
  they are in (`Times in local time (GMT+6)`) so a bare "04:00" is never
  ambiguous.
- Half-hour zones (`Asia/Kolkata`) and daylight saving (`Europe/London` in
  summer) are handled, because the offset is resolved against the real instant.

The one place left in the viewer's own zone is the model page's "trained on"
date, deliberately: that is a fact about the model, not about a place.

---

## 2. What to do next

Ranked by payoff per unit of effort **for this codebase**, which is not the
same as ranked by novelty. Effort is calibrated against the project as it
stands: small, CPU-only, free data sources, trains in seconds.

### 2.1 Fit the variogram instead of assuming it — *small effort, direct payoff*

The one loose end in what was just built. Pool `observed − CAMS` residual pairs
across the whole training window per city, bin by distance, fit an exponential
or spherical model by least squares, and store the fitted range and nugget in
the model artefact so training and serving use the same one. Pooling over time
is what makes the fit stable where a single hour cannot.

This turns "we assumed 15 km" into "we measured the decorrelation length of the
CAMS bias field in each city, and here it is" — which is a *result*, and a
short one to report.

### 2.2 Wind-aware anisotropy — *medium effort, high novelty, the best paper angle*

Kriging distance is currently great-circle distance. Pollution does not travel
that way: it travels downwind. Replace the isotropic distance with an
anisotropic one that stretches the covariance along the wind vector — the wind
components are already features (`wind_u`, `wind_v`), so no new data is needed.

This is where the genuinely novel contribution is. Spatial kriging of air
quality is well established, gradient-boosted CAMS post-processing is well
established, and the two are mostly separate literatures (see §3). A
**wind-oriented, uncertainty-carrying kriged bias field used as a covariate in
temporal MOS at 1–24 h horizons** is a specific, defensible, and testable
combination that is thin on the ground.

### 2.3 Calibrated intervals via conformal prediction — *small effort, real result*

The 80% band is produced by two quantile models and is checked, but nothing
*guarantees* it. Conformalized quantile regression wraps exactly this setup and
yields finite-sample coverage guarantees without distributional assumptions.

This project is unusually well placed for it: the daily scoring job already
measures empirical coverage per horizon on predictions written before the
outcome existed. That is precisely the calibration set conformal prediction
needs, so the natural version here is a **rolling recalibration driven by the
live scorecard** — recompute the conformity correction per horizon each night.
Cheap, uses infrastructure that already exists, and "operational conformal MOS
with rolling recalibration, evaluated on true out-of-sample forecasts" is a
publishable result in its own right.

### 2.4 Meteorological ensemble spread as an uncertainty feature — *small effort*

Open-Meteo's Ensemble API exposes ensemble **mean and spread** for weather,
free, with means and spreads retained far longer than individual members. Note
what is *not* available: the air-quality endpoint is deterministic CAMS
(11 km Europe / 45 km global), so there is no CAMS ensemble spread to be had
from this source. The feasible version is therefore *meteorological* spread —
how uncertain is the weather that will drive dispersion — as a feature for both
the median and the quantile models.

Expect this to help the **intervals** more than the point forecast, which is
the right thing to target next given §2.3.

### 2.5 Weather-driven vs emission-driven decomposition — *medium effort, high interpretability*

Split each station's series into slow and fast components with a
Kolmogorov–Zurbenko filter (iterated moving average — no new dependency) and
feed both. The slow component tracks the emission/synoptic regime; the fast one
tracks weather-driven variation.

Pairs naturally with the spatial block: spatial answers *where* the error is,
the decomposition answers *why now*. It also unlocks a question a user actually
asks — "is today bad because of the weather or because of something burning?" —
which no current feature can answer.

### 2.6 Peak-conditioned scoring, then peak-conditioned modelling — *small then medium*

MAE is dominated by ordinary days, and the hours that matter are the bad ones.
**First** add peak-conditioned metrics to the scorecard (MAE restricted to
hours where truth exceeded a threshold, plus hit rate and false-alarm rate for
threshold exceedance). Only if that shows a real weakness is a two-stage
classifier-then-regressor worth its complexity. Measure before modelling —
otherwise you build a specialist for a problem you have not shown you have.

### 2.7 Satellite AOD and emission proxies — *large effort, real payoff, do last of the data work*

Aerosol optical depth would give genuine spatial coverage between stations, and
an emissions proxy (EDGAR, or road/industry density from OpenStreetMap) would
give the model a *causal* hint about where pollution is produced rather than
only where it is measured.

Ranked below the rest not because it is unpromising but because it is the only
item that adds a new ingestion pipeline, new provider credentials, cloud-gap
handling, and retrieval-bias handling — real work, and none of it shared with
anything else. The static emission proxy is much cheaper than the satellite
feed and can be done first as its own step.

### 2.8 Things deliberately **not** recommended

| Proposal | Verdict | Why |
|---|---|---|
| **Spatiotemporal GNN** | No | The literature's own finding is that sparse networks are where GNNs struggle, and a graph of 3–10 nodes per city is sparse. Reported GNN gains over classical baselines are in the ~7% MAE range; the kriging block above bought ~13% in the regime it targets, on a CPU, in seconds, with four columns. This would be days of work, a heavy dependency, and a likely loss |
| **Bayesian model averaging over 4+ learners** | Not yet | Real but modest gains, against a stated design principle ("two models, both small") and four artefacts to train, ship, version and explain. Revisit only if a single model plateaus |
| **Quantile Regression Forests** | Fold in | Worth running as an *ablation* inside §2.3 rather than as its own workstream. Conformal calibration is the cheaper route to the same goal — reliable intervals — and does not need a new dependency |
| **SHAP-guided feature reduction** | As analysis, not architecture | Worth computing for the paper's interpretability section (where do the spatial features actually rank?). As a performance lever it is weak here: gradient boosting with L2 regularisation and early stopping already tolerates redundant columns |

---

## 3. Where this sits in the literature

Enough to write an honest related-work section, and to avoid overclaiming.

**Established, and not novel:** kriging and regression kriging for air-quality
*mapping* — spatio-temporal regression kriging for urban NO₂
([Eindhoven](https://www.tandfonline.com/doi/full/10.1080/13658816.2019.1667501)),
[regression kriging for Japanese air pollutants](https://link.springer.com/article/10.4209/aaqr.2014.01.0011),
[universal kriging with land-use regression for national PM2.5/NO₂](https://arxiv.org/pdf/1808.09126),
and [hybrid kriging-LUR with XGBoost](https://pmc.ncbi.nlm.nih.gov/articles/PMC7579284/).

**Also established, and also not novel:** machine-learning MOS on CAMS —
[MOS applied to CAMS O₃ forecasts](https://acp.copernicus.org/articles/22/11603/2022/),
[improving the European CAMS forecast with ML](https://acp.copernicus.org/articles/23/5317/2023/acp-23-5317-2023-discussion.html),
and ECMWF's own [aq-biascorrection](https://github.com/ECMWFCode4Earth/aq-biascorrection).
Gradient boosting is one of the standard methods in that line of work.

**The gap:** those two literatures barely meet. Kriging is used to interpolate
*across space at one time*, or to downscale; CAMS MOS post-processing is
overwhelmingly done *per station, independently, in time*. Related work exists
at the edges — [kriging-based pseudo-label generation for PM2.5](https://arxiv.org/pdf/2401.08061)
uses kriging to manufacture training targets, and
[area-to-point kriging of regression residuals](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11333796)
downscales error fields — but a *leave-one-out kriged bias field, carrying its
own kriging variance and a chance-corrected spatial-autocorrelation statistic,
used as covariates in an hourly 1–24 h MOS model* is a combination that is
thinly covered.

So the honest claim is: **novel in combination and in operational framing, not
in its parts.** That is a publishable claim if — and only if — it comes with
the ablation, real stations, and multiple cities.

**For the uncertainty half of the story:**
[conformalized quantile regression](https://papers.neurips.cc/paper/8613-conformalized-quantile-regression.pdf)
is the method for §2.3, and there is a
[broad review of ML predictive-uncertainty estimation](https://arxiv.org/pdf/2209.08307)
worth citing.

**On GNNs, before anyone asks in review:** the
[multi-scale dynamic GNN work](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12677777/)
states the sparse-station problem plainly, and
[GCN-based fusion for air quality](https://arxiv.org/pdf/2105.13125)
reports gains in the region of 7% MAE over classical baselines. Cite both, and
say why a 3–10 node graph is the wrong tool.

---

## 4. What a paper would actually need

The modelling is the easy half. What would make it publishable:

1. **Real stations, not synthetic.** The ablation above is on synthetic data
   with a known spatial structure — that proves the mechanism works, not that
   it helps in Dhaka. Rerun it on 60–90 days of real OpenAQ/WAQI history.
2. **Several cities, chosen for contrast.** Dense network vs sparse; flat vs
   basin; monsoon vs temperate. The interesting result is not one average
   number, it is **where the spatial block helps and where it does not** — and
   the honest finding may be "only above N stations within R km", which is
   itself the useful contribution for anyone deciding whether to build this.
3. **Per-horizon everything.** Averages hide the shape of the problem.
4. **All four baselines, per horizon** — persistence, climatology, raw CAMS,
   and the model without the spatial block. The last one is the ablation and it
   is the whole argument.
5. **Category-level metrics**, not only µg/m³. A user acts on the AQI band.
6. **Interval coverage**, before and after §2.3.
7. **Threats to validity, stated up front:** free-tier station networks are
   biased towards cities and towards places that can afford monitors; WAQI
   values are already-processed AQI-derived concentrations rather than raw
   measurements; a fixed variogram range is an assumption until §2.1 is done;
   and the leave-one-out design means results depend on station density in a
   way that a single-city study cannot separate from station *placement*.

**Plausible venues:** *Environmental Modelling & Software*, *Atmospheric
Environment*, or *Geoscientific Model Development* for the methods-and-code
framing (GMD in particular rewards the "here is the working, reproducible
system" angle, which is this project's actual strength). *Atmospheric Chemistry
and Physics* if the analysis leans on the physical interpretation of the bias
field rather than the engineering.

The project's real differentiator for a reviewer is not the estimator. It is
that the whole loop is honest and reproducible: predictions written before the
outcome exists, a daily scorecard that cannot be gamed, a promotion gate, four
baselines, and every data source open. That is rarer in this literature than
another gradient-boosted regressor, and it should be the paper's spine.
