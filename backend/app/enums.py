"""All locked enums. Do not rename members after Phase 1: the frontend generates
identical TypeScript unions from the OpenAPI export."""

from __future__ import annotations

from enum import StrEnum


class ComplianceStatus(StrEnum):
    PASSING_CONFIGURED_CHECK = "PASSING_CONFIGURED_CHECK"
    REQUIREMENT_NOT_SATISFIED = "REQUIREMENT_NOT_SATISFIED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


class AuditStatus(StrEnum):
    pending = "pending"
    verified = "verified"
    uncertain = "uncertain"


class ReviewDecision(StrEnum):
    approved = "approved"
    edited = "edited"
    rejected = "rejected"
    more_evidence = "more_evidence"


class EscalationReason(StrEnum):
    LOW_EXTRACTION_CONFIDENCE = "LOW_EXTRACTION_CONFIDENCE"
    CONFLICTING_VALUES = "CONFLICTING_VALUES"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    UNSUPPORTED_CONCLUSION = "UNSUPPORTED_CONCLUSION"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    REEXTRACTION_LIMIT = "REEXTRACTION_LIMIT"
    UNVERIFIABLE_SOURCE = "UNVERIFIABLE_SOURCE"
    UNKNOWN_DOCUMENT_TYPE = "UNKNOWN_DOCUMENT_TYPE"


class WorkflowStatus(StrEnum):
    CREATED = "CREATED"
    EXTRACTING = "EXTRACTING"
    EVALUATING = "EVALUATING"
    AUDITING = "AUDITING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    AWAITING_EVIDENCE = "AWAITING_EVIDENCE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AuditorFlag(StrEnum):
    QUOTE_NOT_FOUND = "QUOTE_NOT_FOUND"
    QUOTE_FUZZY_ONLY = "QUOTE_FUZZY_ONLY"
    PAGE_MISSING = "PAGE_MISSING"
    VALUE_QUOTE_MISMATCH = "VALUE_QUOTE_MISMATCH"
    SUPPLIER_ID_CONFLICT = "SUPPLIER_ID_CONFLICT"
    BATCH_ID_CONFLICT = "BATCH_ID_CONFLICT"
    DATE_CONFLICT = "DATE_CONFLICT"
    CERT_EXPIRED = "CERT_EXPIRED"
    CERT_VALIDITY_UNKNOWN = "CERT_VALIDITY_UNKNOWN"
    CONCLUSION_UNSUPPORTED = "CONCLUSION_UNSUPPORTED"
    EXTRACTOR_GUESS_SUSPECTED = "EXTRACTOR_GUESS_SUSPECTED"


class EventType(StrEnum):
    WORKFLOW_CREATED = "WORKFLOW_CREATED"
    DOCUMENT_RECEIVED = "DOCUMENT_RECEIVED"
    EXTRACTION_STARTED = "EXTRACTION_STARTED"
    EXTRACTION_COMPLETED = "EXTRACTION_COMPLETED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    COMPLIANCE_CHECK_STARTED = "COMPLIANCE_CHECK_STARTED"
    FINDING_CREATED = "FINDING_CREATED"
    AUDIT_STARTED = "AUDIT_STARTED"
    AUDIT_CHECK_RESULT = "AUDIT_CHECK_RESULT"
    AUDIT_CONFLICT_FOUND = "AUDIT_CONFLICT_FOUND"
    REEXTRACTION_REQUESTED = "REEXTRACTION_REQUESTED"
    REEXTRACTION_COMPLETED = "REEXTRACTION_COMPLETED"
    AUDIT_COMPLETED = "AUDIT_COMPLETED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    CORRECTIVE_ACTION_DRAFTED = "CORRECTIVE_ACTION_DRAFTED"
    REVIEW_DECISION_RECORDED = "REVIEW_DECISION_RECORDED"
    RULES_RERUN_STARTED = "RULES_RERUN_STARTED"
    RULES_RERUN_COMPLETED = "RULES_RERUN_COMPLETED"
    DISPATCH_RECORDED = "DISPATCH_RECORDED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"


class DocType(StrEnum):
    lab_report = "lab_report"
    certificate = "certificate"
    supplier_declaration = "supplier_declaration"
    unknown = "unknown"


class Verification(StrEnum):
    unverified = "unverified"
    exact = "exact"
    normalized = "normalized"
    fuzzy = "fuzzy"
    not_found = "not_found"
    vision_only = "vision_only"


class AgentName(StrEnum):
    orchestrator = "orchestrator"
    extractor = "extractor"
    compliance = "compliance"
    auditor = "auditor"
    corrective = "corrective"
    reviewer = "reviewer"
    system = "system"
