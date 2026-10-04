import asyncio
import json
import socket
import threading
import time
from contextlib import aclosing

import httpx
import pytest
import uvicorn

from app.enums import EventType
from app.main import create_app
from tests.conftest import CLEAN, FAILED, make_settings


# ------------------------------------------------------------------ bus level
async def test_seq_is_monotonic_per_workflow_and_independent(env):
    a, b = env.workflow(), env.workflow()
    for i in range(5):
        await env.bus.emit(a, EventType.AUDIT_CHECK_RESULT, message=f"a{i}")
    await env.bus.emit(b, EventType.WORKFLOW_CREATED, message="b0")
    assert [e.seq for e in env.bus.history_sync(a)] == [1, 2, 3, 4, 5]
    assert [e.seq for e in env.bus.history_sync(b)] == [1]


async def test_concurrent_emits_never_duplicate_or_skip_seq(env):
    wid = env.workflow()
    await asyncio.gather(*(env.bus.emit(wid, EventType.AUDIT_CHECK_RESULT, message=str(i)) for i in range(60)))
    assert [e.seq for e in env.bus.history_sync(wid)] == list(range(1, 61))


async def test_late_join_gets_history_then_live_without_gaps_or_dupes(env):
    wid = env.workflow()
    for i in range(3):
        await env.bus.emit(wid, EventType.AUDIT_CHECK_RESULT, message=f"old{i}")
    got: list[int] = []

    async def consume():
        async with aclosing(env.bus.stream(wid, 0, idle_timeout=2)) as stream:
            async for ev in stream:
                got.append(ev.seq)
                if len(got) == 8:
                    return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    for i in range(5):  # live events, emitted while the consumer is already attached
        await env.bus.emit(wid, EventType.AUDIT_CHECK_RESULT, message=f"live{i}")
    await asyncio.wait_for(task, 3)
    assert got == [1, 2, 3, 4, 5, 6, 7, 8]
    assert env.bus.subscriber_count(wid) == 0  # cleaned up on exit


async def test_resume_after_seq_has_no_duplicates(env):
    wid = env.workflow()
    for i in range(6):
        await env.bus.emit(wid, EventType.AUDIT_CHECK_RESULT, message=str(i))
    got = []
    async for ev in env.bus.stream(wid, 4, idle_timeout=0.2):
        got.append(ev.seq)
    assert got == [5, 6]


async def test_events_emitted_during_history_read_are_not_lost(env):
    """Subscribe-before-read: an event landing between subscribe and history must still arrive exactly once."""
    wid = env.workflow()
    await env.bus.emit(wid, EventType.AUDIT_CHECK_RESULT, message="1")
    got = []
    gen = env.bus.stream(wid, 0, idle_timeout=0.3)
    first = await gen.__anext__()
    got.append(first.seq)
    await env.bus.emit(wid, EventType.AUDIT_CHECK_RESULT, message="2")
    async for ev in gen:
        got.append(ev.seq)
    assert got == [1, 2]


async def test_disconnect_cleans_up_subscriber(env):
    wid = env.workflow()
    gen = env.bus.stream(wid, 0, idle_timeout=5)
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.05)
    assert env.bus.subscriber_count(wid) == 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await gen.aclose()
    assert env.bus.subscriber_count(wid) == 0


# ------------------------------------------------------------------ real HTTP/SSE
@pytest.fixture
def live(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(make_settings(tmp_path))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True
    t.join(5)


def read_sse(url, *, headers=None, want=None, until=None, timeout=15):
    """Parse an SSE stream into dicts; stop after `want` events or when `until(event)`."""
    out, cur = [], {}
    with httpx.stream("GET", url, headers=headers or {}, timeout=timeout) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line == "":
                if cur:
                    out.append(cur)
                    if (want and len(out) >= want) or (until and until(cur)):
                        break
                    cur = {}
            elif line.startswith(":"):
                continue
            else:
                k, _, v = line.partition(":")
                cur[k] = v.lstrip()
    return out


def upload(base, paths):
    files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in paths]
    return httpx.post(f"{base}/api/evidence/upload", files=files, timeout=30).json()["workflow_id"]


def wait_settled(base, wid):
    for _ in range(200):
        d = httpx.get(f"{base}/api/workflows/{wid}").json()
        if d["status"] in {"COMPLETED", "AWAITING_REVIEW", "FAILED"}:
            return d
        time.sleep(0.05)
    raise AssertionError("not settled")


def test_sse_late_join_full_history_in_order(live):
    base, _ = live
    wid = upload(base, FAILED)
    wait_settled(base, wid)
    hist = httpx.get(f"{base}/api/workflows/{wid}/events/history").json()
    got = read_sse(f"{base}/api/workflows/{wid}/events", want=len(hist))
    assert [int(e["id"]) for e in got] == [e["seq"] for e in hist]
    assert [e["event"] for e in got] == [e["type"] for e in hist]
    payload = json.loads(got[0]["data"])
    assert payload["type"] == "WORKFLOW_CREATED" and payload["seq"] == 1 and payload["workflow_id"] == wid


def test_sse_resume_with_last_event_id_and_query(live):
    base, _ = live
    wid = upload(base, CLEAN)
    wait_settled(base, wid)
    hist = httpx.get(f"{base}/api/workflows/{wid}/events/history").json()
    got = read_sse(f"{base}/api/workflows/{wid}/events", headers={"Last-Event-ID": "10"}, want=len(hist) - 10)
    assert [int(e["id"]) for e in got] == list(range(11, len(hist) + 1))  # no duplicates, no gap
    got2 = read_sse(f"{base}/api/workflows/{wid}/events?after=30", want=len(hist) - 30)
    assert int(got2[0]["id"]) == 31
    r = httpx.get(f"{base}/api/workflows/{wid}/events", headers={"Last-Event-ID": "abc"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_LAST_EVENT_ID"
    assert httpx.get(f"{base}/api/workflows/wf_nope/events").status_code == 404


def test_sse_receives_live_events_while_connected(live):
    base, _ = live
    wid = upload(base, FAILED)
    wait_settled(base, wid)
    last = len(httpx.get(f"{base}/api/workflows/{wid}/events/history").json())
    result = {}

    def consume():
        result["events"] = read_sse(f"{base}/api/workflows/{wid}/events", headers={"Last-Event-ID": str(last)}, until=lambda e: e.get("event") == "RULES_RERUN_COMPLETED")

    t = threading.Thread(target=consume)
    t.start()
    time.sleep(0.5)  # the consumer is attached and waiting
    r = httpx.post(f"{base}/api/workflows/{wid}/rerun-rules", json={"overrides": {"CHEM_MAX": {"limit": 20}}}, timeout=30)
    assert r.status_code == 200
    t.join(15)
    ev = result["events"]
    ids = [int(e["id"]) for e in ev]
    assert ids == list(range(last + 1, last + 1 + len(ids)))  # contiguous: live fan-out has no gaps
    assert ev[0]["event"] == "RULES_RERUN_STARTED" and ev[-1]["event"] == "RULES_RERUN_COMPLETED"
