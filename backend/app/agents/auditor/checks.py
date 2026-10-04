"""Deterministic Auditor checks (code, no LLM). `verify_evidence` is pure given the page texts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.textnorm import match_quote, value_in_quote
from app.enums import AuditorFlag as F
from app.enums import Verification
from app.schemas.domain import EvidenceField

CAPS = {Verification.not_found: 0.40, Verification.fuzzy: 0.70, Verification.vision_only: 0.75}
SUSPECT_CAP = 0.40
IDENTITY_FIELDS = {"supplier_id", "batch_id"}
# A quote that describes ANOTHER document ("Previous report reference: ...") is a classic extraction trap.
DECOY_RX = re.compile(r"\b(previous|prior|earlier|superseded|supersedes|replaces|replaced|reference|ref\.?|amended|obsolete|old)\b", re.I)
BLOCKING_FLAGS = {F.QUOTE_NOT_FOUND, F.QUOTE_FUZZY_ONLY, F.PAGE_MISSING, F.VALUE_QUOTE_MISMATCH, F.EXTRACTOR_GUESS_SUSPECTED, F.CONCLUSION_UNSUPPORTED}


@dataclass
class FieldCheck:
    name: str
    passed: bool
    detail: str


@dataclass
class FieldVerification:
    checks: list[FieldCheck] = field(default_factory=list)
    flags: list[F] = field(default_factory=list)
    needs_reextract: bool = False
    reread_whole_document: bool = False  # True when the quoted page itself is wrong/untrustworthy

    @property
    def ok(self) -> bool:
        return not self.flags


def apply_caps(ev: EvidenceField, suspected: bool = False) -> None:
    """confidence = min(llm_confidence, cap(verification)). Idempotent; never a calibrated probability."""
    base = ev.llm_confidence if ev.llm_confidence is not None else ev.confidence
    cap = CAPS.get(ev.verification, 1.0)
    if suspected:
        cap = min(cap, SUSPECT_CAP)
    ev.confidence = round(min(base, 1.0, cap), 4)


def verify_evidence(ev: EvidenceField, page_texts: list[str] | None) -> FieldVerification:
    out = FieldVerification()
    if ev.verification == Verification.vision_only:
        out.checks.append(FieldCheck("quote_in_source", False, "image-only page: the quote cannot be checked against a text layer"))
        ev.verified = False
        apply_caps(ev)
        return out

    page_ok = bool(ev.document_id) and isinstance(ev.page, int) and page_texts is not None and 1 <= ev.page <= len(page_texts)
    out.checks.append(FieldCheck("source_presence", page_ok, f"document {ev.source_document}, page {ev.page}" if page_ok else "page reference is missing or outside the document"))
    if not page_ok:
        out.flags.append(F.PAGE_MISSING)
        out.needs_reextract, out.reread_whole_document = True, True
        ev.verification, ev.verified, ev.bbox = Verification.not_found, False, None
        apply_caps(ev)
        return out

    assert page_texts is not None and ev.page is not None
    m = match_quote(ev.quote, page_texts[ev.page - 1])
    if m.kind == Verification.not_found:
        out.flags.append(F.QUOTE_NOT_FOUND)
        out.checks.append(FieldCheck("quote_in_source", False, f"quote not found on page {ev.page}"))
        out.needs_reextract = True
    elif m.kind == Verification.fuzzy:
        out.flags.append(F.QUOTE_FUZZY_ONLY)
        out.checks.append(FieldCheck("quote_in_source", False, f"only a fuzzy match ({m.score:.0f}%) on page {ev.page}; not verbatim"))
        out.needs_reextract = True
    else:
        out.checks.append(FieldCheck("quote_in_source", True, f"{m.kind.value} match on page {ev.page}"))
    ev.verification = m.kind

    if m.kind != Verification.not_found:
        vq = value_in_quote(ev.field, ev.value, ev.quote)
        out.checks.append(FieldCheck("value_in_quote", vq, "extracted value appears in the quote" if vq else f"value {ev.value!r} does not appear in the quote"))
        if not vq:
            out.flags.append(F.VALUE_QUOTE_MISMATCH)
            ev.verification = Verification.not_found
            out.needs_reextract = True

    suspected = False
    base = ev.field.split(":")[0]
    if base in IDENTITY_FIELDS and ev.quote and ev.verification in (Verification.exact, Verification.normalized) and DECOY_RX.search(ev.quote):
        suspected = True
        out.flags.append(F.EXTRACTOR_GUESS_SUSPECTED)
        out.checks.append(FieldCheck("not_a_reference_to_another_document", False, "the quote describes a previous/reference document, not this document's own value"))
        out.needs_reextract, out.reread_whole_document = True, True

    ev.verified = ev.verification in (Verification.exact, Verification.normalized) and not out.flags
    if not ev.verified:
        ev.bbox = None
    apply_caps(ev, suspected)
    return out
