"""SQLite engine (WAL), sessions and schema creation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.tables import Base

_TRIGGERS = [
    """CREATE TRIGGER IF NOT EXISTS workflow_events_no_update
       BEFORE UPDATE ON workflow_events
       BEGIN SELECT RAISE(ABORT, 'workflow_events is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS workflow_events_no_delete
       BEFORE DELETE ON workflow_events
       BEGIN SELECT RAISE(ABORT, 'workflow_events is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS rule_sets_no_update
       BEFORE UPDATE ON rule_sets
       BEGIN SELECT RAISE(ABORT, 'rule_sets rows are immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS rule_sets_no_delete
       BEFORE DELETE ON rule_sets
       BEGIN SELECT RAISE(ABORT, 'rule_sets rows are immutable'); END""",
]


class Database:
    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(url, connect_args=connect_args, future=True)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()

        self._sessionmaker = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            for stmt in _TRIGGERS:
                conn.execute(text(stmt))

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Short transaction: commit on success, rollback on error."""
        s = self._sessionmaker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()
