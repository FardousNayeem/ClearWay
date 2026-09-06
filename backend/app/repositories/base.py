"""Shared repository behaviour.

A repository owns SQL for one aggregate and nothing else. It receives a
``Session``; it never opens one, never commits, and never decides policy. That
is what lets a service run several repositories inside a single transaction.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session


class Repository:
    def __init__(self, session: Session) -> None:
        self.session = session


def upsert_many(
    session: Session,
    table: Any,
    rows: list[dict[str, Any]],
    *,
    conflict_columns: list[str],
    update_columns: list[str] | None = None,
    chunk_size: int = 1000,
) -> int:
    """Insert rows, updating on the natural key.

    Ingestion is retried routinely: a scheduled run overlaps the previous one,
    a backfill is resumed, an upstream re-publishes a corrected value. Every
    write therefore has to be idempotent, which is why each time-series table
    carries a natural-key unique constraint for this to target.
    """

    if not rows:
        return 0

    written = 0
    for offset in range(0, len(rows), chunk_size):
        chunk = rows[offset : offset + chunk_size]
        statement = pg_insert(table).values(chunk)
        if update_columns:
            statement = statement.on_conflict_do_update(
                index_elements=conflict_columns,
                set_={column: getattr(statement.excluded, column) for column in update_columns},
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=conflict_columns)
        session.execute(statement)
        written += len(chunk)
    return written
