"""Scheduler entry point.

Runs in its own container. Keeping it out of the API process means an API
restart or scale-out never interrupts a training run, and never fires the same
cron job twice.
"""

from __future__ import annotations

import logging
import signal
import threading

from app.core.config import get_settings
from app.core.logging import configure_logging

from .scheduler import build_scheduler

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    scheduler = build_scheduler()
    scheduler.start()
    logger.info(
        "scheduler started with jobs: %s",
        ", ".join(job.id for job in scheduler.get_jobs()),
    )

    stop = threading.Event()
    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, lambda *_: stop.set())

    stop.wait()
    logger.info("shutting down scheduler")
    scheduler.shutdown(wait=True)


if __name__ == "__main__":
    main()
