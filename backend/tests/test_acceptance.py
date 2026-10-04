import pytest
from fastapi.testclient import TestClient

from app.llm.mock_provider import MockProvider
from app.llm.schemas import FieldReextraction, LLMField
from app.main import create_app
from tests.conftest import CLEAN, DEMO, FAILED, make_settings

PASS, FAIL, REVIEW = "PASSING_CONFIGURED_CHECK", "REQUIREMENT_NOT_SATISFIED", "HUMAN_REVIEW_REQUIRED"


def types(events):
    return [e["type"] for e in events]


# ------------------------------------------------------------------ acceptance
def test_clean_pack_outcome(api):
    wid, d = api.run(CLEAN)
    assert d["status"] == "COMPLETED"
    f = api.by_rule(d)
    assert set(f) == {"CHEM_MAX", "CERT_REQUIRED", "DOC_FRESH", "ID_CONSISTENCY"}
    for x in f.values():
        assert x["compliance_status"] == PASS and x["audit_status"] == "verified" and x["requires_human_review"] is False
        assert x["evidence"] and all(e["verified"] and e["bbox"] for e in x["evidence"])
        assert x["corrective_action"] is None
    assert d["outcome"]["needs_review"] == 0 and "Synthetic Demo Data" in d["labels"] and "Demo Buyer Framework v1.0" in d["labels"] and "Mock provider" in d["labels"]
    ev = api.events(wid)
    assert [e["seq"] for e in ev] == list(range(1, len(ev) + 1))
    assert types(ev)[0] == "WORKFLOW_CREATED" and types(ev)[-1] == "WORKFLOW_COMPLETED"
    assert not any(e["replay"] for e in ev)


def test_failed_pack_outcome_and_event_order(api):
    wid, d = api.run(FAILED)
    assert d["status"] == "AWAITING_REVIEW"
    f = api.by_rule(d)
    chem = f["CHEM_MAX"]
    assert chem["compliance_status"] == FAIL and chem["audit_status"] == "verified" and chem["requires_human_review"]
    assert chem["reason"] == "17.4 ppm exceeds configured maximum of 15 ppm (rule CHEM_MAX, Demo Buyer Framework v1.0)"
    ca = chem["corrective_action"]
    assert ca["status"] == "draft" and ca["label"] == "AI draft. Review before sending." and ca["en"] and ca["roman_ur"]
    assert "fraud" not in (ca["en"] + ca["roman_ur"]).lower()
    assert f["CERT_REQUIRED"]["compliance_status"] == PASS and f["DOC_FRESH"]["compliance_status"] == PASS
    ids = f["ID_CONSISTENCY"]
    assert ids["compliance_status"] == REVIEW and ids["escalation_reason"] == "CONFLICTING_VALUES" and ids["audit_status"] == "verified"
    assert "BATCH_ID_CONFLICT" in ids["auditor_flags"] and ids["requires_human_review"] and ids["corrective_action"]
    assert {e["value"] for e in ids["evidence"] if e["field"] == "batch_id"} == {"BT-2047", "BT-2041"}
    ev = api.events(wid)
    t = types(ev)
    # ordering: extraction -> compliance -> findings -> audit -> reviews/drafts
    assert (
        max(i for i, x in enumerate(t) if x == "EXTRACTION_COMPLETED")
        < t.index("COMPLIANCE_CHECK_STARTED")
        < t.index("FINDING_CREATED")
        < t.index("AUDIT_STARTED")
        < t.index("AUDIT_COMPLETED")
    )
    assert t.index("HUMAN_REVIEW_REQUIRED") > t.index("AUDIT_STARTED") and "CORRECTIVE_ACTION_DRAFTED" in t and "WORKFLOW_COMPLETED" not in t
    assert t.count("FINDING_CREATED") == 4


