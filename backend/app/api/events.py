from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_container
from app.container import Container
from app.core.errors import bad_request
from app.schemas.domain import StoredEvent

router = APIRouter(prefix="/api/workflows", tags=["events"])


def _parse_after(after: int | None, last_event_id: str | None) -> int:
    if after is not None:
        return max(after, 0)
    if last_event_id:
        try:
            return max(int(last_event_id), 0)
        except ValueError as e:
            raise bad_request("BAD_LAST_EVENT_ID", "Last-Event-ID must be an integer seq") from e
    return 0


@router.get("/{workflow_id}/events")
async def stream_events(
    workflow_id: str,
    after: int | None = Query(default=None, ge=0, description="Replay events with seq > after"),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    c: Container = Depends(get_container),
) -> EventSourceResponse:
    """Server-Sent Events: stored history after `Last-Event-ID`/`after`, then live events."""
    c.repo.require_workflow(workflow_id)
    start = _parse_after(after, last_event_id)

    async def gen() -> AsyncIterator[dict[str, Any]]:
        agen = c.bus.stream(workflow_id, start, idle_timeout=c.settings.sse_idle_seconds)
        try:
            async for ev in agen:
                yield {"id": str(ev.seq), "event": ev.type.value, "data": ev.model_dump_json()}
        finally:  # a client disconnect cancels this generator: always release the bus subscriber
            await agen.aclose()

    return EventSourceResponse(gen(), ping=15)


@router.get("/{workflow_id}/events/history", response_model=list[StoredEvent])
async def event_history(workflow_id: str, after: int = Query(default=0, ge=0), c: Container = Depends(get_container)) -> list[StoredEvent]:
    c.repo.require_workflow(workflow_id)
    return await c.bus.history(workflow_id, after)
