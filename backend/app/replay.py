"""Replays: bundled recordings of real runs. Re-created as a NEW workflow whose events are all flagged replay=true."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any

from app.container import Container
from app.core.errors import not_found
from app.core.store import new_id
from app.enums import EventType
from app.models.tables import DocumentRow, WorkflowRow
from app.schemas.domain import ExtractedDocument, Finding, ReplayInfo

NAME_OK = re.compile(r"^[A-Za-z0-9_\-]+$")


def _load(dir_: Path, name: str) -> dict[str, Any]:
    if not NAME_OK.fullmatch(name) or not (dir_ / f"{name}.json").exists():
        raise not_found("Replay", name)
    return json.loads((dir_ / f"{name}.json").read_text(encoding="utf-8"))


def list_replays(dir_: Path) -> list[ReplayInfo]:
    out: list[ReplayInfo] = []
    for p in sorted(dir_.glob("*.json")) if dir_.exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append(ReplayInfo(name=p.stem, title=d["title"], description=d["description"], event_count=len(d["events"])))
        except (OSError, KeyError, json.JSONDecodeError):
            continue
    return out


async def start_replay(c: Container, name: str) -> str:
    rec = _load(c.settings.replay_dir, name)
    new_wid = new_id("wf")
    text = json.dumps(rec)
    old_ids = [rec["workflow"]["id"]] + [d["id"] for d in rec["documents"]] + [f["finding_id"] for f in rec["findings"]]
    mapping = {o: (new_wid if o == rec["workflow"]["id"] else new_id(o.split("_")[0])) for o in old_ids}
    for o, n in mapping.items():
        text = text.replace(o, n)
    rec = json.loads(text)

    def build() -> None:
        w = rec["workflow"]
        rs = w["rule_set"]
        c.repo.save_rule_set(id_=rs["id"], name=rs["name"], version=rs["version"], content=rs["content"], derived_from=rs.get("derived_from"))
        with c.db.session() as s:
            s.add(
                WorkflowRow(
                    id=new_wid,
                    status=w["status"],
                    rule_set_id=rs["id"],
                    evaluation_number=w["evaluation_number"],
                    provider=w["provider"],
                    model=w["model"],
                    replay=True,
                    replay_name=name,
                )
            )
            s.flush()
            for d in rec["documents"]:
                src = c.settings.replay_dir / f"{name}_files" / d["file"]
                dst = c.settings.upload_dir / new_wid / d["file"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                s.add(
                    DocumentRow(
                        id=d["id"],
                        workflow_id=new_wid,
                        original_name=d["original_name"],
                        stored_path=str(dst.resolve()),
                        sha256=d["sha256"],
                        mime=d["mime"],
                        size=d["size"],
                        doc_type=d["doc_type"],
                        page_count=d["page_count"],
                        status=d["status"],
                        error=d.get("error"),
                    )
                )
        for did, ex in rec["extractions"].items():
            c.repo.save_extraction(ExtractedDocument.model_validate(ex), f"replay:{new_wid}:{did}", write_cache=False)
        for f in rec["findings"]:
            c.repo.save_finding(Finding.model_validate(f))

    await asyncio.to_thread(build)

    async def play() -> None:
        for ev in rec["events"]:
            await asyncio.sleep(c.settings.replay_step_seconds)
            await c.bus.emit(
                new_wid,
                EventType(ev["type"]),
                agent=ev["agent"],
                message=ev["message"],
                finding_id=ev.get("finding_id"),
                document_id=ev.get("document_id"),
                data=ev.get("data") or {},
                replay=True,
            )

    c.orchestrator._spawn(play())
    return new_wid