def test_case_b_genuine_conflict_does_not_reextract(api):
    wid, d = api.run(FAILED)
    ev = api.events(wid)
    assert "REEXTRACTION_REQUESTED" not in types(ev)
    conflict = [e for e in ev if e["type"] == "AUDIT_CONFLICT_FOUND"]
    assert len(conflict) == 1 and conflict[0]["data"]["case"] == "B" and conflict[0]["data"]["genuine"] is True and conflict[0]["data"]["reextraction_attempted"] is False
    assert {v["value"] for v in conflict[0]["data"]["values"]} == {"BT-2047", "BT-2041"}


def test_case_a_fabricated_quote_is_reextracted_and_resolved(make_api):
    api = make_api(mock_faults="lab_report.batch_id")
    wid, d = api.run(CLEAN)
    assert d["status"] == "COMPLETED"
    assert all(x["compliance_status"] == PASS for x in api.by_rule(d).values())
    ev = api.events(wid)
    t = types(ev)
    assert t.index("AUDIT_CONFLICT_FOUND") < t.index("REEXTRACTION_REQUESTED") < t.index("REEXTRACTION_COMPLETED")
    done = next(e for e in ev if e["type"] == "REEXTRACTION_COMPLETED")
    assert done["data"]["old_value"] == "BT-2041" and done["data"]["new_value"] == "BT-2047" and done["data"]["changed"] is True
    req = next(e for e in ev if e["type"] == "REEXTRACTION_REQUESTED")
    assert req["data"]["mode"] == "page" and req["data"]["page_hint"] == 2  # re-reads ONLY that page
    ext = next(e for e in ev if e["type"] == "EXTRACTION_COMPLETED" and e["data"].get("fault_injected"))
    assert ext["data"]["fault"] == ["lab_report.batch_id"]
    flip = [e for e in ev if e["type"] == "COMPLIANCE_CHECK_STARTED" and e["data"].get("reevaluation")]
    assert flip and any(
        c["rule_id"] == "ID_CONSISTENCY" and c["before"]["compliance_status"] == REVIEW and c["after"]["compliance_status"] == PASS for c in flip[0]["data"]["changes"]
    )
    assert t.index("REEXTRACTION_COMPLETED") < t.index("AUDIT_COMPLETED")


def test_case_a_decoy_pack_catches_previous_report_reference(make_api):
    api = make_api(mock_faults="lab_report.batch_id")
    wid, d = api.run(FAILED)  # extractor picks the decoy 'Previous report reference: BT-2041'
    ev = api.events(wid)
    checks = [e for e in ev if e["type"] == "AUDIT_CHECK_RESULT" and e["data"].get("field") == "batch_id"]
    assert "EXTRACTOR_GUESS_SUSPECTED" in checks[0]["data"]["flags"] or any("EXTRACTOR_GUESS_SUSPECTED" in c["data"]["flags"] for c in checks)
    req = next(e for e in ev if e["type"] == "REEXTRACTION_REQUESTED")
    assert req["data"]["mode"] == "document"  # the right value is NOT on the decoy's page
    done = next(e for e in ev if e["type"] == "REEXTRACTION_COMPLETED")
    assert done["data"]["old_value"] == "BT-2041" and done["data"]["new_value"] == "BT-2047"
    ids = api.by_rule(d)["ID_CONSISTENCY"]
    assert ids["compliance_status"] == REVIEW and ids["escalation_reason"] == "CONFLICTING_VALUES" and ids["audit_status"] == "verified"


