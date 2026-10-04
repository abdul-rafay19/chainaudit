"""Deterministic compliance rules engine.

PURE: no I/O, no LLM, no clock. The only date source is `ruleset.reference_date`.
Every `reason` string is built by code from a template. This module is the ONLY
place compliance statuses are decided (non-negotiable N1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.core.textnorm import normalize_id, parse_iso_date, to_decimal
from app.enums import ComplianceStatus as CS
from app.enums import DocType, Verification
from app.enums import EscalationReason as ER
from app.rules.models import (
    ConsistencyRule,
    DateWindowRule,
    NumericMaxRule,
    RequiredCertificationRule,
    Rule,
    RuleSet,
)
from app.schemas.domain import EvidenceField, ExtractedDocument

PROMPT_INJECTION_NOTE = "possible_prompt_injection"


@dataclass(frozen=True)
class IntakeIssue:
    """A document-level problem that must reach a human (failed read, unknown type, scan, injection)."""

    document_id: str
    name: str
    kind: str  # extraction_failed | unknown_type | image_only | prompt_injection
    detail: str = ""


@dataclass
class EvidenceBundle:
    documents: list[ExtractedDocument]
    intake: list[IntakeIssue] = field(default_factory=list)


@dataclass
class RuleResult:
    rule_id: str
    title: str
    status: CS
    reason: str
    escalation_reason: ER | None = None
    evidence: list[EvidenceField] = field(default_factory=list)


Gate = tuple[ER, str]
_TAG_RX = re.compile(r"\s*\(rule [^()]*\)\s*$")


def core_reason(reason: str) -> str:
    """The conclusion without the trailing '(rule X, <framework vN>)' tag, so findings can be compared across rule-set versions."""
    return _TAG_RX.sub("", reason)


def fmt_num(d: Decimal) -> str:
    s = format(d.normalize(), "f")
    return s


def _gate(ev: EvidenceField, doc: ExtractedDocument, rs: RuleSet) -> Gate | None:
    """Evidence we must not auto-decide on."""
    if PROMPT_INJECTION_NOTE in doc.extraction_notes:
        return ER.UNVERIFIABLE_SOURCE, f"source '{ev.source_document}' contains text that looks like instructions to an AI (possible prompt injection)"
    if ev.verification == Verification.vision_only:
        return ER.UNVERIFIABLE_SOURCE, f"value '{ev.field}' in '{ev.source_document}' was read from an image and cannot be verified against a text layer"
    if ev.confidence < rs.min_extraction_confidence:
        return ER.LOW_EXTRACTION_CONFIDENCE, (
            f"extraction confidence {ev.confidence:.2f} for '{ev.field}' in '{ev.source_document}' is below the configured minimum of {rs.min_extraction_confidence:.2f}"
        )
    return None


def _cp(ev: EvidenceField) -> EvidenceField:
    return ev.model_copy(deep=True)


def _absent_marker(field_name: str, doc: ExtractedDocument) -> EvidenceField:
    """Evidence that we searched a document and the value was not there (value=None, never guessed)."""
    return EvidenceField(
        field=field_name,
        value=None,
        source_document=doc.source_document or doc.document_id,
        document_id=doc.document_id,
        page=None,
        quote=None,
        confidence=1.0,
        verification=Verification.not_found,
        verified=False,
    )


def _tag(rule: Rule, rs: RuleSet) -> str:
    return f"rule {rule.id}, {rs.tag}"


def _combine(review: list[tuple[EvidenceField, Gate]], fails: list[str], passes: list[str], ev_all: list[EvidenceField], rule: Rule, rs: RuleSet) -> RuleResult:
    """Precedence: any doubt -> human review; else any failure -> not satisfied; else pass."""
    if review:
        reason_code = review[0][1][0]
        text = "; ".join(g[1] for _, g in review)
        return RuleResult(rule.id, rule.title, CS.HUMAN_REVIEW_REQUIRED, f"Human review required: {text} ({_tag(rule, rs)})", reason_code, ev_all)
    if fails:
        return RuleResult(rule.id, rule.title, CS.REQUIREMENT_NOT_SATISFIED, "; ".join(fails) + f" ({_tag(rule, rs)})", None, ev_all)
    return RuleResult(rule.id, rule.title, CS.PASSING_CONFIGURED_CHECK, "; ".join(passes) + f" ({_tag(rule, rs)})", None, ev_all)


# ------------------------------------------------------------------ rule types
def _numeric_max(rule: NumericMaxRule, rs: RuleSet, docs: list[ExtractedDocument]) -> RuleResult:
    pairs = [(ev, d) for d in docs for ev in d.test_results if ev.field == rule.field]
    limit = Decimal(str(rule.limit))
    if not pairs:
        return RuleResult(
            rule.id,
            rule.title,
            CS.HUMAN_REVIEW_REQUIRED,
            f"Human review required: no '{rule.field}' value was found in the submitted documents ({_tag(rule, rs)})",
            ER.MISSING_REQUIRED_FIELD,
            [_absent_marker(rule.field, d) for d in docs if d.doc_type == DocType.lab_report],
        )
    review: list[tuple[EvidenceField, Gate]] = []
    fails: list[str] = []
    passes: list[str] = []
    for ev, doc in pairs:
        g = _gate(ev, doc, rs)
        if g is None:
            dec = to_decimal(ev.value) if ev.value is not None else None
            if dec is None:
                g = (ER.MISSING_REQUIRED_FIELD if ev.value is None else ER.LOW_EXTRACTION_CONFIDENCE, f"'{rule.field}' in '{ev.source_document}' has no usable numeric value")
            elif not ev.unit:
                g = (ER.MISSING_REQUIRED_FIELD, f"unit missing for {fmt_num(dec)} in '{ev.source_document}'; expected {rule.unit}")
            elif ev.unit.strip().lower() != rule.unit.strip().lower():
                g = (ER.CONFLICTING_VALUES, f"unit mismatch: '{ev.unit}' reported in '{ev.source_document}' but the rule is configured in {rule.unit}")
        if g is not None:
            review.append((ev, g))
            continue
        assert dec is not None
        if dec <= limit:
            passes.append(f"{fmt_num(dec)} {rule.unit} is within configured maximum of {fmt_num(limit)} {rule.unit}")
        else:
            fails.append(f"{fmt_num(dec)} {rule.unit} exceeds configured maximum of {fmt_num(limit)} {rule.unit}")
    return _combine(review, fails, passes, [_cp(e) for e, _ in pairs], rule, rs)


def _required_cert(rule: RequiredCertificationRule, rs: RuleSet, docs: list[ExtractedDocument]) -> RuleResult:
    ref = rs.reference_date
    want = normalize_id(rule.value)
    found = [(c, d) for d in docs for c in d.certifications if c.name.value is not None and normalize_id(str(c.name.value)) == want]
    if not found:
        return RuleResult(
            rule.id,
            rule.title,
            CS.REQUIREMENT_NOT_SATISFIED,
            f"Required certification {rule.value} was not found in the submitted documents ({_tag(rule, rs)})",
            None,
            [_absent_marker(f"certification:{rule.value}", d) for d in docs],
        )
    review: list[tuple[EvidenceField, Gate]] = []
    ok: list[str] = []
    expired: list[str] = []
    evs: list[EvidenceField] = []
    for cert, doc in found:
        evs.append(_cp(cert.name))
        if cert.valid_until:
            evs.append(_cp(cert.valid_until))
        g = _gate(cert.name, doc, rs)
        vu = cert.valid_until
        if g is None and (vu is None or vu.value is None):
            g = (ER.MISSING_REQUIRED_FIELD, f"validity end date missing for {rule.value} in '{cert.name.source_document}'")
        if g is None:
            assert vu is not None
            g = _gate(vu, doc, rs)
        if g is None:
            assert vu is not None
            vd = parse_iso_date(vu.value)
            if vd is None:
                g = (ER.LOW_EXTRACTION_CONFIDENCE, f"validity date '{vu.value}' in '{vu.source_document}' is not a valid ISO date")
            elif vd >= ref:
                ok.append(f"{rule.value} is valid until {vd.isoformat()}, on or after the reference date {ref.isoformat()}")
            else:
                expired.append(f"{rule.value} expired on {vd.isoformat()}, before the reference date {ref.isoformat()}")
        if g is not None:
            review.append((cert.name, g))
    if ok:  # at least one fully valid certificate satisfies the requirement
        return RuleResult(rule.id, rule.title, CS.PASSING_CONFIGURED_CHECK, "; ".join(ok) + f" ({_tag(rule, rs)})", None, evs)
    return _combine(review, expired, [], evs, rule, rs)


def _date_window(rule: DateWindowRule, rs: RuleSet, docs: list[ExtractedDocument]) -> RuleResult:
    ref: date = rs.reference_date
    targets = [d for d in docs if d.doc_type in rule.document_types]
    types = "/".join(t.value for t in rule.document_types)
    if not targets:
        return RuleResult(
            rule.id, rule.title, CS.HUMAN_REVIEW_REQUIRED, f"Human review required: no {types} document was found to check ({_tag(rule, rs)})", ER.MISSING_REQUIRED_FIELD, []
        )
    review: list[tuple[EvidenceField, Gate]] = []
    fails: list[str] = []
    passes: list[str] = []
    evs: list[EvidenceField] = []
    for d in targets:
        ev = d.document_date
        if ev is None or ev.value is None:
            marker = _absent_marker("document_date", d)
            evs.append(marker)
            review.append((marker, (ER.MISSING_REQUIRED_FIELD, f"no document date found in '{marker.source_document}'")))
            continue
        evs.append(_cp(ev))
        g = _gate(ev, d, rs)
        if g is None:
            dt = parse_iso_date(ev.value)
            if dt is None:
                g = (ER.LOW_EXTRACTION_CONFIDENCE, f"date '{ev.value}' in '{ev.source_document}' could not be parsed as an ISO date")
            else:
                age = (ref - dt).days
                if age < 0:
                    g = (ER.CONFLICTING_VALUES, f"'{ev.source_document}' is dated {dt.isoformat()}, which is after the reference date {ref.isoformat()}")
                elif age > rule.max_age_days:
                    fails.append(f"{d.source_document or 'report'} is {age} days old, over the configured maximum of {rule.max_age_days} days")
                else:
                    passes.append(f"{d.source_document or 'report'} is {age} days old, within the configured maximum of {rule.max_age_days} days")
        if g is not None:
            review.append((ev, g))
    return _combine(review, fails, passes, evs, rule, rs)


def _consistency(rule: ConsistencyRule, rs: RuleSet, docs: list[ExtractedDocument]) -> RuleResult:
    review: list[tuple[EvidenceField, Gate]] = []
    conflict_msgs: list[str] = []
    conflict_ev: list[EvidenceField] = []
    all_ev: list[EvidenceField] = []
    ok_msgs: list[str] = []
    for fname in rule.fields:
        items = [(getattr(d, fname), d) for d in docs if getattr(d, fname) is not None and getattr(d, fname).value is not None]
        if not items:
            review.append(
                (
                    _absent_marker(fname, docs[0]) if docs else _absent_marker(fname, ExtractedDocument(document_id="none", doc_type=DocType.unknown)),
                    (ER.MISSING_REQUIRED_FIELD, f"no {fname} found in any document"),
                )
            )
            continue
        evs = [_cp(ev) for ev, _ in items]
        all_ev.extend(evs)
        gated = False
        for ev, d in items:
            g = _gate(ev, d, rs)
            if g is not None:
                review.append((ev, g))
                gated = True
        if gated:
            continue
        distinct = {normalize_id(str(ev.value)) for ev, _ in items}
        if len(distinct) > 1:
            parts = ", ".join(f"{ev.source_document}: {ev.value}" for ev, _ in items)
            conflict_msgs.append(f"{fname} differs across documents ({parts})")
            conflict_ev.extend(evs)
        else:
            ok_msgs.append(f"{fname} {items[0][0].value} is consistent across {len(items)} document(s)")
    if conflict_msgs:
        extra = f"; {'; '.join(g[1] for _, g in review)}" if review else ""
        return RuleResult(
            rule.id,
            rule.title,
            CS.HUMAN_REVIEW_REQUIRED,
            f"Potential inconsistency requiring human review: {'; '.join(conflict_msgs)}{extra} ({_tag(rule, rs)})",
            ER.CONFLICTING_VALUES,
            conflict_ev,
        )
    return _combine(review, [], ok_msgs, all_ev, rule, rs)


def _intake(issue: IntakeIssue, rs: RuleSet) -> RuleResult:
    mapping = {
        "extraction_failed": (ER.EXTRACTION_FAILED, f"'{issue.name}' could not be processed: {issue.detail or 'extraction failed'}"),
        "unknown_type": (ER.UNKNOWN_DOCUMENT_TYPE, f"'{issue.name}' could not be classified as a lab report, certificate or supplier declaration"),
        "image_only": (ER.UNVERIFIABLE_SOURCE, f"'{issue.name}' has image-only pages ({issue.detail}); values cannot be verified against a text layer"),
        "prompt_injection": (
            ER.UNVERIFIABLE_SOURCE,
            f"'{issue.name}' contains text that looks like instructions to an AI (possible prompt injection); its values were not trusted",
        ),
    }
    code, text = mapping[issue.kind]
    return RuleResult(f"DOC_INTAKE:{issue.document_id}", f"Document intake: {issue.name}", CS.HUMAN_REVIEW_REQUIRED, f"Human review required: {text} ({rs.tag})", code, [])


def evaluate(ruleset: RuleSet, bundle: EvidenceBundle) -> list[RuleResult]:
    out: list[RuleResult] = []
    docs = bundle.documents
    for rule in ruleset.rules:
        if isinstance(rule, NumericMaxRule):
            out.append(_numeric_max(rule, ruleset, docs))
        elif isinstance(rule, RequiredCertificationRule):
            out.append(_required_cert(rule, ruleset, docs))
        elif isinstance(rule, DateWindowRule):
            out.append(_date_window(rule, ruleset, docs))
        elif isinstance(rule, ConsistencyRule):
            out.append(_consistency(rule, ruleset, docs))
    out.extend(_intake(i, ruleset) for i in bundle.intake)
    return out
