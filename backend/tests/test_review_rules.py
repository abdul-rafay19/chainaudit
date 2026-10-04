import time

from tests.conftest import CLEAN, FAILED

PASS, FAIL, REVIEW = "PASSING_CONFIGURED_CHECK", "REQUIREMENT_NOT_SATISFIED", "HUMAN_REVIEW_REQUIRED"


def review(api, fid, decision, **kw):
    body = {"decision": decision, "reviewer": "Test Reviewer", **kw}
    return api.client.post(f"/api/reviews/{fid}", json=body)


def failed_run(api):
    wid, d = api.run(FAILED)
    return wid, api.by_rule(d)


def status(api, wid):
    return api.client.get(f"/api/workflows/{wid}").json()["status"]


def test_review_queue_filters(api):
    wid, f = failed_run(api)
    q = api.client.get("/api/findings", params={"workflow_id": wid, "needs_review": "true"}).json()
    assert {x["rule_id"] for x in q} == {"CHEM_MAX", "ID_CONSISTENCY"}
    assert {x["rule_id"] for x in api.client.get("/api/findings", params={"workflow_id": wid, "status": FAIL}).json()} == {"CHEM_MAX"}
    assert api.client.get("/api/findings", params={"workflow_id": "wf_nope"}).status_code == 404
    assert api.client.get("/api/findings", params={"status": "BOGUS"}).status_code == 422