def test_reextraction_limit_escalates(make_api):
    class Stubborn(MockProvider):
        def _reextract(self, user_text):
            return FieldReextraction(field=LLMField(field="batch_id", value="BT-2041", unit=None, page=2, quote="Batch ID: BT-2041", confidence=0.9))

    api = make_api(mock_faults="lab_report.batch_id")
    api.c.provider.inner = Stubborn(faults={"lab_report.batch_id"})
    wid, d = api.run(CLEAN)
    ev = api.events(wid)
    assert types(ev).count("REEXTRACTION_REQUESTED") == 2  # MAX_REEXTRACTIONS_PER_FIELD
    assert any(e["type"] == "AUDIT_CONFLICT_FOUND" and e["data"].get("limit_reached") for e in ev)
    ids = api.by_rule(d)["ID_CONSISTENCY"]
    assert ids["audit_status"] == "uncertain" and ids["escalation_reason"] == "REEXTRACTION_LIMIT" and ids["requires_human_review"]
    assert d["status"] == "AWAITING_REVIEW"
    bad = [e for e in ids["evidence"] if e["value"] == "BT-2041"][0]
    # a one-character-off quote is only a fuzzy match: capped at 0.70, never 'verified', and below the 0.80 review threshold
    assert bad["verification"] == "fuzzy" and bad["confidence"] <= 0.70 and bad["verified"] is False
    assert "QUOTE_FUZZY_ONLY" in ids["auditor_flags"]


def test_semantic_check_can_make_uncertain_but_never_changes_status(make_api):
    api = make_api(mock_faults="semantic.CHEM_MAX")
    wid, d = api.run(CLEAN)
    chem = api.by_rule(d)["CHEM_MAX"]
    assert chem["compliance_status"] == PASS  # N2: verdict untouched
    assert (
        chem["audit_status"] == "uncertain"
        and chem["escalation_reason"] == "UNSUPPORTED_CONCLUSION"
        and "CONCLUSION_UNSUPPORTED" in chem["auditor_flags"]
        and chem["requires_human_review"]
    )
    sem = [e for e in api.events(wid) if e["type"] == "AUDIT_CHECK_RESULT" and e["data"].get("check") == "semantic" and e["data"].get("fault_injected")]
    assert sem and sem[0]["finding_id"] == chem["finding_id"]


def test_second_run_uses_cache_and_no_llm(api):
    api.run(CLEAN)
    calls = api.c.provider.calls
    wid, d = api.run(CLEAN)
    assert api.c.provider.calls == calls
    done = [e for e in api.events(wid) if e["type"] == "EXTRACTION_COMPLETED"]
    assert len(done) == 3 and all(e["data"]["cached"] for e in done)
    assert d["status"] == "COMPLETED"


# ------------------------------------------------------------------ robustness
def test_injection_pdf_end_to_end(api):
    wid, d = api.run([DEMO / "robustness" / "injection_test.pdf"])
    assert d["extractions"][0]["test_results"][0]["value"] == 19.5
    f = api.by_rule(d)
    assert f["CHEM_MAX"]["compliance_status"] == REVIEW and f["CHEM_MAX"]["escalation_reason"] == "UNVERIFIABLE_SOURCE"
    assert any(k.startswith("DOC_INTAKE") and v["escalation_reason"] == "UNVERIFIABLE_SOURCE" for k, v in f.items())
    assert d["status"] == "AWAITING_REVIEW"


def test_scan_like_pdf_escalates_unverifiable(api):
    wid, d = api.run([DEMO / "robustness" / "scan_like.pdf"])
    intake = [v for k, v in api.by_rule(d).items() if k.startswith("DOC_INTAKE")]
    assert any(v["escalation_reason"] == "UNVERIFIABLE_SOURCE" for v in intake)
    assert d["status"] == "AWAITING_REVIEW" and all(v["requires_human_review"] for v in intake)


def test_corrupt_document_is_isolated(api, tmp_path):
    bad = ("broken.pdf", b"%PDF-1.4\nthis is not a real pdf")
    wid, d = api.run([CLEAN[0], bad, CLEAN[1], CLEAN[2]])
    f = api.by_rule(d)
    intake = [v for k, v in f.items() if k.startswith("DOC_INTAKE")]
    assert len(intake) == 1 and intake[0]["escalation_reason"] == "EXTRACTION_FAILED" and "broken.pdf" in intake[0]["rule_title"]
    assert f["CHEM_MAX"]["compliance_status"] == PASS  # the rest of the pack continued
    assert d["status"] == "AWAITING_REVIEW"
    wid2, d2 = api.run([bad])  # nothing processable at all -> FAILED, with a reason
    assert d2["status"] == "FAILED" and "no document" in d2["failure_reason"]
    assert types(api.events(wid2))[-1] == "WORKFLOW_FAILED"


