import json
import re
import sqlite3
import time
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text

import app.enums as enums
from app.agents.auditor.state_machine import FindingAuditor
from app.config import Settings
from app.enums import AuditorFlag, ComplianceStatus, EscalationReason
from app.models.tables import AppendOnlyError, WorkflowEventRow
from app.schemas.domain import Finding
from tests.conftest import CLEAN, DEMO, FAILED

APP = Path(__file__).resolve().parents[1] / "app"


# ------------------------------------------------------------------ N2: the Auditor cannot change a verdict
def make_finding(status=ComplianceStatus.PASSING_CONFIGURED_CHECK):
    return Finding(finding_id="f1", workflow_id="w", evaluation_number=1, rule_id="CHEM_MAX", rule_title="t", compliance_status=status, reason="r")


def test_auditor_has_no_write_path_to_compliance_status():
    f = make_finding()
    a = FindingAuditor(f)
    public = {n for n in dir(a) if not n.startswith("_")}
    assert {"add_flag", "mark_verified", "mark_uncertain", "request_reextraction"} <= public
    assert not [n for n in public if "status" in n.lower() and not n.startswith("mark_")]
    with pytest.raises(AttributeError):
        a.compliance_status = ComplianceStatus.REQUIREMENT_NOT_SATISFIED  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        a.finding = f  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        a.__dict__  # noqa: B018  (slots: no instance dict to smuggle writes through)
    # exercising every permitted action never alters the verdict
    for st in ComplianceStatus:
        f = make_finding(st)
        a = FindingAuditor(f)
        a.add_flag(AuditorFlag.QUOTE_NOT_FOUND)
        a.mark_uncertain(EscalationReason.REEXTRACTION_LIMIT)
        a.mark_verified()
        a.request_reextraction("d", "batch_id")
        a.finalize()
        assert f.compliance_status == st and f.audit_status.value == "uncertain" and f.requires_human_review  # uncertainty is sticky


def test_outcome_mapping_table():
    cases = [
        (ComplianceStatus.PASSING_CONFIGURED_CHECK, False, False),
        (ComplianceStatus.REQUIREMENT_NOT_SATISFIED, False, True),
        (ComplianceStatus.HUMAN_REVIEW_REQUIRED, False, True),
        (ComplianceStatus.PASSING_CONFIGURED_CHECK, True, True),
        (ComplianceStatus.REQUIREMENT_NOT_SATISFIED, True, True),
    ]
    for status, uncertain, review in cases:
        f = make_finding(status)
        a = FindingAuditor(f)
        if uncertain:
            a.mark_uncertain(EscalationReason.LOW_EXTRACTION_CONFIDENCE)
        a.finalize()
        assert f.requires_human_review is review


def test_grep_no_compliance_status_write_in_auditor_code():
    write = re.compile(r"compliance_status\s*=(?!=)|\.compliance_status\s*:=|setattr\([^)]*compliance_status")
    offenders = [str(p) for p in (APP / "agents" / "auditor").rglob("*.py") if write.search(p.read_text())]
    assert offenders == []
    # and only the compliance agent assigns it (via the single apply_result function) outside of schema definitions
    writers = [str(p.relative_to(APP)) for p in APP.rglob("*.py") if re.search(r"\.compliance_status\s*=(?!=)", p.read_text())]
    # evaluator.apply_result is the only decision path; store.py merely mirrors the value into a DB column when persisting
    assert sorted(writers) == ["agents/compliance/evaluator.py", "core/store.py"]
    assert "row.compliance_status = f.compliance_status.value" in (APP / "core" / "store.py").read_text()
    assert not re.search(r"\bLLMProvider\b|generate_structured", (APP / "rules" / "engine.py").read_text())  # N1: engine never touches an LLM
    eng = (APP / "rules" / "engine.py").read_text()
    assert not re.search(r"datetime\.now|date\.today|time\.time|import requests|import httpx|open\(", eng)  # pure: no clock, no I/O