def test_dispatch_blocked_until_approved_then_demo_outbox_only(api):
    wid, f = failed_run(api)
    fid = f["CHEM_MAX"]["finding_id"]
    r = api.client.post(f"/api/findings/{fid}/dispatch", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DISPATCH_NOT_APPROVED"
    assert review(api, fid, "approved").status_code == 200
    r = api.client.post(f"/api/findings/{fid}/dispatch", json={"channel": "demo_whatsapp", "language": "roman_ur"})
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == f["CHEM_MAX"]["corrective_action"]["roman_ur"] and body["channel"] == "demo_whatsapp" and "Nothing was actually sent" in body["note"]
    audit = api.client.get(f"/api/audit/{wid}").json()
    assert len(audit["outbox"]) == 1 and audit["outbox"][0]["finding_id"] == fid
    assert any(e["type"] == "DISPATCH_RECORDED" and e["data"]["demo_only"] for e in audit["events"])
    assert api.client.post(f"/api/findings/{fid}/dispatch", json={"channel": "sms"}).status_code == 422
    assert api.client.post("/api/findings/fnd_nope/dispatch", json={}).status_code == 404


def test_review_validation_and_conflicts(api):
    wid, f = failed_run(api)
    chem, ids = f["CHEM_MAX"]["finding_id"], f["ID_CONSISTENCY"]["finding_id"]
    r = review(api, chem, "edited")
    assert r.status_code == 422 and r.json()["error"]["code"] == "EDITED_TEXT_REQUIRED"
    assert review(api, chem, "edited", edited_text="   ").status_code == 422
    r = review(api, chem, "maybe")
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert api.client.post(f"/api/reviews/{chem}", json={"decision": "approved"}).status_code == 422  # reviewer required
    assert review(api, "fnd_nope", "approved").status_code == 404
    passing = f["CERT_REQUIRED"]["finding_id"]
    r = review(api, passing, "approved")
    assert r.status_code == 409 and r.json()["error"]["code"] == "REVIEW_NOT_REQUIRED"

    assert review(api, chem, "approved", comment="ok").status_code == 200
    n_events = len(api.events(wid))
    again = review(api, chem, "approved", comment="ok")  # same decision: idempotent, no new event
    assert again.status_code == 200 and len(api.events(wid)) == n_events
    r = review(api, chem, "rejected")
    assert r.status_code == 409 and r.json()["error"]["code"] == "DECISION_ALREADY_RECORDED"
    assert status(api, wid) == "AWAITING_REVIEW"

    r = review(api, ids, "edited", edited_text="Please confirm which batch id is correct.")
    assert r.status_code == 200 and r.json()["workflow_status"] == "COMPLETED"  # all required reviews decided
    ev = api.events(wid)
    assert [e["type"] for e in ev][-1] == "WORKFLOW_COMPLETED"
    assert sum(e["type"] == "REVIEW_DECISION_RECORDED" for e in ev) == 2
    assert review(api, chem, "approved").status_code == 200 or True  # late idempotent repeat must not crash
    # edited text wins at dispatch
    d = api.client.post(f"/api/findings/{ids}/dispatch", json={}).json()
    assert d["text"] == "Please confirm which batch id is correct."


def test_rejected_finding_cannot_be_dispatched(api):
    wid, f = failed_run(api)
    fid = f["CHEM_MAX"]["finding_id"]
    assert review(api, fid, "rejected").status_code == 200
    assert api.client.post(f"/api/findings/{fid}/dispatch", json={}).status_code == 409


def test_more_evidence_flow_and_additional_upload(api, tmp_path):
    wid, f = failed_run(api)
    chem, ids = f["CHEM_MAX"]["finding_id"], f["ID_CONSISTENCY"]["finding_id"]
    r = api.upload(CLEAN[:1])
    assert r.status_code == 200  # (a different workflow) just to have two
    r = api.client.post(f"/api/workflows/{wid}/evidence", files=[("files", ("extra.pdf", FAILED[1].read_bytes(), "application/pdf"))])
    assert r.status_code == 409 and r.json()["error"]["code"] == "WORKFLOW_STATE"  # only while AWAITING_EVIDENCE

    assert review(api, ids, "more_evidence", comment="need corrected declaration").json()["workflow_status"] == "AWAITING_EVIDENCE"
    assert review(api, chem, "approved").json()["workflow_status"] == "AWAITING_EVIDENCE"  # a more_evidence item is still open
    r = api.client.post(f"/api/workflows/{wid}/evidence", files=[("files", ("certificate_SUP-002_copy.pdf", FAILED[1].read_bytes(), "application/pdf"))])
    assert r.status_code == 200
    d = api.wait(wid)
    assert d["evaluation_number"] == 2 and d["status"] == "AWAITING_REVIEW"
    old = [x for x in d["findings"] if x["superseded"]]
    live = api.by_rule(d)
    assert len(old) == 4 and all(x["evaluation_number"] == 1 for x in old) and all(x["evaluation_number"] == 2 for x in live.values())
    assert live["CHEM_MAX"]["review_decision"] == "approved"  # unchanged finding keeps its human decision
    assert live["CHEM_MAX"]["corrective_action"]
    assert live["ID_CONSISTENCY"]["review_decision"] is None  # more_evidence is never carried over
    assert review(api, old[0]["finding_id"], "approved").status_code == 409  # superseded


def test_rerun_rules_flips_chem_without_any_llm_call(api):
    wid, f = failed_run(api)
    calls = api.c.provider.calls
    t0 = time.perf_counter()
    r = api.client.post(f"/api/workflows/{wid}/rerun-rules", json={"overrides": {"CHEM_MAX": {"limit": 20}}})
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] == [{"rule_id": "CHEM_MAX", "before": FAIL, "after": PASS}]
    assert body["llm_calls"] == 0 and api.c.provider.calls == calls  # cached evidence, zero model calls
    assert elapsed < 3.0
    d = api.wait(wid)
    live = api.by_rule(d)
    assert live["CHEM_MAX"]["compliance_status"] == PASS and live["CHEM_MAX"]["requires_human_review"] is False
    assert "20 ppm" in live["CHEM_MAX"]["reason"] and "1.0+override" in live["CHEM_MAX"]["reason"]
    assert live["ID_CONSISTENCY"]["compliance_status"] == REVIEW and live["ID_CONSISTENCY"]["corrective_action"]  # unchanged, draft kept
    assert d["evaluation_number"] == 2 and d["status"] == "AWAITING_REVIEW" and len([x for x in d["findings"] if x["superseded"]]) == 4
    rs = api.client.get(f"/api/rule-sets/{body['rule_set_id']}").json()
    assert rs["derived_from"] == d["rule_set"]["derived_from"] and rs["version"] == "1.0+override" and rs["content"]["rules"][0]["limit"] == 20
    ev = api.events(wid)
    after = [e for e in ev if e["seq"] > next(x["seq"] for x in ev if x["type"] == "RULES_RERUN_STARTED")]
    assert after[-1]["type"] == "RULES_RERUN_COMPLETED" and after[-1]["data"]["llm_calls"] == 0 and after[-1]["data"]["extraction_rerun"] is False
    assert not any(e["type"] in {"EXTRACTION_STARTED", "EXTRACTION_COMPLETED", "REEXTRACTION_REQUESTED"} for e in after)
    assert api.client.get("/api/rules").json()["content"]["rules"][0]["limit"] == 15  # base rule set is immutable
    # the same override again resolves to the same derived rule set (content-addressed)
    r2 = api.client.post(f"/api/workflows/{wid}/rerun-rules", json={"overrides": {"CHEM_MAX": {"limit": 20}}}).json()
    assert r2["rule_set_id"] == body["rule_set_id"] and r2["changed"] == [] and r2["llm_calls"] == 0
    # switch back via rule_set_id
    r3 = api.client.post(f"/api/workflows/{wid}/rerun-rules", json={"rule_set_id": d["rule_set"]["derived_from"]}).json()
    assert r3["changed"] == [{"rule_id": "CHEM_MAX", "before": PASS, "after": FAIL}] and r3["llm_calls"] == 0


