# Decisions

The choices worth explaining, and the reasoning at the time.

## No PostGIS

The spatial problem is "which handful of stations are near this point", over a
few hundred rows. A bounding box narrows the query so an index can serve it, and
haversine in Python does the exact filtering. That is faster to write, trivially
unit testable without a database, and one fewer extension to install. PostGIS
would earn its place at polygon intersections or millions of rows, neither of
which applies.

## APScheduler, not Celery

Four scheduled jobs, none long-running. Celery plus a broker would be two more
services to operate for no gain. The jobs are plain functions in
`app/jobs/tasks.py`, called identically from the scheduler, the CLI and tests,
so swapping the runner later touches one file.

The scheduler runs in its own container rather than inside the API process, so
an API restart never interrupts a training run and scaling the API out does not
fire every cron job twice.

## Two ground-truth providers behind one protocol

OpenAQ coverage is thin in Beijing and Chattogram; WAQI is much stronger there
but only exposes the latest reading. Rather than choosing, `MeasurementProvider`
is a protocol with two implementations, and the ingestion service does not know
which it has. Adding a third network is a new class and nothing else.

## WAQI publishes an index, not a concentration

`iaqi.pm25.v` is an **AQI value**. Storing it as micrograms would overstate
pollution roughly threefold in the moderate range and corrupt every training
label. It is converted back to a concentration at the provider boundary, so
nothing downstream has to know. `concentration_from_aqi` has a round-trip test.

## Open-Meteo returns wind in km/h

The default unit is km/h and the field is called `wind_speed_ms`. Left implicit,
every wind feature would be 3.6 times too large. The unit is now stated
explicitly in the request and pinned by a test. This was caught by calling the
real API, not by a mocked one.

## Chronological splits, never random

Adjacent hours at one station are near-duplicates. A random split puts copies of
the test rows into training and reports an accuracy the model cannot reproduce
in production. The holdout is the most recent slice of time, which is also the
only split that matches how the model is used.

## The features a forecast may use

A forecast issued at `t` for `t + h` may use observations up to `t`, and CAMS
and weather at `t + h` because those are themselves forecasts and genuinely
available at issue time. Anything else is leakage. Lag columns are built from
the observation series alone; ambient columns are joined at the target hour.
Three tests assert exactly this.

Two canaries guard against a leak reappearing: error must grow at least 1.15x
from one hour to twelve, and MAE must exceed 1 µg/m³. During development a
degenerate synthetic fixture produced an MAE of 0.08 that was flat across every
horizon, which is what a leak looks like.

## The panel is reindexed to a gap-free hourly grid

Lags are computed with `shift()`. Without reindexing, a six-hour outage turns a
one-hour lag into a six-hour lag still labelled as one hour. The panel is
reindexed per station first, so every lag is a true offset and a gap stays a gap.

## Predictions are stored before the outcome exists

The scorecard joins stored forecasts to observations that landed later. Scoring
by re-deriving predictions now, with hindsight, would produce much prettier
numbers and mean nothing.

## One accent colour, and the AQI scale owns meaning

Green through maroon is a published public-health standard that people read
without a legend, so interface chrome must never compete with it. The chrome
accent is a cool blue that appears nowhere in the AQI ramp: a blue element is
always a control, never a health signal.

## Two chart hues, not four

Four categorical hues cannot all be told apart under deuteranopia; green versus
pink collapsed at a colour-difference of 4.7 when checked. The chart therefore
uses two validated hues for the pair that carries the claim, Clearway against
CAMS, and draws persistence and climatology as neutral dashed lines with direct
labels. Both palettes pass the lightness band, chroma floor, all-pairs
colour-vision separation and contrast checks in light and dark.

## A stale reading is withheld, not shown

A six-hour-old value from 40 km away, presented as "now", is worse than
admitting uncertainty. Past the freshness window the nowcast falls back to CAMS
and labels itself `source: "cams"`. A station with no fresh reading appears on
the map hollow rather than green, because "we do not know" and "the air is
clean" must never look the same.