# ------------------------------------------------------------------ N5: append-only events
def test_events_are_append_only_in_db_and_orm(api):
    wid, _ = api.run(CLEAN)
    path = api.c.settings.database_url.replace("sqlite:///", "")
    raw = sqlite3.connect(path)
    for stmt in ("UPDATE workflow_events SET message='tampered'", "DELETE FROM workflow_events"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            raw.execute(stmt)
    raw.close()
    with api.c.db.session() as s:
        row = s.get(WorkflowEventRow, 1)
        row.message = "x"
        with pytest.raises(AppendOnlyError):
            s.flush()
        s.rollback()
    with pytest.raises(Exception, match="immutable"):
        with api.c.db.engine.begin() as conn:
            conn.execute(text("UPDATE rule_sets SET name='x'"))
    src = "\n".join(p.read_text() for p in APP.rglob("*.py") if p.name not in {"tables.py", "db.py"})
    assert not re.search(r"(update|delete)\(\s*WorkflowEventRow", src)  # no update/delete code path exists


def test_every_finding_has_evidence_or_escalation_reason(api):
    packs = [
        CLEAN,
        FAILED,
        [DEMO / "robustness" / "injection_test.pdf"],
        [DEMO / "robustness" / "scan_like.pdf"],
        [CLEAN[0], ("broken.pdf", b"%PDF-1.4\nnope")],
        [CLEAN[0], CLEAN[2]],
    ]
    for p in packs:
        wid, d = api.run(p)
        for f in d["findings"]:
            assert f["evidence"] or f["escalation_reason"], f["rule_id"]
            if f["compliance_status"] == "HUMAN_REVIEW_REQUIRED":
                assert f["escalation_reason"] and f["requires_human_review"]
            for e in f["evidence"]:  # N3: nothing unsupported
                if e["value"] is not None:
                    assert e["source_document"] and e["document_id"] and e["page"] and e["quote"] and 0 <= e["confidence"] <= 1


def test_banned_wording_never_in_user_facing_text(api):
    wid, d = api.run(FAILED)
    blob = json.dumps(d).lower() + json.dumps(api.events(wid)).lower()
    assert "fraud" not in blob and "certified" not in blob


# ------------------------------------------------------------------ contract
def test_openapi_exports_every_locked_enum():
    from app.config import Settings as S
    from app.main import create_app

    spec = create_app(S(_env_file=None, database_url="sqlite://", llm_provider="mock")).openapi()
    comps = spec["components"]["schemas"]
    for name in ("ComplianceStatus", "AuditStatus", "ReviewDecision", "EscalationReason", "WorkflowStatus", "AuditorFlag", "EventType", "DocType", "Verification"):
        assert name in comps, name
        assert comps[name]["enum"] == [m.value for m in getattr(enums, name)]
    paths = set(spec["paths"])
    for p in [
        "/api/health",
        "/api/evidence/upload",
        "/api/workflows/{workflow_id}/evidence",
        "/api/workflows",
        "/api/workflows/{workflow_id}",
        "/api/workflows/{workflow_id}/events",
        "/api/workflows/{workflow_id}/events/history",
        "/api/workflows/{workflow_id}/rerun-rules",
        "/api/findings",
        "/api/findings/{finding_id}",
        "/api/findings/{finding_id}/evidence-image",
        "/api/reviews/{finding_id}",
        "/api/findings/{finding_id}/dispatch",
        "/api/rules",
        "/api/rule-sets/{rule_set_id}",
        "/api/audit/{workflow_id}",
        "/api/replays",
        "/api/replays/{name}/start",
    ]:
        assert p in paths, p
    committed = json.loads((Path(__file__).resolve().parents[2] / "docs" / "openapi.json").read_text())
    assert committed == json.loads(json.dumps(spec, sort_keys=True)), "docs/openapi.json is stale: run `make openapi`"


def test_config_fails_clearly_without_key():
    for prov, msg in (("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY")):
        with pytest.raises(ValidationError, match=msg):
            Settings(_env_file=None, llm_provider=prov, llm_model="m")
    with pytest.raises(ValidationError, match="LLM_MODEL"):
        Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key="k")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="nonsense")
    assert Settings(_env_file=None, llm_provider="mock").llm_provider == "mock"