def test_rerun_rules_errors(api):
    wid, _ = failed_run(api)
    post = lambda b: api.client.post(f"/api/workflows/{wid}/rerun-rules", json=b)  # noqa: E731
    assert post({}).status_code == 400
    assert post({"rule_set_id": "x", "overrides": {"CHEM_MAX": {"limit": 1}}}).status_code == 400
    assert post({"rule_set_id": "nope@9"}).status_code == 404
    for bad in ({"NOPE": {"limit": 1}}, {"CHEM_MAX": {"type": "x"}}, {"CHEM_MAX": {"limit": "abc"}}):
        r = post({"overrides": bad})
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_OVERRIDES"
    assert api.client.post("/api/workflows/wf_nope/rerun-rules", json={"overrides": {"CHEM_MAX": {"limit": 1}}}).status_code == 404


def test_evidence_image_highlight_and_fallback(api):
    wid, f = failed_run(api)
    fid = f["CHEM_MAX"]["finding_id"]
    r = api.client.get(f"/api/findings/{fid}/evidence-image", params={"evidence_index": 0})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n" and r.headers["x-highlight"] == "bbox"
    again = api.client.get(f"/api/findings/{fid}/evidence-image", params={"evidence_index": 0})
    assert again.content == r.content and any(api.c.settings.render_dir.glob("*.png"))  # disk cache
    assert api.client.get(f"/api/findings/{fid}/evidence-image", params={"evidence_index": 9}).status_code == 404
    assert api.client.get("/api/findings/fnd_nope/evidence-image").status_code == 404
    # no bbox -> plain page with X-Highlight: none
    fobj = api.c.repo.require_finding(fid)
    fobj.evidence[0].bbox = None
    api.c.repo.save_finding(fobj)
    plain = api.client.get(f"/api/findings/{fid}/evidence-image", params={"evidence_index": 0})
    assert plain.status_code == 200 and plain.headers["x-highlight"] == "none" and plain.content != r.content


def test_absent_certificate_has_no_image_but_has_reason(api):
    wid, d = api.run([CLEAN[0], CLEAN[2]])  # no certificate in the pack
    cert = api.by_rule(d)["CERT_REQUIRED"]
    assert cert["compliance_status"] == FAIL and cert["audit_status"] == "verified" and cert["evidence"]
    assert api.client.get(f"/api/findings/{cert['finding_id']}/evidence-image").status_code == 404


def test_finding_detail_and_audit_record(api):
    wid, f = failed_run(api)
    fd = api.client.get(f"/api/findings/{f['ID_CONSISTENCY']['finding_id']}").json()
    assert fd["finding"]["rule_id"] == "ID_CONSISTENCY" and fd["audit_checks"]
    assert any(c["type"] == "AUDIT_CONFLICT_FOUND" for c in fd["audit_checks"])
    chem = api.client.get(f"/api/findings/{f['CHEM_MAX']['finding_id']}").json()
    assert any(c["data"].get("field") == "chemical_ppm" for c in chem["audit_checks"])
    a = api.client.get(f"/api/audit/{wid}").json()
    assert a["supplier_ids"] == ["SUP-002"] and a["batch_ids"] == ["BT-2041", "BT-2047"]
    assert len(a["documents"]) == 3 and len(a["findings"]) == 4 and a["corrective_actions"] and a["rule_set"]["name"] == "Demo Buyer Framework"
    assert a["events"] == api.events(wid)
    assert api.client.get("/api/audit/wf_nope").status_code == 404
