from __future__ import annotations

import json

from app.container import Container
from app.core.errors import not_found
from app.core.store import doc_info, status_counts
from app.enums import ReviewDecision, WorkflowStatus
from app.schemas.domain import AuditRecord, RuleSetInfo, WorkflowDetail, WorkflowOutcome

TERMINAL = {ReviewDecision.approved, ReviewDecision.edited, ReviewDecision.rejected}


def rule_set_info(c: Container, rule_set_id: str) -> RuleSetInfo:
    row = c.repo.get_rule_set(rule_set_id)
    if row is None:
        raise not_found("Rule set", rule_set_id)
    content = json.loads(row.content_json)
    return RuleSetInfo(id=row.id, name=row.name, version=row.version, label=content.get("label", ""), derived_from=row.derived_from, content=content)


def build_detail(c: Container, wid: str) -> WorkflowDetail:
    wf = c.repo.require_workflow(wid)
    findings = c.repo.findings(wid)
    live = [f for f in findings if not f.superseded]
    counts = status_counts(findings)
    needs = [f for f in live if f.requires_human_review]
    pending = [f for f in needs if f.review_decision not in TERMINAL]
    rs = rule_set_info(c, wf.rule_set_id)
    labels = ["Synthetic Demo Data", f"{rs.name} v{rs.version}"]
    if wf.replay:
        labels.append("REPLAY")
    if wf.provider == "mock":
        labels.append("Mock provider")
    outcome = WorkflowOutcome(
        **counts,
        needs_review=len(pending),
        reviewed=len(needs) - len(pending),
        summary=f"{counts['passing']} passing, {counts['not_satisfied']} requirement(s) not satisfied, {counts['human_review']} for human review; {len(pending)} awaiting a decision",
    )
    return WorkflowDetail(
        workflow_id=wid,
        status=WorkflowStatus(wf.status),
        created_at=wf.created_at,
        updated_at=wf.updated_at,
        provider=wf.provider,
        model=wf.model,
        replay=wf.replay,
        evaluation_number=wf.evaluation_number,
        failure_reason=wf.failure_reason,
        labels=labels,
        documents=[doc_info(d) for d in c.repo.documents(wid)],
        extractions=c.repo.extractions(wid),
        findings=findings,
        rule_set=rs,
        outcome=outcome,
    )


def build_audit(c: Container, wid: str) -> AuditRecord:
    d = build_detail(c, wid)
    ids = lambda name: sorted({str(x.value) for ex in d.extractions for x in [getattr(ex, name)] if x is not None and x.value is not None})  # noqa: E731
    return AuditRecord(
        workflow_id=wid,
        status=d.status,
        labels=d.labels,
        supplier_ids=ids("supplier_id"),
        batch_ids=ids("batch_id"),
        documents=d.documents,
        extractions=d.extractions,
        rule_set=d.rule_set,
        findings=d.findings,
        reviews=c.repo.reviews(wid),
        corrective_actions=c.repo.corrective_actions(wid),
        outbox=c.repo.outbox(wid),
        events=c.bus.history_sync(wid),
    )
