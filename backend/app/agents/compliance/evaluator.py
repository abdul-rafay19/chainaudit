"""Agent 2: Compliance Evaluation. Statuses come straight from the deterministic rules engine."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.events import EventBus
from app.core.store import Repo, new_id
from app.enums import AgentName, DocType, EventType
from app.rules.engine import PROMPT_INJECTION_NOTE, EvidenceBundle, IntakeIssue, RuleResult, evaluate
from app.rules.models import RuleSet
from app.schemas.domain import ExtractedDocument, Finding


def intake_issues(repo: Repo, wid: str, docs: list[ExtractedDocument]) -> list[IntakeIssue]:
    issues: list[IntakeIssue] = []
    by_id = {d.document_id: d for d in docs}
    for row in repo.documents(wid):
        if row.status == "failed":
            issues.append(IntakeIssue(row.id, row.original_name, "extraction_failed", row.error or "extraction failed"))
            continue
        d = by_id.get(row.id)
        if d is None:
            continue
        if d.doc_type == DocType.unknown:
            issues.append(IntakeIssue(row.id, row.original_name, "unknown_type"))
        if d.image_only_pages:
            issues.append(IntakeIssue(row.id, row.original_name, "image_only", "pages " + ", ".join(map(str, d.image_only_pages))))
        if PROMPT_INJECTION_NOTE in d.extraction_notes:
            issues.append(IntakeIssue(row.id, row.original_name, "prompt_injection"))
    return issues


def apply_result(f: Finding, r: RuleResult, rs: RuleSet) -> None:
    """The single place a finding's compliance_status is written (from an engine result)."""
    f.compliance_status = r.status
    f.reason = r.reason
    f.escalation_reason = r.escalation_reason
    f.evidence = [e.model_copy(deep=True) for e in r.evidence]
    f.rule_title = r.title
    f.rule_set_label = rs.tag


class ComplianceAgent:
    def __init__(self, repo: Repo, bus: EventBus) -> None:
        self.repo, self.bus = repo, bus

    async def evaluate(self, wid: str, rs: RuleSet, evaluation_number: int, docs: list[ExtractedDocument], intake: list[IntakeIssue], *, replay: bool = False) -> list[Finding]:
        await self.bus.emit(
            wid,
            EventType.COMPLIANCE_CHECK_STARTED,
            agent=AgentName.compliance,
            replay=replay,
            message=f"Evaluating {rs.tag} (reference date {rs.reference_date.isoformat()}) against {len(docs)} document(s)",
            data={"evaluation_number": evaluation_number, "rule_set": rs.tag, "rule_count": len(rs.rules), "reference_date": rs.reference_date.isoformat()},
        )
        results = evaluate(rs, EvidenceBundle(docs, intake))
        findings: list[Finding] = []
        for r in results:
            f = Finding(
                finding_id=new_id("fnd"), workflow_id=wid, evaluation_number=evaluation_number, rule_id=r.rule_id, rule_title=r.title, compliance_status=r.status, reason=r.reason
            )
            apply_result(f, r, rs)
            await asyncio.to_thread(self.repo.save_finding, f)
            findings.append(f)
            await self.bus.emit(
                wid,
                EventType.FINDING_CREATED,
                agent=AgentName.compliance,
                finding_id=f.finding_id,
                replay=replay,
                message=f"{r.rule_id}: {r.status.value}",
                data={
                    "rule_id": r.rule_id,
                    "rule_title": r.title,
                    "compliance_status": r.status.value,
                    "escalation_reason": r.escalation_reason.value if r.escalation_reason else None,
                    "reason": r.reason,
                    "evaluation_number": evaluation_number,
                },
            )
        return findings

    async def reevaluate(
        self, wid: str, rs: RuleSet, findings: list[Finding], docs: list[ExtractedDocument], intake: list[IntakeIssue], *, reason: str, replay: bool = False
    ) -> list[dict[str, Any]]:
        """Deterministic re-evaluation after the EVIDENCE changed (never because an agent wanted a different verdict)."""
        results = {r.rule_id: r for r in evaluate(rs, EvidenceBundle(docs, intake))}
        changes: list[dict[str, Any]] = []
        for f in findings:
            r = results.get(f.rule_id)
            if r is None:
                continue
            before = {"compliance_status": f.compliance_status.value, "reason": f.reason}
            apply_result(f, r, rs)
            if before["compliance_status"] != f.compliance_status.value or before["reason"] != f.reason:
                changes.append({"finding_id": f.finding_id, "rule_id": f.rule_id, "before": before, "after": {"compliance_status": f.compliance_status.value, "reason": f.reason}})
            await asyncio.to_thread(self.repo.save_finding, f)
        if changes:
            summary = (
                "; ".join(
                    f"{c['rule_id']}: {c['before']['compliance_status']} -> {c['after']['compliance_status']}"
                    for c in changes
                    if c["before"]["compliance_status"] != c["after"]["compliance_status"]
                )
                or "reasons updated"
            )
            await self.bus.emit(
                wid,
                EventType.COMPLIANCE_CHECK_STARTED,
                agent=AgentName.compliance,
                replay=replay,
                message=f"Re-evaluated rules after evidence changed ({reason}): {summary}",
                data={"reevaluation": True, "reason": reason, "changes": changes},
            )
        return changes
