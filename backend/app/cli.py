"""Operator commands.

    python -m app.cli bootstrap --days 60

``bootstrap`` is the one that matters: on a clean database it discovers
stations, backfills history, trains a first model and promotes it, so a new
machine goes from empty to serving with a single command.
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.core.config import get_settings
from app.core.database import session_scope
from app.core.errors import ClearwayError
from app.core.logging import configure_logging
from app.jobs import tasks
from app.providers.factory import build_ambient_provider, build_measurement_providers
from app.repositories.measurements import MeasurementRepository
from app.services.ingestion import IngestionService
from app.services.training import TrainingService

logger = logging.getLogger("clearway.cli")


def cmd_discover(_: argparse.Namespace) -> None:
    tasks.discover_stations()


def cmd_ingest(_: argparse.Namespace) -> None:
    tasks.ingest_recent()


def cmd_backfill(args: argparse.Namespace) -> None:
    settings = get_settings()
    with session_scope() as session, build_ambient_provider(settings) as ambient:
        service = IngestionService(
            session, settings, build_measurement_providers(settings), ambient
        )
        logger.info("backfilling %s days of ambient data", args.days)
        ambient_report = service.backfill_ambient(args.days)
        logger.info("backfilling %s days of observations", args.days)
        measurement_report = service.backfill_measurements(args.days, args.city)
        logger.info(
            "backfill complete: %s observations, %s ambient rows, %s failures",
            measurement_report.measurements_written,
            ambient_report.ambient_written,
            len(measurement_report.failures) + len(ambient_report.failures),
        )


def cmd_train(args: argparse.Namespace) -> None:
    settings = get_settings()
    with session_scope() as session:
        version = TrainingService(session, settings).train_candidate(
            window_days=args.days, promote=not args.no_promote, force=args.force
        )
        overall = version.metrics.get("overall", {})
        print(f"\ntrained {version.name} v{version.version}  ({version.notes or 'candidate'})")
        print(f"  rows: {version.training_rows}")
        print(f"  {'estimator':<14}{'MAE':>8}{'RMSE':>8}{'bias':>8}")
        for name, metrics in sorted(overall.items(), key=lambda kv: kv[1]["mae"]):
            print(
                f"  {name:<14}{metrics['mae']:8.2f}{metrics['rmse']:8.2f}{metrics['bias']:+8.2f}"
            )


def cmd_forecast(_: argparse.Namespace) -> None:
    tasks.run_forecasts()


def cmd_score(_: argparse.Namespace) -> None:
    tasks.score_yesterday()


def cmd_status(_: argparse.Namespace) -> None:
    with session_scope() as session:
        rows = MeasurementRepository(session).coverage()
        print(f"  {'city':<12}{'provider':<9}{'station':<34}{'rows':>8}  window")
        for row in rows:
            window = (
                f"{row.first_at:%Y-%m-%d} to {row.last_at:%Y-%m-%d}" if row.first_at else "-"
            )
            print(
                f"  {row.city_slug:<12}{row.provider:<9}{row.name[:32]:<34}{row.rows:>8}  {window}"
            )


def cmd_bootstrap(args: argparse.Namespace) -> None:
    """Empty database to serving, in one command."""

    logger.info("1/4 discovering stations")
    tasks.discover_stations()
    logger.info("2/4 backfilling %s days", args.days)
    cmd_backfill(args)
    logger.info("3/4 training the first model")
    cmd_train(args)
    logger.info("4/4 issuing a first forecast run")
    tasks.run_forecasts()
    logger.info("bootstrap complete")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clearway", description=__doc__)
    parser.add_argument("--log-level", default="INFO")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("discover", help="find stations near configured cities").set_defaults(
        func=cmd_discover
    )
    subparsers.add_parser("ingest", help="pull recent observations and CAMS").set_defaults(
        func=cmd_ingest
    )
    subparsers.add_parser("forecast", help="run a forecast for every station").set_defaults(
        func=cmd_forecast
    )
    subparsers.add_parser("score", help="score yesterday's forecasts").set_defaults(
        func=cmd_score
    )
    subparsers.add_parser("status", help="data coverage per station").set_defaults(
        func=cmd_status
    )

    backfill = subparsers.add_parser("backfill", help="pull a long history")
    backfill.add_argument("--days", type=int, default=60)
    backfill.add_argument("--city", default=None)
    backfill.set_defaults(func=cmd_backfill)

    train = subparsers.add_parser("train", help="train a candidate model")
    train.add_argument("--days", type=int, default=90)
    train.add_argument("--no-promote", action="store_true")
    train.add_argument("--force", action="store_true", help="promote even if it loses")
    train.set_defaults(func=cmd_train)

    bootstrap = subparsers.add_parser("bootstrap", help="empty database to serving")
    bootstrap.add_argument("--days", type=int, default=60)
    bootstrap.add_argument("--city", default=None)
    bootstrap.add_argument("--no-promote", action="store_true")
    bootstrap.add_argument("--force", action="store_true")
    bootstrap.set_defaults(func=cmd_bootstrap)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)
    try:
        args.func(args)
    except ClearwayError as exc:
        logger.error("%s", exc.message)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
