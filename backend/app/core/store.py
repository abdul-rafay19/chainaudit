"""Repository: all synchronous DB access in one place (call via asyncio.to_thread from async code)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import func, select

from app.core.errors import AppError, not_found
from app.db import Database
from app.enums import ComplianceStatus, WorkflowStatus
from app.models.tables import (
    CorrectiveActionRow,
    DocumentRow,
    ExtractionCacheRow,
    ExtractionRow,
    FindingRow,
    OutboxRow,
    ReviewRow,
    RuleSetRow,
    SemanticCacheRow,
    WorkflowRow,
    utcnow_iso,
)
from app.schemas.domain import DocumentInfo, ExtractedDocument, Finding, WorkflowSummary

ALLOWED_TRANSITIONS: dict[WorkflowStatus, set[WorkflowStatus]] = {
    WorkflowStatus.CREATED: {WorkflowStatus.EXTRACTING, WorkflowStatus.FAILED},
    WorkflowStatus.EXTRACTING: {WorkflowStatus.EVALUATING, WorkflowStatus.FAILED},
    WorkflowStatus.EVALUATING: {WorkflowStatus.AUDITING, WorkflowStatus.FAILED},
    WorkflowStatus.AUDITING: {WorkflowStatus.AWAITING_REVIEW, WorkflowStatus.COMPLETED, WorkflowStatus.FAILED},
    WorkflowStatus.AWAITING_REVIEW: {
        WorkflowStatus.COMPLETED,
        WorkflowStatus.AWAITING_EVIDENCE,
        WorkflowStatus.EVALUATING,
        WorkflowStatus.EXTRACTING,
        WorkflowStatus.FAILED,
    },
    WorkflowStatus.AWAITING_EVIDENCE: {WorkflowStatus.EXTRACTING, WorkflowStatus.EVALUATING, WorkflowStatus.AWAITING_REVIEW, WorkflowStatus.COMPLETED, WorkflowStatus.FAILED},
    WorkflowStatus.COMPLETED: {WorkflowStatus.EVALUATING, WorkflowStatus.EXTRACTING},
    WorkflowStatus.FAILED: set(),
}
RUNNING_STATES = {WorkflowStatus.EXTRACTING, WorkflowStatus.EVALUATING, WorkflowStatus.AUDITING}


class IllegalTransition(RuntimeError):
    pass


def validate_transition(old: WorkflowStatus, new: WorkflowStatus) -> None:
    """The single place where workflow state transitions are validated."""
    if new not in ALLOWED_TRANSITIONS[old]:
        raise IllegalTransition(f"Illegal workflow transition {old.value} -> {new.value}")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def doc_info(r: DocumentRow) -> DocumentInfo:
    return DocumentInfo(
        document_id=r.id,
        original_name=r.original_name,
        sha256=r.sha256,
        mime=r.mime,
        size=r.size,
        doc_type=r.doc_type,  # type: ignore[arg-type]
        page_count=r.page_count,
        status=r.status,
        error=r.error,
    )


class Repo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---- workflows -----------------------------------------------------
    def create_workflow(self, *, rule_set_id: str, provider: str, model: str, replay: bool = False, replay_name: str | None = None) -> str:
        wid = new_id("wf")
        with self.db.session() as s:
            s.add(WorkflowRow(id=wid, status=WorkflowStatus.CREATED.value, rule_set_id=rule_set_id, provider=provider, model=model, replay=replay, replay_name=replay_name))
        return wid

    def get_workflow(self, wid: str) -> WorkflowRow | None:
        with self.db.session() as s:
            return s.get(WorkflowRow, wid)

    def require_workflow(self, wid: str) -> WorkflowRow:
        w = self.get_workflow(wid)
        if w is None:
            raise not_found("Workflow", wid)
        return w

    def set_status(self, wid: str, new: WorkflowStatus, *, failure_reason: str | None = None) -> WorkflowStatus:
        with self.db.session() as s:
            w = s.get(WorkflowRow, wid)
            if w is None:
                raise not_found("Workflow", wid)
            old = WorkflowStatus(w.status)
            validate_transition(old, new)
            w.status = new.value
            w.updated_at = utcnow_iso()
            if failure_reason is not None:
                w.failure_reason = failure_reason
            return old

    def update_workflow(self, wid: str, **fields: Any) -> None:
        with self.db.session() as s:
            w = s.get(WorkflowRow, wid)
            if w is None:
                raise not_found("Workflow", wid)
            for k, v in fields.items():
                setattr(w, k, v)
            w.updated_at = utcnow_iso()

    def list_workflows(self) -> list[WorkflowSummary]:
        with self.db.session() as s:
            out = []
            for w in s.scalars(select(WorkflowRow).order_by(WorkflowRow.created_at.desc())):
                dc = s.scalar(select(func.count()).select_from(DocumentRow).where(DocumentRow.workflow_id == w.id)) or 0
                fc = s.scalar(select(func.count()).select_from(FindingRow).where(FindingRow.workflow_id == w.id, FindingRow.superseded.is_(False))) or 0
                nr = (
                    s.scalar(
                        select(func.count())
                        .select_from(FindingRow)
                        .where(FindingRow.workflow_id == w.id, FindingRow.superseded.is_(False), FindingRow.requires_human_review.is_(True), FindingRow.review_decision.is_(None))
                    )
                    or 0
                )
                out.append(
                    WorkflowSummary(
                        workflow_id=w.id,
                        status=WorkflowStatus(w.status),
                        created_at=w.created_at,
                        updated_at=w.updated_at,
                        provider=w.provider,
                        model=w.model,
                        replay=w.replay,
                        document_count=dc,
                        finding_count=fc,
                        needs_review_count=nr,
                        rule_set_id=w.rule_set_id,
                    )
                )
            return out

    def running_workflows(self) -> list[str]:
        with self.db.session() as s:
            return [w.id for w in s.scalars(select(WorkflowRow)) if WorkflowStatus(w.status) in RUNNING_STATES]

    # ---- documents -----------------------------------------------------
    def add_document(self, *, wid: str, original_name: str, stored_path: str, sha256: str, mime: str, size: int, page_count: int | None = None, doc_id: str | None = None) -> str:
        did = doc_id or new_id("doc")
        with self.db.session() as s:
            s.add(DocumentRow(id=did, workflow_id=wid, original_name=original_name, stored_path=stored_path, sha256=sha256, mime=mime, size=size, page_count=page_count))
        return did

    def get_document(self, did: str) -> DocumentRow | None:
        with self.db.session() as s:
            return s.get(DocumentRow, did)

    def documents(self, wid: str) -> list[DocumentRow]:
        with self.db.session() as s:
            return list(s.scalars(select(DocumentRow).where(DocumentRow.workflow_id == wid).order_by(DocumentRow.created_at, DocumentRow.id)))

    def update_document(self, did: str, **fields: Any) -> None:
        with self.db.session() as s:
            d = s.get(DocumentRow, did)
            if d is None:
                return
            for k, v in fields.items():
                setattr(d, k, v)

    # ---- extractions ---------------------------------------------------
    def save_extraction(self, doc: ExtractedDocument, cache_key: str, *, write_cache: bool = True) -> None:
        payload = doc.model_dump_json()
        with self.db.session() as s:
            row = s.scalar(select(ExtractionRow).where(ExtractionRow.document_id == doc.document_id))
            if row is None:
                s.add(
                    ExtractionRow(
                        document_id=doc.document_id,
                        extractor_version=doc.extractor_version,
                        provider=doc.provider,
                        model=doc.model,
                        result_json=payload,
                        cache_key=cache_key,
                        reextraction_counts={},
                    )
                )
            else:
                row.result_json = payload
                row.cache_key = cache_key
                row.updated_at = utcnow_iso()
            if write_cache:
                c = s.get(ExtractionCacheRow, cache_key)
                if c is None:
                    s.add(ExtractionCacheRow(cache_key=cache_key, result_json=payload))
                else:
                    c.result_json = payload
                    c.updated_at = utcnow_iso()

    def load_extraction(self, did: str) -> ExtractedDocument | None:
        with self.db.session() as s:
            row = s.scalar(select(ExtractionRow).where(ExtractionRow.document_id == did))
            return ExtractedDocument.model_validate_json(row.result_json) if row else None

    def extraction_cache_key(self, did: str) -> str | None:
        with self.db.session() as s:
            row = s.scalar(select(ExtractionRow).where(ExtractionRow.document_id == did))
            return row.cache_key if row else None

    def extractions(self, wid: str) -> list[ExtractedDocument]:
        with self.db.session() as s:
            rows = s.execute(
                select(ExtractionRow, DocumentRow)
                .join(DocumentRow, DocumentRow.id == ExtractionRow.document_id)
                .where(DocumentRow.workflow_id == wid)
                .order_by(DocumentRow.created_at, DocumentRow.id)
            ).all()
            return [ExtractedDocument.model_validate_json(e.result_json) for e, _ in rows]

    def cache_get(self, key: str) -> ExtractedDocument | None:
        with self.db.session() as s:
            row = s.get(ExtractionCacheRow, key)
            return ExtractedDocument.model_validate_json(row.result_json) if row else None

    def reextraction_count(self, did: str, field: str) -> int:
        with self.db.session() as s:
            row = s.scalar(select(ExtractionRow).where(ExtractionRow.document_id == did))
            return int((row.reextraction_counts or {}).get(field, 0)) if row else 0

    def bump_reextraction(self, did: str, field: str) -> int:
        with self.db.session() as s:
            row = s.scalar(select(ExtractionRow).where(ExtractionRow.document_id == did))
            if row is None:
                return 0
            counts = dict(row.reextraction_counts or {})
            counts[field] = int(counts.get(field, 0)) + 1
            row.reextraction_counts = counts
            return counts[field]

    def semantic_get(self, key: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            row = s.get(SemanticCacheRow, key)
            return json.loads(row.result_json) if row else None

    def semantic_put(self, key: str, value: dict[str, Any]) -> None:
        with self.db.session() as s:
            if s.get(SemanticCacheRow, key) is None:
                s.add(SemanticCacheRow(cache_key=key, result_json=json.dumps(value)))

    # ---- rule sets (immutable) ----------------------------------------
    def save_rule_set(self, *, id_: str, name: str, version: str, content: dict[str, Any], derived_from: str | None) -> bool:
        with self.db.session() as s:
            if s.get(RuleSetRow, id_) is not None:
                return False
            s.add(RuleSetRow(id=id_, name=name, version=version, content_json=json.dumps(content), derived_from=derived_from))
            return True

    def get_rule_set(self, id_: str) -> RuleSetRow | None:
        with self.db.session() as s:
            return s.get(RuleSetRow, id_)

    # ---- findings ------------------------------------------------------
    def save_finding(self, f: Finding) -> None:
        with self.db.session() as s:
            row = s.get(FindingRow, f.finding_id)
            data = f.model_dump_json()
            if row is None:
                row = FindingRow(
                    finding_id=f.finding_id,
                    workflow_id=f.workflow_id,
                    evaluation_number=f.evaluation_number,
                    rule_id=f.rule_id,
                    compliance_status="",
                    audit_status="",
                    data_json=data,
                )
                s.add(row)
            row.compliance_status = f.compliance_status.value
            row.audit_status = f.audit_status.value
            row.requires_human_review = f.requires_human_review
            row.review_decision = f.review_decision.value if f.review_decision else None
            row.superseded = f.superseded
            row.data_json = data

    def get_finding(self, fid: str) -> Finding | None:
        with self.db.session() as s:
            row = s.get(FindingRow, fid)
            return Finding.model_validate_json(row.data_json) if row else None

    def require_finding(self, fid: str) -> Finding:
        f = self.get_finding(fid)
        if f is None:
            raise not_found("Finding", fid)
        return f

    def findings(self, wid: str | None = None, *, status: str | None = None, needs_review: bool | None = None, include_superseded: bool = True) -> list[Finding]:
        with self.db.session() as s:
            q = select(FindingRow).order_by(FindingRow.created_at, FindingRow.finding_id)
            if wid:
                q = q.where(FindingRow.workflow_id == wid)
            if status:
                q = q.where(FindingRow.compliance_status == status)
            if not include_superseded:
                q = q.where(FindingRow.superseded.is_(False))
            out = [Finding.model_validate_json(r.data_json) for r in s.scalars(q)]
        if needs_review is not None:
            out = [f for f in out if (f.requires_human_review and f.review_decision is None) == needs_review]
        return out

    # ---- reviews / corrective / outbox --------------------------------
    def add_review(self, *, finding_id: str, workflow_id: str, decision: str, edited_text: str | None, comment: str | None, reviewer: str) -> str:
        with self.db.session() as s:
            r = ReviewRow(finding_id=finding_id, workflow_id=workflow_id, decision=decision, edited_text=edited_text, comment=comment, reviewer=reviewer)
            s.add(r)
            s.flush()
            return r.created_at

    def reviews(self, wid: str) -> list[dict[str, Any]]:
        with self.db.session() as s:
            return [
                {
                    "id": r.id,
                    "finding_id": r.finding_id,
                    "decision": r.decision,
                    "edited_text": r.edited_text,
                    "comment": r.comment,
                    "reviewer": r.reviewer,
                    "created_at": r.created_at,
                }
                for r in s.scalars(select(ReviewRow).where(ReviewRow.workflow_id == wid).order_by(ReviewRow.id))
            ]

    def latest_review(self, finding_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            r = s.scalar(select(ReviewRow).where(ReviewRow.finding_id == finding_id).order_by(ReviewRow.id.desc()))
            return None if r is None else {"decision": r.decision, "edited_text": r.edited_text, "comment": r.comment, "reviewer": r.reviewer, "created_at": r.created_at}

    def add_corrective(self, *, finding_id: str, workflow_id: str, en: str, roman_ur: str, label: str, provider: str, model: str) -> None:
        with self.db.session() as s:
            s.add(CorrectiveActionRow(finding_id=finding_id, workflow_id=workflow_id, en=en, roman_ur=roman_ur, label=label, provider=provider, model=model))

    def corrective_actions(self, wid: str) -> list[dict[str, Any]]:
        with self.db.session() as s:
            return [
                {
                    "id": r.id,
                    "finding_id": r.finding_id,
                    "en": r.en,
                    "roman_ur": r.roman_ur,
                    "status": r.status,
                    "label": r.label,
                    "provider": r.provider,
                    "model": r.model,
                    "created_at": r.created_at,
                }
                for r in s.scalars(select(CorrectiveActionRow).where(CorrectiveActionRow.workflow_id == wid).order_by(CorrectiveActionRow.id))
            ]

    def add_outbox(self, *, finding_id: str, workflow_id: str, channel: str, language: str, text: str) -> int:
        with self.db.session() as s:
            r = OutboxRow(finding_id=finding_id, workflow_id=workflow_id, channel=channel, language=language, text=text)
            s.add(r)
            s.flush()
            return r.id

    def outbox(self, wid: str) -> list[dict[str, Any]]:
        with self.db.session() as s:
            return [
                {"id": r.id, "finding_id": r.finding_id, "channel": r.channel, "language": r.language, "text": r.text, "created_at": r.created_at}
                for r in s.scalars(select(OutboxRow).where(OutboxRow.workflow_id == wid).order_by(OutboxRow.id))
            ]


def status_counts(findings: list[Finding]) -> dict[str, int]:
    live = [f for f in findings if not f.superseded]
    return {
        "passing": sum(f.compliance_status == ComplianceStatus.PASSING_CONFIGURED_CHECK for f in live),
        "not_satisfied": sum(f.compliance_status == ComplianceStatus.REQUIREMENT_NOT_SATISFIED for f in live),
        "human_review": sum(f.compliance_status == ComplianceStatus.HUMAN_REVIEW_REQUIRED for f in live),
    }


__all__ = ["Repo", "AppError", "IllegalTransition", "validate_transition", "new_id", "doc_info", "status_counts", "RUNNING_STATES"]
