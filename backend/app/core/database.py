"""Engine, session factory and the FastAPI dependency that hands sessions out.

One place owns the connection lifecycle. Repositories receive a ``Session``;
they never create one, which is what makes a service able to run several
repository calls inside a single transaction.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    echo=_settings.db_echo,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency. One session per request, always closed."""
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """For jobs and scripts, which have no request to hang a session off.

    Commits on success, rolls back on any exception. Callers get a transaction
    boundary without writing try/except/finally at every call site.
    """
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
