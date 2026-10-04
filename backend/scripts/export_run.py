"""Record real runs as bundled replays.

    python scripts/export_run.py --record-demo      # runs the demo scenarios in-process (mock provider) and writes demo_data/replays
    python scripts/export_run.py --db X.db --workflow wf_... --name my_run --title T --description D

A recording stores the workflow snapshot (documents, extractions, findings, rule set) and its full event stream.
POST /api/replays/{name}/start re-creates it as a NEW workflow whose events are all flagged replay=true.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ["CHAINAUDIT_NO_AUTOAPP"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.container import Container  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo_data"
OUT = DEMO / "replays"


def export(c: Container, wid: str, name: str, title: str, description: str, out: Path = OUT) -> Path:
    wf = c.repo.require_workflow(wid)
    rs = c.repo.get_rule_set(wf.rule_set_id)
    assert rs is not None
    docs = c.repo.documents(wid)
    files_dir = out / f"{name}_files"
    if files_dir.exists():
        shutil.rmtree(files_dir)
    files_dir.mkdir(parents=True)
    doc_rows = []
    for d in docs:
        fn = Path(d.stored_path).name
        shutil.copyfile(d.stored_path, files_dir / fn)
        doc_rows.append(
            {
                "id": d.id,
                "original_name": d.original_name,
                "sha256": d.sha256,
                "mime": d.mime,
                "size": d.size,
                "doc_type": d.doc_type,
                "page_count": d.page_count,
                "status": d.status,
                "error": d.error,
                "file": fn,
            }
        )
    ext = {d.document_id: d.model_dump(mode="json") for d in c.repo.extractions(wid)}
    rec = {
        "title": title,
        "description": description,
        "workflow": {
            "id": wid,
            "status": wf.status,
            "evaluation_number": wf.evaluation_number,
            "provider": wf.provider,
            "model": wf.model,
            "rule_set": {"id": rs.id, "name": rs.name, "version": rs.version, "content": json.loads(rs.content_json), "derived_from": rs.derived_from},
        },
        "documents": doc_rows,
        "extractions": ext,
        "findings": [f.model_dump(mode="json") for f in c.repo.findings(wid)],
        "events": [
            {"type": e.type.value, "agent": str(e.agent), "message": e.message, "finding_id": e.finding_id, "document_id": e.document_id, "data": e.data}
            for e in c.bus.history_sync(wid)
        ],
    }
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.json"
    path.write_text(json.dumps(rec, indent=1) + "\n")
    return path


def _run_pack(c: Container, paths: list[Path]) -> str:
    import asyncio

    from app.core.storage import validate_upload

    async def go() -> str:
        uploads = [validate_upload(p.name, p.read_bytes()) for p in paths]
        wid, _ = await c.orchestrator.create_from_uploads(uploads, None)
        await c.orchestrator.wait_idle()
        return wid

    return asyncio.run(go())


SCENARIOS = [
    ("clean_supplier", "Clean supplier (SUP-001)", "All four configured checks pass and every value is verified against its source. No human review needed.", "clean_supplier", ""),
    (
        "failed_supplier",
        "Failed supplier (SUP-002)",
        "Chemical limit exceeded (17.4 > 15 ppm) with a corrective-action draft, plus a genuine batch-id inconsistency between documents, both verified and sent to human review.",
        "failed_supplier",
        "",
    ),
    (
        "auditor_loop_fault_injection",
        "Auditor to extractor loop (fault injection, test mode)",
        "A deliberate wrong batch id is injected on the first extraction attempt. The Auditor cannot find the quote, asks the extractor to re-read the page, and the corrected value resolves the conflict.",
        "clean_supplier",
        "lab_report.batch_id",
    ),
    (
        "decoy_trap_fault_injection",
        "Decoy trap caught (fault injection, test mode)",
        "The extractor picks the decoy 'Previous report reference: BT-2041'. The Auditor recognises it describes another document, requests a re-read of the whole document, and the real batch id is recovered.",
        "failed_supplier",
        "lab_report.batch_id",
    ),
]


def record_demo() -> None:
    for name, title, desc, pack, faults in SCENARIOS:
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(_env_file=None, database_url=f"sqlite:///{tmp}/r.db", upload_dir=Path(tmp) / "up", render_dir=Path(tmp) / "rn", llm_provider="mock", mock_faults=faults)
            c = Container(s)
            c.startup()
            paths = sorted((DEMO / pack).glob("*.pdf"))
            wid = _run_pack(c, paths)
            p = export(c, wid, name, title, desc)
            print(f"recorded {p.name}: {c.repo.require_workflow(wid).status}, {len(c.bus.history_sync(wid))} events")
            c.db.dispose()
            time.sleep(0.05)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record-demo", action="store_true")
    ap.add_argument("--db")
    ap.add_argument("--workflow")
    ap.add_argument("--name")
    ap.add_argument("--title", default="")
    ap.add_argument("--description", default="")
    a = ap.parse_args()
    if a.record_demo:
        record_demo()
        return
    if not (a.db and a.workflow and a.name):
        ap.error("--record-demo, or --db + --workflow + --name")
    c = Container(Settings(_env_file=None, database_url=f"sqlite:///{a.db}", llm_provider="mock"))
    print(export(c, a.workflow, a.name, a.title or a.name, a.description))


if __name__ == "__main__":
    main()
