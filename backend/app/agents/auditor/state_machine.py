"""The ONLY surface the Auditor has on a finding (non-negotiable N2).

There is intentionally no method that writes `compliance_status`. Compliance verdicts are
assigned by the deterministic rules engine (via the compliance agent) and nowhere else.
Evidence corrections go through the extractor followed by a deterministic re-evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.auditor.checks import BLOCKING_FLAGS
from app.enums import AuditorFlag, AuditStatus, ComplianceStatus, EscalationReason
from app.schemas.domain import Finding


@dataclass(frozen=True)
class ReextractionRequest:
    document_id: str
    field: str


class FindingAuditor:
    __slots__ = ("_f", "_requests")
    _f: Finding
    _requests: list[ReextractionRequest]
    _ALLOWED_ATTRS = {"_f", "_requests"}

    def __init__(self, finding: Finding) -> None:
        object.__setattr__(self, "_f", finding)
        object.__setattr__(self, "_requests", [])
        self._f.auditor_flags = []
        self._f.audit_status = AuditStatus.pending
        self._f.audit_notes = []

    def __setattr__(self, name: str, value: object) -> None:
        if name not in self._ALLOWED_ATTRS:
            raise AttributeError(f"FindingAuditor has no writable attribute '{name}'; the Auditor cannot change compliance status")
        object.__setattr__(self, name, value)

    # ---- read-only views ------------------------------------------------
    @property
    def finding_id(self) -> str:
        return self._f.finding_id

    @property
    def rule_id(self) -> str:
        return self._f.rule_id

    @property
    def flags(self) -> list[AuditorFlag]:
        return list(self._f.auditor_flags)

    @property
    def requested_reextractions(self) -> list[ReextractionRequest]:
        return list(self._requests)

    # ---- the four permitted actions --------------------------------------
    def add_flag(self, flag: AuditorFlag, note: str | None = None) -> None:
        if flag not in self._f.auditor_flags:
            self._f.auditor_flags.append(flag)
        if note and note not in self._f.audit_notes:
            self._f.audit_notes.append(note)

    def mark_verified(self) -> None:
        if self._f.audit_status != AuditStatus.uncertain:  # uncertainty is sticky
            self._f.audit_status = AuditStatus.verified

    def mark_uncertain(self, reason: EscalationReason) -> None:
        self._f.audit_status = AuditStatus.uncertain
        specific = {EscalationReason.REEXTRACTION_LIMIT, EscalationReason.UNVERIFIABLE_SOURCE}
        if self._f.escalation_reason is None or reason in specific:
            self._f.escalation_reason = reason

    def request_reextraction(self, document_id: str, field: str) -> None:
        self._requests.append(ReextractionRequest(document_id, field))

    # ---- derived outcome (reads compliance status, never writes it) -------
    def finalize(self) -> None:
        """Outcome mapping: PASSING+verified -> no review; everything else -> review."""
        if self._f.audit_status == AuditStatus.pending:
            self.mark_verified()
        needs = not (self._f.compliance_status == ComplianceStatus.PASSING_CONFIGURED_CHECK and self._f.audit_status == AuditStatus.verified)
        self._f.requires_human_review = needs

    def has_blocking_flag(self) -> bool:
        return any(f in BLOCKING_FLAGS for f in self._f.auditor_flags)