def test_health_labels_mock_and_faults(make_api):
    h = make_api(mock_faults="lab_report.batch_id").client.get("/api/health").json()
    assert h["status"] == "ok" and h["provider"] == "mock" and h["mock"] is True and h["fault_injection"] == ["lab_report.batch_id"]


def test_provider_sdk_signatures_exist():
    """Guards against invented SDK calls: the real parameters we pass must exist in the installed SDKs."""
    import inspect

    anthropic = pytest.importorskip("anthropic")
    openai = pytest.importorskip("openai")
    a = inspect.signature(anthropic.AsyncAnthropic(api_key="x").messages.create).parameters
    assert {"model", "max_tokens", "system", "messages", "tools", "tool_choice", "timeout"} <= set(a)
    o = inspect.signature(openai.AsyncOpenAI(api_key="x").chat.completions.create).parameters
    assert {"model", "messages", "response_format", "timeout"} <= set(o)


# ------------------------------------------------------------------ replays
def test_replay_list_and_start(make_api):
    api = make_api(replay_step_seconds=0.0)
    lst = api.client.get("/api/replays").json()
    names = {r["name"] for r in lst}
    assert {"clean_supplier", "failed_supplier", "auditor_loop_fault_injection", "decoy_trap_fault_injection"} <= names
    rec = json.loads((DEMO / "replays" / "failed_supplier.json").read_text())
    r = api.client.post("/api/replays/failed_supplier/start")
    assert r.status_code == 200
    wid = r.json()["workflow_id"]
    for _ in range(100):
        ev = api.events(wid)
        if len(ev) == len(rec["events"]):
            break
        time.sleep(0.05)
    assert len(ev) == len(rec["events"]) and all(e["replay"] for e in ev)
    assert [e["seq"] for e in ev] == list(range(1, len(ev) + 1)) and [e["type"] for e in ev] == [e["type"] for e in rec["events"]]
    assert all(e["workflow_id"] == wid for e in ev)
    d = api.client.get(f"/api/workflows/{wid}").json()
    assert d["replay"] is True and "REPLAY" in d["labels"] and d["status"] == "AWAITING_REVIEW"
    assert wid != rec["workflow"]["id"] and all(f["workflow_id"] == wid for f in d["findings"])
    f = {x["rule_id"]: x for x in d["findings"]}
    assert f["CHEM_MAX"]["compliance_status"] == "REQUIREMENT_NOT_SATISFIED"
    img = api.client.get(f"/api/findings/{f['CHEM_MAX']['finding_id']}/evidence-image")
    assert img.status_code == 200 and img.headers["x-highlight"] == "bbox"  # replayed documents are real files
    fd = api.client.get(f"/api/findings/{f['ID_CONSISTENCY']['finding_id']}").json()
    assert fd["audit_checks"]  # event finding_ids were remapped consistently
    again = api.client.post("/api/replays/failed_supplier/start").json()["workflow_id"]
    assert again != wid
    assert api.client.post("/api/replays/nope/start").status_code == 404
    assert api.client.post("/api/replays/..%2Fetc/start").status_code in (404, 422)


def test_replayed_fault_injection_is_labelled(make_api):
    api = make_api(replay_step_seconds=0.0)
    wid = api.client.post("/api/replays/auditor_loop_fault_injection/start").json()["workflow_id"]
    rec = json.loads((DEMO / "replays" / "auditor_loop_fault_injection.json").read_text())
    for _ in range(100):
        ev = api.events(wid)
        if len(ev) == len(rec["events"]):
            break
        time.sleep(0.05)
    assert any(e["data"].get("fault_injected") for e in ev) and any(e["type"] == "REEXTRACTION_COMPLETED" for e in ev)
