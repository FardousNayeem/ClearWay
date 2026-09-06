"""In-process scheduling.

Four jobs, none long-running, so APScheduler inside the API process is the
right size. Celery plus a broker would be two more services to operate for no
gain, and this can be swapped for one later without touching ``tasks``.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import tasks

logger = logging.getLogger(__name__)


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")

    # Ingest a few minutes past the hour, once upstreams have published.
    scheduler.add_job(
        tasks.ingest_recent,
        CronTrigger(minute=7),
        id="ingest_recent",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    # Forecast after ingestion, so it uses the freshest observation.
    scheduler.add_job(
        tasks.run_forecasts,
        CronTrigger(minute=20),
        id="run_forecasts",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    # Score yesterday once it is safely complete in every timezone.
    scheduler.add_job(
        tasks.score_yesterday,
        CronTrigger(hour=3, minute=0),
        id="score_yesterday",
        max_instances=1,
        coalesce=True,
    )
    # Retrain weekly on the enlarged dataset.
    scheduler.add_job(
        tasks.retrain,
        CronTrigger(day_of_week="sun", hour=4, minute=0),
        id="retrain",
        max_instances=1,
        coalesce=True,
    )
    # Re-discover stations occasionally; networks add and remove sites.
    scheduler.add_job(
        tasks.discover_stations,
        CronTrigger(day_of_week="mon", hour=2, minute=0),
        id="discover_stations",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
