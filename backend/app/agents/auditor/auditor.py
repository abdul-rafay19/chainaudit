"""Agent 3: Evidence Auditor. Challenges findings, never changes a compliance status."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.agents.auditor.checks import BLOCKING_FLAGS, verify_evidence
from app.agents.auditor.semantic import semantic_check
from app.agents.auditor.state_machine import FindingAuditor
from app.agents.extraction.extractor import ExtractionAgent
from app.config import Settings
from app.core import pdf as pdfmod
from app.core.events import EventBus
from app.core.logging import get_logger
from app.core.store import Repo
from app.core.textnorm import normalize, normalize_id, parse_iso_date
from app.enums import AgentName, EventType, Verification
from app.enums import AuditorFlag as F
from app.enums import EscalationReason as ER
from app.llm.base import LLMError, LLMProvider
from app.schemas.domain import EvidenceField, ExtractedDocument, Finding
from app.schemas.refs import get_ref, iter_refs, set_ref

log = get_logger("auditor")
Key = tuple[str, str, str]


def ekey(e: EvidenceField) -> Key:
    return (e.document_id, e.field, str(e.value))


@dataclass
class FieldOutcome:
    flags: list[F] = field(default_factory=list)
    limit_hit: bool = False
    vision_only: bool = False


@dataclass
class AuditContext:
    wid: str
    reference_date: date
    reevaluate: Callable[[list[ExtractedDocument], str], Awaitable[list[dict[str, Any]]]]
    allow_reextraction: bool = True
    semantic_cache_only: bool = False
    replay: bool = False


@dataclass
class AuditSummary:
    verified: int = 0
    uncertain: int = 0
    needs_review: int = 0
    reextractions: int = 0
    conflicts: int = 0


class AuditorAgent:
    def __init__(self, repo: Repo, bus: EventBus, provider: LLMProvider, extractor: ExtractionAgent, settings: Settings) -> None:
        self.repo, self.bus, self.provider, self.extractor, self.settings = repo, bus, provider, extractor, settings

    async def _emit(
        self, ctx: AuditContext, type_: EventType, message: str, *, finding_id: str | None = None, document_id: str | None = None, data: dict[str, Any] | None = None
    ) -> None:
        await self.bus.emit(ctx.wid, type_, agent=AgentName.auditor, message=message, finding_id=finding_id, document_id=document_id, data=data or {}, replay=ctx.replay)

    # ------------------------------------------------------------------ main
    async def run(self, ctx: AuditContext, findings: list[Finding], docs: list[ExtractedDocument]) -> AuditSummary:
        summary = AuditSummary()
        await self._emit(
            ctx,
            EventType.AUDIT_STARTED,
            f"Auditor challenging {len(findings)} finding(s) across {len(docs)} document(s)",
            data={"findings": len(findings), "documents": len(docs), "reextraction_allowed": ctx.allow_reextraction},
        )
        texts: dict[str, list[str] | None] = {}
        paths: dict[str, Path] = {}
        for d in docs:
            row = await asyncio.to_thread(self.repo.get_document, d.document_id)
            if row is None:
                texts[d.document_id] = None
                continue
            paths[d.document_id] = Path(row.stored_path)
            try:
                texts[d.document_id] = (await asyncio.to_thread(pdfmod.analyze, paths[d.document_id])).texts
            except pdfmod.PdfError:
                texts[d.document_id] = None

        # 1. deterministic evidence verification + Case A loop
        outcomes: dict[Key, FieldOutcome] = {}
        for d in docs:
            for ref, _ in iter_refs(d):
                await self._audit_field(ctx, d, ref, texts[d.document_id], paths.get(d.document_id), outcomes, summary)
            key = await asyncio.to_thread(self.repo.extraction_cache_key, d.document_id)
            if key:
                await asyncio.to_thread(self.repo.save_extraction, d, key, write_cache=False)

        # 2. engine re-evaluates with audited evidence (the evidence changed, not the verdict)
        await ctx.reevaluate(docs, "evidence verified by auditor")

        auds = {f.finding_id: FindingAuditor(f) for f in findings}
        by_fid = {f.finding_id: f for f in findings}
        for fid, aud in auds.items():
            for e in by_fid[fid].evidence:
                o = outcomes.get(ekey(e))
                if o is None:
                    continue
                for fl in o.flags:
                    aud.add_flag(fl)
                if o.limit_hit:
                    aud.mark_uncertain(ER.REEXTRACTION_LIMIT)
                elif o.vision_only:
                    aud.mark_uncertain(ER.UNVERIFIABLE_SOURCE)
                elif any(fl in BLOCKING_FLAGS for fl in o.flags):
                    aud.mark_uncertain(ER.LOW_EXTRACTION_CONFIDENCE)

        # 3-5. cross-document identity/date checks, certificate validity, absence checks
        await self._cross_document(ctx, docs, by_fid, auds, summary)
        await self._certificates(ctx, docs, by_fid, auds)
        await self._absence(ctx, texts, by_fid, auds)

        # 6. semantic check, only for findings that survived the deterministic checks
        await self._semantic(ctx, by_fid, auds)

        # 7. outcome mapping + review escalation
        for f in findings:
            aud = auds[f.finding_id]
            aud.finalize()
            summary.verified += f.audit_status.value == "verified"
            summary.uncertain += f.audit_status.value == "uncertain"
            if f.requires_human_review:
                summary.needs_review += 1
                await self._emit(
                    ctx,
                    EventType.HUMAN_REVIEW_REQUIRED,
                    f"{f.rule_id} needs human review" + (f" ({f.escalation_reason.value})" if f.escalation_reason else ""),
                    finding_id=f.finding_id,
                    data={
                        "rule_id": f.rule_id,
                        "compliance_status": f.compliance_status.value,
                        "audit_status": f.audit_status.value,
                        "escalation_reason": f.escalation_reason.value if f.escalation_reason else None,
                        "reason": f.reason,
                    },
                )
            await asyncio.to_thread(self.repo.save_finding, f)
        await self._emit(
            ctx,
            EventType.AUDIT_COMPLETED,
            f"Audit complete: {summary.verified} verified, {summary.uncertain} uncertain, {summary.needs_review} need human review",
            data={
                "verified": summary.verified,
                "uncertain": summary.uncertain,
                "needs_review": summary.needs_review,
                "reextractions": summary.reextractions,
                "conflicts": summary.conflicts,
            },
        )
        return summary

    # --------------------------------------------------------- field loop (A)
    async def _audit_field(
        self, ctx: AuditContext, doc: ExtractedDocument, ref: str, texts: list[str] | None, path: Path | None, outcomes: dict[Key, FieldOutcome], summary: AuditSummary
    ) -> None:
        did = doc.document_id
        limit = self.settings.max_reextractions_per_field
        attempt = 0
        while True:
            ev = get_ref(doc, ref)
            if ev is None:
                return
            res = verify_evidence(ev, texts)
            if ev.verification in (Verification.exact, Verification.normalized, Verification.fuzzy) and path is not None and isinstance(ev.page, int):
                ev.bbox = await asyncio.to_thread(pdfmod.find_bbox, path, ev.page, ev.quote, ev.value)
            outcome = FieldOutcome(flags=list(res.flags), vision_only=ev.verification == Verification.vision_only)
            outcomes[ekey(ev)] = outcome
            await self._emit(
                ctx,
                EventType.AUDIT_CHECK_RESULT,
                f"{ev.field} in {ev.source_document}: " + ("verified" if ev.verified else "not verified"),
                document_id=did,
                data={
                    "check": "evidence",
                    "field": ev.field,
                    "ref": ref,
                    "passed": ev.verified,
                    "verification": ev.verification.value,
                    "confidence": ev.confidence,
                    "page": ev.page,
                    "value": ev.value,
                    "quote": ev.quote,
                    "bbox": ev.bbox,
                    "flags": [x.value for x in res.flags],
                    "checks": [c.__dict__ for c in res.checks],
                    "document": ev.source_document,
                },
            )
            if res.ok or not res.needs_reextract or not ctx.allow_reextraction:
                return
            count = await asyncio.to_thread(self.repo.reextraction_count, did, ref)
            summary.conflicts += 1
            objection = "; ".join(c.detail for c in res.checks if not c.passed) or "evidence could not be verified"
            await self._emit(
                ctx,
                EventType.AUDIT_CONFLICT_FOUND,
                f"{ev.field} in {ev.source_document} could not be verified: {objection}",
                document_id=did,
                data={
                    "case": "A",
                    "field": ev.field,
                    "ref": ref,
                    "document": ev.source_document,
                    "page": ev.page,
                    "previous_value": ev.value,
                    "quote": ev.quote,
                    "flags": [x.value for x in res.flags],
                    "attempts_used": count,
                    "limit": limit,
                    "limit_reached": count >= limit,
                },
            )
            if count >= limit:
                outcome.limit_hit = True
                return
            whole = res.reread_whole_document or count >= 1 or not isinstance(ev.page, int)
            attempt = count + 1
            await self._emit(
                ctx,
                EventType.REEXTRACTION_REQUESTED,
                f"Asking extractor to re-read {'the whole document' if whole else f'page {ev.page}'} for {ev.field} (attempt {attempt}/{limit})",
                document_id=did,
                data={
                    "field": ev.field,
                    "ref": ref,
                    "page_hint": None if whole else ev.page,
                    "attempt": attempt,
                    "limit": limit,
                    "mode": "document" if whole else "page",
                    "document": ev.source_document,
                },
            )
            await asyncio.to_thread(self.repo.bump_reextraction, did, ref)
            summary.reextractions += 1
            old_value = ev.value
            try:
                new = await self.extractor.reextract(
                    doc,
                    ref,
                    pages=None if whole or not isinstance(ev.page, int) else [ev.page],
                    objection=f"{objection}. Re-read the page and return only a value that is explicitly printed.",
                )  # type: ignore[list-item]
            except (LLMError, TimeoutError) as e:
                log.warning("re-extraction call failed: %s", type(e).__name__)
                new = None
            if new is not None:
                set_ref(doc, ref, new)
                key = await asyncio.to_thread(self.repo.extraction_cache_key, did)
                if key:
                    await asyncio.to_thread(self.repo.save_extraction, doc, key, write_cache=True)
            changed = new is not None and str(new.value) != str(old_value)
            await self._emit(
                ctx,
                EventType.REEXTRACTION_COMPLETED,
                f"{ev.field}: {old_value} -> {new.value if new else 'no supported value found'}",
                document_id=did,
                data={
                    "field": ev.field,
                    "ref": ref,
                    "old_value": old_value,
                    "new_value": new.value if new else None,
                    "changed": changed,
                    "found": new is not None,
                    "page": new.page if new else None,
                    "attempt": attempt,
                    "document": ev.source_document,
                },
            )

    # ------------------------------------------------------ cross-doc (A/B)
    @staticmethod
    def _affected(by_fid: dict[str, Finding], did: str, fname: str) -> list[str]:
        return [fid for fid, f in by_fid.items() if any(e.document_id == did and e.field == fname for e in f.evidence)]

    async def _cross_document(self, ctx: AuditContext, docs: list[ExtractedDocument], by_fid: dict[str, Finding], auds: dict[str, FindingAuditor], summary: AuditSummary) -> None:
        for fname, flag in (("supplier_id", F.SUPPLIER_ID_CONFLICT), ("batch_id", F.BATCH_ID_CONFLICT)):
            items = [(d, getattr(d, fname)) for d in docs if getattr(d, fname) is not None]
            if len(items) < 2:
                continue
            distinct = {normalize_id(str(e.value)) for _, e in items}
            values = [{"document": e.source_document, "document_id": d.document_id, "value": e.value, "page": e.page, "quote": e.quote, "verified": e.verified} for d, e in items]
            if len(distinct) == 1:
                await self._emit(
                    ctx,
                    EventType.AUDIT_CHECK_RESULT,
                    f"{fname} is consistent across {len(items)} documents",
                    data={"check": "cross_document", "field": fname, "passed": True, "values": values},
                )
                continue
            affected = {fid for d, e in items for fid in self._affected(by_fid, d.document_id, fname)}
            genuine = all(e.verified for _, e in items)
            summary.conflicts += 1
            for fid in affected:
                auds[fid].add_flag(flag)
            if genuine:  # Case B: both quotes verify; re-reading cannot change what the documents say
                for fid in affected:
                    auds[fid].add_flag(flag, "Both sources were re-read and verified; this is a genuine inconsistency between documents. No re-extraction attempted.")
                    auds[fid].mark_verified()
            await self._emit(
                ctx,
                EventType.AUDIT_CONFLICT_FOUND,
                f"{fname} differs across documents: "
                + ", ".join(f"{v['document']}={v['value']}" for v in values)
                + (" (both sources verified; potential inconsistency for human review)" if genuine else ""),
                data={
                    "case": "B" if genuine else "unresolved",
                    "field": fname,
                    "genuine": genuine,
                    "values": values,
                    "flag": flag.value,
                    "reextraction_attempted": False,
                    "finding_ids": sorted(affected),
                },
                finding_id=next(iter(sorted(affected)), None),
            )
        for d in docs:
            if d.document_date and d.document_date.value is not None:
                dt = parse_iso_date(d.document_date.value)
                if dt is not None and dt > ctx.reference_date:
                    for fid in self._affected(by_fid, d.document_id, "document_date"):
                        auds[fid].add_flag(F.DATE_CONFLICT, f"{d.source_document} is dated after the reference date")
                    await self._emit(
                        ctx,
                        EventType.AUDIT_CHECK_RESULT,
                        f"{d.source_document} is dated after the reference date",
                        document_id=d.document_id,
                        data={"check": "date", "field": "document_date", "passed": False, "flag": F.DATE_CONFLICT.value},
                    )

    async def _certificates(self, ctx: AuditContext, docs: list[ExtractedDocument], by_fid: dict[str, Finding], auds: dict[str, FindingAuditor]) -> None:
        for d in docs:
            for c in d.certifications:
                vu = parse_iso_date(c.valid_until.value) if c.valid_until and c.valid_until.value is not None else None
                iss = parse_iso_date(c.issued_date.value) if c.issued_date and c.issued_date.value is not None else None
                flag: F | None = None
                detail = f"{c.name.value} is valid until {vu} (reference date {ctx.reference_date})"
                if vu is None:
                    flag, detail = F.CERT_VALIDITY_UNKNOWN, f"validity end date for {c.name.value} is missing or unreadable"
                elif vu < ctx.reference_date:
                    flag, detail = F.CERT_EXPIRED, f"{c.name.value} expired on {vu}, before the reference date {ctx.reference_date}"
                elif iss is not None and iss > vu:
                    flag, detail = F.DATE_CONFLICT, f"{c.name.value} issue date {iss} is after its validity end {vu}"
                if flag:
                    for fid in self._affected(by_fid, d.document_id, c.name.field):
                        auds[fid].add_flag(flag)
                await self._emit(
                    ctx,
                    EventType.AUDIT_CHECK_RESULT,
                    detail,
                    document_id=d.document_id,
                    data={"check": "certificate_validity", "field": c.name.field, "passed": flag is None, "flag": flag.value if flag else None, "document": d.source_document},
                )

    async def _absence(self, ctx: AuditContext, texts: dict[str, list[str] | None], by_fid: dict[str, Finding], auds: dict[str, FindingAuditor]) -> None:
        """A 'not found' conclusion is only supported if the token really is absent from the document text."""
        for fid, f in by_fid.items():
            for e in f.evidence:
                if e.value is not None or not e.field.startswith("certification:"):
                    continue
                token = normalize_id(e.field.split(":", 1)[1])
                pages = texts.get(e.document_id) or []
                present = any(token in normalize_id(normalize(t)) for t in pages)
                if present:
                    auds[fid].add_flag(F.CONCLUSION_UNSUPPORTED, f"{e.field.split(':', 1)[1]} appears in '{e.source_document}' although extraction reported it as absent")
                    auds[fid].mark_uncertain(ER.UNSUPPORTED_CONCLUSION)
                await self._emit(
                    ctx,
                    EventType.AUDIT_CHECK_RESULT,
                    f"Absence check for {e.field} in {e.source_document}: " + ("FOUND in text, conclusion unsupported" if present else "confirmed absent"),
                    finding_id=fid,
                    document_id=e.document_id,
                    data={"check": "absence", "field": e.field, "passed": not present, "document": e.source_document},
                )

    async def _semantic(self, ctx: AuditContext, by_fid: dict[str, Finding], auds: dict[str, FindingAuditor]) -> None:
        todo = [f for fid, f in by_fid.items() if not f.rule_id.startswith("DOC_INTAKE") and any(e.quote for e in f.evidence) and not auds[fid].has_blocking_flag()]
        sem = asyncio.Semaphore(self.settings.extraction_concurrency)

        async def one(f: Finding) -> tuple[Any, bool]:
            async with sem:
                return await semantic_check(f, provider=self.provider, repo=self.repo, timeout=self.settings.llm_timeout_seconds, cache_only=ctx.semantic_cache_only)

        results = await asyncio.gather(*(one(f) for f in todo))
        for f, (res, cached) in zip(todo, results, strict=True):
            faulted = bool(res and any(c.startswith("fault_injected") for c in res.concerns))
            if res is None:
                f.audit_notes.append("Semantic check not available for this run; deterministic checks only.")
                await self._emit(
                    ctx,
                    EventType.AUDIT_CHECK_RESULT,
                    f"{f.rule_id}: semantic check unavailable (deterministic checks stand)",
                    finding_id=f.finding_id,
                    data={"check": "semantic", "passed": None, "available": False},
                )
                continue
            if not res.supported:
                auds[f.finding_id].add_flag(F.CONCLUSION_UNSUPPORTED, res.rationale)
                auds[f.finding_id].mark_uncertain(ER.UNSUPPORTED_CONCLUSION)
            data: dict[str, Any] = {"check": "semantic", "passed": res.supported, "rationale": res.rationale, "concerns": res.concerns, "cached": cached, "available": True}
            if faulted:
                data["fault_injected"] = True
            await self._emit(
                ctx,
                EventType.AUDIT_CHECK_RESULT,
                f"{f.rule_id}: conclusion " + ("supported by the quote" if res.supported else "NOT supported by the quote"),
                finding_id=f.finding_id,
                data=data,
            )
