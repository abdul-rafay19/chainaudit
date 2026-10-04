"""Pydantic domain + API schemas. Source of truth for the OpenAPI contract."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.enums import (
    AgentName,
    AuditorFlag,
    AuditStatus,
    ComplianceStatus,
    DocType,
    EscalationReason,
    EventType,
    ReviewDecision,
    Verification,
    WorkflowStatus,
)

CORRECTIVE_LABEL = "AI draft. Review before sending."


class EvidenceField(BaseModel):
    field: str
    value: str | float | None = None
    unit: str | None = None
    source_document: str
    document_id: str
    page: int | None = None
    quote: str | None = None
    confidence: float = 0.0
    llm_confidence: float | None = None  # raw, pre-cap value reported by the extractor
    verification: Verification = Verification.unverified
    verified: bool = False  # True only for exact|normalized
    bbox: list[float] | None = None


class Certification(BaseModel):
    name: EvidenceField
    issued_date: EvidenceField | None = None
    valid_until: EvidenceField | None = None


class ExtractedDocument(BaseModel):
    document_id: str
    doc_type: DocType
    supplier_id: EvidenceField | None = None
    batch_id: EvidenceField | None = None
    document_date: EvidenceField | None = None
    test_results: list[EvidenceField] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    other: list[EvidenceField] = Field(default_factory=list)
    extraction_notes: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    extractor_version: str = ""
    source_document: str = ""
    page_count: int | None = None
    image_only_pages: list[int] = Field(default_factory=list)

    def all_fields(self) -> list[EvidenceField]:
        out: list[EvidenceField] = []
        for f in (self.supplier_id, self.batch_id, self.document_date):
            if f is not None:
                out.append(f)
        out.extend(self.test_results)
        for c in self.certifications:
            out.append(c.name)
            if c.issued_date:
                out.append(c.issued_date)
            if c.valid_until:
                out.append(c.valid_until)
        out.extend(self.other)
        return out


class CorrectiveAction(BaseModel):
    en: str
    roman_ur: str
    status: Literal["draft"] = "draft"
    label: str = CORRECTIVE_LABEL


class Finding(BaseModel):
    finding_id: str
    workflow_id: str
    evaluation_number: int
    rule_id: str
    rule_title: str
    compliance_status: ComplianceStatus
    reason: str
    escalation_reason: EscalationReason | None = None
    evidence: list[EvidenceField] = Field(default_factory=list)
    auditor_flags: list[AuditorFlag] = Field(default_factory=list)
    audit_status: AuditStatus = AuditStatus.pending
    requires_human_review: bool = False
    review_decision: ReviewDecision | None = None
    corrective_action: CorrectiveAction | None = None
    superseded: bool = False
    audit_notes: list[str] = Field(default_factory=list)
    rule_set_label: str = ""


class StoredEvent(BaseModel):
    id: int
    workflow_id: str
    seq: int
    type: EventType
    agent: AgentName | str
    timestamp: str
    finding_id: str | None = None
    document_id: str | None = None
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    replay: bool = False


class DocumentInfo(BaseModel):
    document_id: str
    original_name: str
    sha256: str
    mime: str
    size: int
    doc_type: DocType
    page_count: int | None = None
    status: str
    error: str | None = None


class RuleSetInfo(BaseModel):
    id: str
    name: str
    version: str
    label: str
    derived_from: str | None = None
    content: dict[str, Any]


class WorkflowSummary(BaseModel):
    workflow_id: str
    status: WorkflowStatus
    created_at: str
    updated_at: str
    provider: str
    model: str
    replay: bool = False
    document_count: int = 0
    finding_count: int = 0
    needs_review_count: int = 0
    rule_set_id: str = ""


class WorkflowOutcome(BaseModel):
    passing: int = 0
    not_satisfied: int = 0
    human_review: int = 0
    needs_review: int = 0
    reviewed: int = 0
    summary: str = ""


class WorkflowDetail(BaseModel):
    workflow_id: str
    status: WorkflowStatus
    created_at: str
    updated_at: str
    provider: str
    model: str
    replay: bool = False
    evaluation_number: int
    failure_reason: str | None = None
    labels: list[str] = Field(default_factory=list)
    documents: list[DocumentInfo]
    extractions: list[ExtractedDocument]
    findings: list[Finding]
    rule_set: RuleSetInfo
    outcome: WorkflowOutcome


class UploadResponse(BaseModel):
    workflow_id: str
    document_ids: list[str] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    decision: ReviewDecision
    edited_text: str | None = None
    comment: str | None = None
    reviewer: str = Field(min_length=1, max_length=120)


class ReviewResponse(BaseModel):
    finding_id: str
    decision: ReviewDecision
    workflow_status: WorkflowStatus
    recorded_at: str


class DispatchRequest(BaseModel):
    channel: Literal["demo_whatsapp", "demo_email"] = "demo_email"
    language: Literal["en", "roman_ur"] = "en"


class DispatchResponse(BaseModel):
    finding_id: str
    channel: str
    language: str
    text: str
    outbox_id: int
    note: str = "Recorded in demo outbox only. Nothing was actually sent."


class RerunRequest(BaseModel):
    rule_set_id: str | None = None
    overrides: dict[str, dict[str, Any]] | None = None


class RerunResponse(BaseModel):
    workflow_id: str
    rule_set_id: str
    evaluation_number: int
    changed: list[dict[str, Any]]
    llm_calls: int = 0
    note: str = "Re-evaluated cached evidence; no extraction was re-run."


class FindingDetail(BaseModel):
    finding: Finding
    events: list[StoredEvent]
    audit_checks: list[StoredEvent]


class ReplayInfo(BaseModel):
    name: str
    title: str
    description: str
    event_count: int


class ReplayStartResponse(BaseModel):
    workflow_id: str
    name: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    app_env: str
    provider: str
    model: str
    mock: bool
    fault_injection: list[str] = Field(default_factory=list)
    rule_set: str
    version: str = "1.0.0"


class AuditRecord(BaseModel):
    workflow_id: str
    status: WorkflowStatus
    labels: list[str]
    supplier_ids: list[str]
    batch_ids: list[str]
    documents: list[DocumentInfo]
    extractions: list[ExtractedDocument]
    rule_set: RuleSetInfo
    findings: list[Finding]
    reviews: list[dict[str, Any]]
    corrective_actions: list[dict[str, Any]]
    outbox: list[dict[str, Any]]
    events: list[StoredEvent]