def test_upload_validation(make_api):
    api = make_api(max_upload_mb=1, max_files_per_upload=2)

    def err(r, status, code):
        assert r.status_code == status, r.text
        assert r.json()["error"]["code"] == code and "message" in r.json()["error"]

    err(api.upload([("notes.txt", b"hello world")]), 415, "UNSUPPORTED_FILE_TYPE")
    err(api.upload([("renamed.png", CLEAN[0].read_bytes())]), 415, "EXTENSION_MISMATCH")
    err(api.upload([("big.pdf", b"%PDF-1.4" + b"0" * (1024 * 1024 + 10))]), 413, "FILE_TOO_LARGE")
    err(api.upload([("e.pdf", b"")]), 400, "EMPTY_FILE")
    err(api.upload(CLEAN), 413, "TOO_MANY_FILES")
    r = api.client.post("/api/evidence/upload")
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert api.client.get("/api/workflows").json() == []  # a rejected upload never leaves a half-made workflow
    r = api.upload([CLEAN[0]], rule_set_id="nope@9")
    err(r, 404, "NOT_FOUND")
    assert api.client.get("/api/workflows/wf_missing").status_code == 404
    assert api.client.get("/api/nonexistent").json()["error"]["code"] == "NOT_FOUND"


def test_unknown_document_type_escalates(api):
    wid, d = api.run([("mystery.pdf", _plain_pdf("Some unrelated memo about lunch arrangements for the whole team next week."))])
    assert any(v["escalation_reason"] == "UNKNOWN_DOCUMENT_TYPE" for k, v in api.by_rule(d).items() if k.startswith("DOC_INTAKE"))


def _plain_pdf(text):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


# ------------------------------------------------------------------ lifecycle
def test_startup_marks_interrupted_workflows_failed(tmp_path):
    s = make_settings(tmp_path)
    with TestClient(create_app(s)) as first:
        c = first.app.state.container
        wid = c.repo.create_workflow(rule_set_id=c.default_rule_set_id, provider="mock", model="m")
        c.repo.set_status(wid, __import__("app.enums", fromlist=["WorkflowStatus"]).WorkflowStatus.EXTRACTING)
    with TestClient(create_app(s)) as second:
        d = second.get(f"/api/workflows/{wid}").json()
        assert d["status"] == "FAILED" and d["failure_reason"] == "interrupted"
        ev = second.get(f"/api/workflows/{wid}/events/history").json()
        assert ev[-1]["type"] == "WORKFLOW_FAILED" and "interrupted" in ev[-1]["message"]


def test_illegal_transitions_raise():
    from app.core.store import IllegalTransition, validate_transition
    from app.enums import WorkflowStatus as W

    validate_transition(W.CREATED, W.EXTRACTING)
    for old, new in [(W.CREATED, W.COMPLETED), (W.FAILED, W.EXTRACTING), (W.COMPLETED, W.AWAITING_REVIEW), (W.EXTRACTING, W.AUDITING), (W.AUDITING, W.EXTRACTING)]:
        with pytest.raises(IllegalTransition):
            validate_transition(old, new)


def test_workflow_timeout_becomes_failed(make_api):
    class Slow(MockProvider):
        async def generate_structured(self, **k):
            import asyncio

            await asyncio.sleep(5)
            return await super().generate_structured(**k)

    api = make_api(workflow_timeout_seconds=0.5, llm_timeout_seconds=30)
    api.c.provider.inner = Slow()
    wid, d = api.run([CLEAN[0]])
    assert d["status"] == "FAILED" and "timed out" in d["failure_reason"]
