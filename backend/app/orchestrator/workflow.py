"""Workflow lifecycle: parallel extraction -> evaluation -> audit -> review. Failures become events."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from app.agents.auditor.auditor import AuditContext
from app.agents.compliance.evaluator import intake_issues
from app.agents.corrective.drafter import needs_draft
from app.core.errors import AppError, bad_request, conflict
from app.core.logging import get_logger, workflow_ctx
from app.core.storage import ValidatedUpload, store_bytes
from app.core.store import IllegalTransition
from app.enums import AgentName, EventType, ReviewDecision, WorkflowStatus
from app.rules.engine import core_reason
from app.rules.loader import RuleSetError, apply_overrides, base_id, derive_id, to_content
from app.schemas.domain import (
    DispatchRequest,
    DispatchResponse,
    ExtractedDocument,
    Finding,
    RerunResponse,
    ReviewRequest,
    ReviewResponse,
)

if TYPE_CHECKING:
    from app.container import Container

log = get_logger("orchestrator")
TERMINAL_DECISIONS = {ReviewDecision.approved, ReviewDecision.edited, ReviewDecision.rejected}
LABELS = ["Synthetic Demo Data", "Demo Buyer Framework"]


def _same_conclusion(a: Finding, b: Finding) -> bool:
    """Same rule, same verdict, same substance (ignoring the rule-set version tag)."""
    return a.rule_id == b.rule_id and a.compliance_status == b.compliance_status and core_reason(a.reason) == core_reason(b.reason)


class Orchestrator:
    def __init__(self, c: Container) -> None:
        self.c = c
        self._tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------ plumbing
    def _spawn(self, coro: Any) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        """Used by tests/shutdown: wait for background workflow tasks."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _set(self, wid: str, status: WorkflowStatus) -> None:
        await asyncio.to_thread(self.c.repo.set_status, wid, status)

    async def _fail(self, wid: str, reason: str, replay: bool = False) -> None:
        try:
            await asyncio.to_thread(self.c.repo.set_status, wid, WorkflowStatus.FAILED, failure_reason=reason)
        except IllegalTransition:
            return
        await self.c.bus.emit(wid, EventType.WORKFLOW_FAILED, agent=AgentName.orchestrator, message=f"Workflow failed: {reason}", data={"reason": reason}, replay=replay)

    async def _guard(self, wid: str, coro: Any) -> None:
        workflow_ctx.set(wid)
        try:
            await asyncio.wait_for(coro, timeout=self.c.settings.workflow_timeout_seconds)
        except TimeoutError:
            await self._fail(wid, "workflow timed out")
        except asyncio.CancelledError:
            await self._fail(wid, "interrupted")
            raise
        except Exception as e:  # never a raw traceback to clients: becomes an event
            log.exception("workflow crashed")
            await self._fail(wid, f"unexpected error ({type(e).__name__})")

    async def recover_interrupted(self) -> list[str]:
        ids = await asyncio.to_thread(self.c.repo.running_workflows)
        for wid in ids:
            await self._fail(wid, "interrupted")
        return ids

    # ------------------------------------------------------------ ingestion
    async def add_documents(self, wid: str, uploads: list[ValidatedUpload]) -> list[str]:
        ids: list[str] = []
        for u in uploads:
            path = await asyncio.to_thread(store_bytes, self.c.settings.upload_dir, wid, u)
            did = await asyncio.to_thread(self.c.repo.add_document, wid=wid, original_name=u.display_name, stored_path=str(path), sha256=u.sha256, mime=u.mime, size=len(u.data))
            ids.append(did)
            await self.c.bus.emit(
                wid,
                EventType.DOCUMENT_RECEIVED,
                agent=AgentName.orchestrator,
                document_id=did,
                message=f"Received {u.display_name}",
                data={"document": u.display_name, "sha256": u.sha256, "size": len(u.data), "mime": u.mime},
            )
        return ids

    async def create_from_uploads(self, uploads: list[ValidatedUpload], rule_set_id: str | None) -> tuple[str, list[str]]:
        rsid = rule_set_id or self.c.default_rule_set_id
        rs = self.c.ruleset(rsid)  # 404 for unknown id
        wid = await asyncio.to_thread(self.c.repo.create_workflow, rule_set_id=rsid, provider=self.c.provider.name, model=self.c.provider.model)
        await self.c.bus.emit(
            wid,
            EventType.WORKFLOW_CREATED,
            agent=AgentName.orchestrator,
            message=f"Workflow created with {rs.tag}",
            data={
                "rule_set_id": rsid,
                "rule_set": rs.tag,
                "rule_set_label": rs.label,
                "provider": self.c.provider.name,
                "model": self.c.provider.model,
                "mock": self.c.provider.name == "mock",
                "labels": LABELS,
                "document_count": len(uploads),
            },
        )
        ids = await self.add_documents(wid, uploads)
        self._spawn(self._guard(wid, self._run(wid, ids)))
        return wid, ids

    async def add_evidence(self, wid: str, uploads: list[ValidatedUpload]) -> list[str]:
        async with self.c.lock(wid):
            wf = await asyncio.to_thread(self.c.repo.require_workflow, wid)
            if WorkflowStatus(wf.status) != WorkflowStatus.AWAITING_EVIDENCE:
                raise conflict("WORKFLOW_STATE", f"Additional evidence is only accepted while the workflow is AWAITING_EVIDENCE (currently {wf.status})")
            ids = await self.add_documents(wid, uploads)
            await self._set(wid, WorkflowStatus.EXTRACTING)
        self._spawn(self._guard(wid, self._run(wid, ids)))
        return ids

    # ------------------------------------------------------------ pipeline
    async def _run(self, wid: str, doc_ids: list[str]) -> None:
        c = self.c
        if WorkflowStatus((await asyncio.to_thread(c.repo.require_workflow, wid)).status) == WorkflowStatus.CREATED:
            await self._set(wid, WorkflowStatus.EXTRACTING)
        sem = asyncio.Semaphore(c.settings.extraction_concurrency)
        results = await asyncio.gather(*(c.extractor.process(wid, d, sem) for d in doc_ids), return_exceptions=True)
        ok = [r for r in results if isinstance(r, ExtractedDocument)]
        existing = await asyncio.to_thread(c.repo.extractions, wid)
        if not ok and not existing:
            await self._fail(wid, "no document could be processed")
            return
        wf = await asyncio.to_thread(c.repo.require_workflow, wid)
        await self._evaluate_and_audit(wid, c.ruleset(wf.rule_set_id), rerun=False)

    async def _evaluate_and_audit(self, wid: str, rs: Any, *, rerun: bool) -> list[Finding]:
        c = self.c
        await self._set(wid, WorkflowStatus.EVALUATING)
        docs = await asyncio.to_thread(c.repo.extractions, wid)
        intake = await asyncio.to_thread(intake_issues, c.repo, wid, docs)
        history = await asyncio.to_thread(c.repo.findings, wid)  # every evaluation so far (drafts can be reused)
        old = [f for f in history if not f.superseded]  # the evaluation being replaced (decisions carry over from here only)
        for f in old:
            f.superseded = True
            await asyncio.to_thread(c.repo.save_finding, f)
        wf = await asyncio.to_thread(c.repo.require_workflow, wid)
        number = wf.evaluation_number + 1
        await asyncio.to_thread(c.repo.update_workflow, wid, evaluation_number=number)
        findings = await c.compliance.evaluate(wid, rs, number, docs, intake)
        await self._set(wid, WorkflowStatus.AUDITING)

        async def reeval(docs_: list[ExtractedDocument], reason: str) -> list[dict[str, Any]]:
            return await c.compliance.reevaluate(wid, rs, findings, docs_, intake, reason=reason)

        ctx = AuditContext(wid=wid, reference_date=rs.reference_date, reevaluate=reeval, allow_reextraction=not rerun, semantic_cache_only=rerun)
        await c.auditor.run(ctx, findings, docs)

        supplier = next((d.supplier_id.value for d in docs if d.supplier_id and d.supplier_id.value), "") or ""
        for f in findings:
            prior = next((p for p in old if _same_conclusion(p, f)), None)
            if prior is not None and prior.review_decision in TERMINAL_DECISIONS:  # unchanged finding keeps its human decision
                f.review_decision = prior.review_decision
            donor = next((p for p in reversed(history) if _same_conclusion(p, f) and p.corrective_action is not None), None)
            if donor is not None and needs_draft(f):  # an identical conclusion already has a draft: reuse, no model call
                f.corrective_action = donor.corrective_action
            if prior is not None or donor is not None:
                await asyncio.to_thread(c.repo.save_finding, f)
            if f.corrective_action is None and needs_draft(f):
                await c.corrective.draft(wid, f, str(supplier))
        await self._finish(wid)
        return findings

    def _pending(self, findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
        live = [f for f in findings if not f.superseded and f.requires_human_review]
        undecided = [f for f in live if f.review_decision not in TERMINAL_DECISIONS]
        more = [f for f in undecided if f.review_decision == ReviewDecision.more_evidence]
        return undecided, more

    async def _finish(self, wid: str) -> None:
        findings = await asyncio.to_thread(self.c.repo.findings, wid, include_superseded=False)
        undecided, _ = self._pending(findings)
        if undecided:
            await self._set(wid, WorkflowStatus.AWAITING_REVIEW)
        else:
            await self._set(wid, WorkflowStatus.COMPLETED)
            await self.c.bus.emit(
                wid, EventType.WORKFLOW_COMPLETED, agent=AgentName.orchestrator, message="Workflow completed: no open items require human review", data={"findings": len(findings)}
            )

    # ------------------------------------------------------------ rerun rules
    async def rerun_rules(self, wid: str, rule_set_id: str | None, overrides: dict[str, dict[str, Any]] | None) -> RerunResponse:
        c = self.c
        if bool(rule_set_id) == bool(overrides):
            raise bad_request("RERUN_BAD_REQUEST", "Provide exactly one of rule_set_id or overrides")
        async with c.lock(wid):
            wf = await asyncio.to_thread(c.repo.require_workflow, wid)
            if WorkflowStatus(wf.status) not in {WorkflowStatus.AWAITING_REVIEW, WorkflowStatus.COMPLETED, WorkflowStatus.AWAITING_EVIDENCE}:
                raise conflict("WORKFLOW_STATE", f"Rules can only be re-run on a settled workflow (currently {wf.status})")
            if rule_set_id:
                rs = c.ruleset(rule_set_id)
                new_id_ = rule_set_id
            else:
                base = c.ruleset(wf.rule_set_id)
                root = base
                row = await asyncio.to_thread(c.repo.get_rule_set, wf.rule_set_id)
                while row is not None and row.derived_from:  # walk to the root rule set
                    root = c.ruleset(row.derived_from)
                    row = await asyncio.to_thread(c.repo.get_rule_set, row.derived_from)
                try:
                    rs = apply_overrides(base, overrides or {}, root)
                except RuleSetError as e:
                    raise AppError(422, "INVALID_OVERRIDES", str(e)) from e
                new_id_ = derive_id(base_id(base), to_content(rs))
                await asyncio.to_thread(c.repo.save_rule_set, id_=new_id_, name=rs.framework, version=rs.version, content=to_content(rs), derived_from=wf.rule_set_id)
            before = {f.rule_id: f for f in await asyncio.to_thread(c.repo.findings, wid, include_superseded=False)}
            t0, calls0 = time.perf_counter(), c.provider.calls
            await asyncio.to_thread(c.repo.update_workflow, wid, rule_set_id=new_id_)
            await c.bus.emit(
                wid,
                EventType.RULES_RERUN_STARTED,
                agent=AgentName.orchestrator,
                message=f"Re-running rules with {rs.tag} on cached evidence (no extraction re-run)",
                data={"rule_set_id": new_id_, "rule_set": rs.tag, "overrides": overrides, "derived_from": wf.rule_set_id if overrides else None},
            )
            findings = await self._evaluate_and_audit(wid, rs, rerun=True)
            changed = [
                {"rule_id": f.rule_id, "before": before[f.rule_id].compliance_status.value, "after": f.compliance_status.value}
                for f in findings
                if f.rule_id in before and before[f.rule_id].compliance_status != f.compliance_status
            ]
            llm_calls = c.provider.calls - calls0
            await c.bus.emit(
                wid,
                EventType.RULES_RERUN_COMPLETED,
                agent=AgentName.orchestrator,
                message="Re-evaluated cached evidence" + (f"; {len(changed)} finding(s) changed" if changed else "; no finding changed"),
                data={"rule_set_id": new_id_, "changed": changed, "llm_calls": llm_calls, "duration_ms": round((time.perf_counter() - t0) * 1000), "extraction_rerun": False},
            )
            wf2 = await asyncio.to_thread(c.repo.require_workflow, wid)
            return RerunResponse(workflow_id=wid, rule_set_id=new_id_, evaluation_number=wf2.evaluation_number, changed=changed, llm_calls=llm_calls)

    # ------------------------------------------------------------ review
    async def submit_review(self, finding_id: str, req: ReviewRequest) -> ReviewResponse:
        c = self.c
        f0 = await asyncio.to_thread(c.repo.require_finding, finding_id)
        async with c.lock(f0.workflow_id):
            f = await asyncio.to_thread(c.repo.require_finding, finding_id)
            wf = await asyncio.to_thread(c.repo.require_workflow, f.workflow_id)
            status = WorkflowStatus(wf.status)
            if f.superseded:
                raise conflict("FINDING_SUPERSEDED", "This finding was superseded by a newer evaluation")
            if not f.requires_human_review:
                raise conflict("REVIEW_NOT_REQUIRED", "This finding does not require human review")
            if status not in {WorkflowStatus.AWAITING_REVIEW, WorkflowStatus.AWAITING_EVIDENCE}:
                raise conflict("WORKFLOW_STATE", f"Reviews are not accepted while the workflow is {status.value}")
            if req.decision == ReviewDecision.edited and not (req.edited_text and req.edited_text.strip()):
                raise AppError(422, "EDITED_TEXT_REQUIRED", "A decision of 'edited' requires edited_text")
            existing = f.review_decision
            if existing is not None and existing != ReviewDecision.more_evidence and existing != req.decision:
                raise conflict("DECISION_ALREADY_RECORDED", f"A different decision ({existing.value}) was already recorded for this finding")
            latest = await asyncio.to_thread(c.repo.latest_review, finding_id)
            same = existing == req.decision and latest is not None and latest["edited_text"] == req.edited_text
            at = (
                latest["created_at"]
                if same and latest
                else await asyncio.to_thread(
                    c.repo.add_review,
                    finding_id=finding_id,
                    workflow_id=f.workflow_id,
                    decision=req.decision.value,
                    edited_text=req.edited_text,
                    comment=req.comment,
                    reviewer=req.reviewer,
                )
            )
            if not same:
                f.review_decision = req.decision
                await asyncio.to_thread(c.repo.save_finding, f)
                await c.bus.emit(
                    f.workflow_id,
                    EventType.REVIEW_DECISION_RECORDED,
                    agent=AgentName.reviewer,
                    finding_id=finding_id,
                    message=f"{req.reviewer} recorded '{req.decision.value}' for {f.rule_id}",
                    data={"decision": req.decision.value, "reviewer": req.reviewer, "rule_id": f.rule_id, "has_comment": bool(req.comment)},
                )
            findings = await asyncio.to_thread(c.repo.findings, f.workflow_id, include_superseded=False)
            undecided, more = self._pending(findings)
            if not undecided:
                await self._set(f.workflow_id, WorkflowStatus.COMPLETED)
                await c.bus.emit(
                    f.workflow_id,
                    EventType.WORKFLOW_COMPLETED,
                    agent=AgentName.orchestrator,
                    message="All required reviews decided: workflow completed",
                    data={"findings": len(findings)},
                )
            elif more and status == WorkflowStatus.AWAITING_REVIEW:
                await self._set(f.workflow_id, WorkflowStatus.AWAITING_EVIDENCE)
            elif not more and status == WorkflowStatus.AWAITING_EVIDENCE:
                await self._set(f.workflow_id, WorkflowStatus.AWAITING_REVIEW)
            wf2 = await asyncio.to_thread(c.repo.require_workflow, f.workflow_id)
            return ReviewResponse(finding_id=finding_id, decision=req.decision, workflow_status=WorkflowStatus(wf2.status), recorded_at=at)

    # ------------------------------------------------------------ dispatch (demo outbox only)
    async def dispatch(self, finding_id: str, req: DispatchRequest) -> DispatchResponse:
        c = self.c
        f = await asyncio.to_thread(c.repo.require_finding, finding_id)
        async with c.lock(f.workflow_id):
            f = await asyncio.to_thread(c.repo.require_finding, finding_id)
            if f.superseded:
                raise conflict("FINDING_SUPERSEDED", "This finding was superseded by a newer evaluation")
            if f.review_decision not in {ReviewDecision.approved, ReviewDecision.edited}:
                raise conflict("DISPATCH_NOT_APPROVED", "Nothing is dispatched without a recorded human approval (approved or edited)")
            review = await asyncio.to_thread(c.repo.latest_review, finding_id)
            if f.review_decision == ReviewDecision.edited and review and review["edited_text"]:
                text = review["edited_text"]
            elif f.corrective_action is not None:
                text = f.corrective_action.en if req.language == "en" else f.corrective_action.roman_ur
            else:
                raise conflict("NO_DRAFT", "There is no corrective-action draft to send for this finding")
            oid = await asyncio.to_thread(c.repo.add_outbox, finding_id=finding_id, workflow_id=f.workflow_id, channel=req.channel, language=req.language, text=text)
            await c.bus.emit(
                f.workflow_id,
                EventType.DISPATCH_RECORDED,
                agent=AgentName.orchestrator,
                finding_id=finding_id,
                message=f"Recorded in demo outbox ({req.channel}). Nothing was actually sent.",
                data={"channel": req.channel, "language": req.language, "outbox_id": oid, "demo_only": True},
            )
            return DispatchResponse(finding_id=finding_id, channel=req.channel, language=req.language, text=text, outbox_id=oid)
