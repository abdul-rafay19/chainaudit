"""EventBus: append-only persistence (source of truth) + in-memory fan-out to SSE subscribers.

Ordering guarantee: for a given workflow, the DB write (which assigns `seq`) and the
fan-out happen under one per-workflow lock, so subscribers always see seq in order.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import func, select

from app.db import Database
from app.enums import AgentName, EventType
from app.models.tables import WorkflowEventRow, utcnow_iso
from app.schemas.domain import StoredEvent


def row_to_event(r: WorkflowEventRow) -> StoredEvent:
    return StoredEvent(
        id=r.id,
        workflow_id=r.workflow_id,
        seq=r.seq,
        type=EventType(r.type),
        agent=r.agent,
        timestamp=r.timestamp,
        finding_id=r.finding_id,
        document_id=r.document_id,
        message=r.message,
        data=json.loads(r.data_json or "{}"),
        replay=r.replay,
    )


class EventBus:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._subs: dict[str, set[asyncio.Queue[StoredEvent]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ---- writing -------------------------------------------------------
    def _insert(
        self,
        workflow_id: str,
        type_: EventType,
        agent: str,
        message: str,
        finding_id: str | None,
        document_id: str | None,
        data: dict[str, Any],
        replay: bool,
    ) -> StoredEvent:
        with self._db.session() as s:
            cur = s.execute(select(func.coalesce(func.max(WorkflowEventRow.seq), 0)).where(WorkflowEventRow.workflow_id == workflow_id))
            seq = int(cur.scalar_one()) + 1
            row = WorkflowEventRow(
                workflow_id=workflow_id,
                seq=seq,
                type=type_.value,
                agent=str(agent),
                timestamp=utcnow_iso(),
                finding_id=finding_id,
                document_id=document_id,
                message=message,
                data_json=json.dumps(data, default=str),
                replay=replay,
            )
            s.add(row)
            s.flush()
            return row_to_event(row)

    async def emit(
        self,
        workflow_id: str,
        type_: EventType,
        *,
        agent: AgentName | str = AgentName.system,
        message: str = "",
        finding_id: str | None = None,
        document_id: str | None = None,
        data: dict[str, Any] | None = None,
        replay: bool = False,
    ) -> StoredEvent:
        lock = self._locks.setdefault(workflow_id, asyncio.Lock())
        async with lock:
            ev = await asyncio.to_thread(self._insert, workflow_id, type_, str(agent), message, finding_id, document_id, data or {}, replay)
            for q in list(self._subs.get(workflow_id, ())):
                q.put_nowait(ev)
        return ev

    # ---- reading -------------------------------------------------------
    def history_sync(self, workflow_id: str, after: int = 0) -> list[StoredEvent]:
        with self._db.session() as s:
            rows = s.scalars(select(WorkflowEventRow).where(WorkflowEventRow.workflow_id == workflow_id, WorkflowEventRow.seq > after).order_by(WorkflowEventRow.seq)).all()
            return [row_to_event(r) for r in rows]

    async def history(self, workflow_id: str, after: int = 0) -> list[StoredEvent]:
        return await asyncio.to_thread(self.history_sync, workflow_id, after)

    # ---- subscribing ---------------------------------------------------
    @asynccontextmanager
    async def subscribe(self, workflow_id: str) -> AsyncIterator[asyncio.Queue[StoredEvent]]:
        q: asyncio.Queue[StoredEvent] = asyncio.Queue()
        self._subs.setdefault(workflow_id, set()).add(q)
        try:
            yield q
        finally:
            subs = self._subs.get(workflow_id)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    self._subs.pop(workflow_id, None)

    def subscriber_count(self, workflow_id: str) -> int:
        return len(self._subs.get(workflow_id, ()))

    async def stream(self, workflow_id: str, after: int = 0, idle_timeout: float = 3600) -> AsyncGenerator[StoredEvent, None]:
        """History with seq > after, then live events: no gaps, no duplicates.

        Subscribe FIRST (buffering live events), then read history, then drain the
        buffer skipping anything already delivered.
        """
        async with self.subscribe(workflow_id) as q:
            last = after
            for ev in await self.history(workflow_id, after):
                last = ev.seq
                yield ev
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=idle_timeout)
                except TimeoutError:
                    return
                if ev.seq <= last:
                    continue
                last = ev.seq
                yield ev
