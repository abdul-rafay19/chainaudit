"""Agent 1: Evidence Extraction. One task per document; failures are isolated per document."""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any

from app.agents.extraction import prompts
from app.agents.extraction.classifier import classify
from app.config import Settings
from app.core import pdf as pdfmod
from app.core.events import EventBus
from app.core.logging import get_logger
from app.core.store import Repo
from app.enums import AgentName, DocType, EventType, Verification
from app.llm.base import LLMError, LLMProvider
from app.llm.mock_provider import FAULT_NOTE
from app.llm.schemas import ExtractionOutput, FieldReextraction, LLMField
from app.rules.engine import PROMPT_INJECTION_NOTE
from app.schemas.domain import Certification, EvidenceField, ExtractedDocument
from app.schemas.refs import get_ref, llm_field_name

log = get_logger("extractor")
INJECTION_RX = re.compile(r"ignore (all )?(previous|prior|above) instructions|system prompt|you are now|disregard (all|the|your) (previous|prior|above)", re.I)
FIELD_OK = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
VISION_CAP = 0.75


def cache_key(doc_sha: str, doc_type: str, provider: LLMProvider) -> str:
    raw = "|".join([doc_sha, doc_type, prompts.EXTRACTOR_VERSION, provider.name, provider.model, prompts.prompt_hash(doc_type)])
    return hashlib.sha256(raw.encode()).hexdigest()


def _clean_value(v: Any) -> str | float | None:
    if isinstance(v, str):
        v = v.strip()
        return v or None
    if isinstance(v, bool):
        return None
    return v


def to_evidence(lf: LLMField | None, *, name: str, doc_id: str, source: str, image_only: set[int], field_name: str | None = None) -> EvidenceField | None:
    """LLM field -> EvidenceField (null values are dropped: unsupported values stay null)."""
    if lf is None:
        return None
    value = _clean_value(lf.value)
    if value is None:
        return None
    conf = min(float(lf.confidence), 1.0)
    ver = Verification.unverified
    if lf.page is not None and lf.page in image_only:
        ver, conf = Verification.vision_only, min(conf, VISION_CAP)
    return EvidenceField(
        field=field_name or lf.field,
        value=value,
        unit=(lf.unit or None),
        source_document=source,
        document_id=doc_id,
        page=lf.page,
        quote=lf.quote,
        confidence=conf,
        llm_confidence=min(float(lf.confidence), 1.0),
        verification=ver,
        verified=False,
    )


def build_document(out: ExtractionOutput, *, doc_id: str, source: str, doc_type: DocType, provider: LLMProvider, page_count: int, image_only: list[int]) -> ExtractedDocument:
    io = set(image_only)

    def conv(lf: LLMField | None, fname: str | None = None) -> EvidenceField | None:
        return to_evidence(lf, name=source, doc_id=doc_id, source=source, image_only=io, field_name=fname)

    tests: list[EvidenceField] = []
    for t in out.test_results:
        fname = t.field if FIELD_OK.match(t.field or "") else "unknown_field"
        e = conv(t, fname)
        if e:
            tests.append(e)
    others: list[EvidenceField] = []
    for t in out.other:
        e = conv(t, t.field if FIELD_OK.match(t.field or "") else "unknown_field")
        if e:
            others.append(e)
    certs: list[Certification] = []
    for c in out.certifications:
        nm = conv(c.name)
        if nm is None:
            continue
        nm.field = f"certification:{nm.value}"
        certs.append(Certification(name=nm, issued_date=conv(c.issued_date, "issued_date"), valid_until=conv(c.valid_until, "valid_until")))
    return ExtractedDocument(
        document_id=doc_id,
        doc_type=doc_type,
        source_document=source,
        supplier_id=conv(out.supplier_id, "supplier_id"),
        batch_id=conv(out.batch_id, "batch_id"),
        document_date=conv(out.document_date, "document_date"),
        test_results=tests,
        certifications=certs,
        other=others,
        extraction_notes=[str(n)[:300] for n in out.notes][:20],
        provider=provider.name,
        model=provider.model,
        extractor_version=prompts.EXTRACTOR_VERSION,
        page_count=page_count,
        image_only_pages=image_only,
    )


def reset_verification(doc: ExtractedDocument) -> None:
    """Cached results never carry audit state: the Auditor recomputes everything."""
    for ref_ev in doc.all_fields():
        ref_ev.bbox, ref_ev.verified = None, False
        ref_ev.verification = Verification.vision_only if ref_ev.verification == Verification.vision_only else Verification.unverified


class ExtractionAgent:
    def __init__(self, provider: LLMProvider, repo: Repo, bus: EventBus, settings: Settings) -> None:
        self.provider, self.repo, self.bus, self.settings = provider, repo, bus, settings

    async def _fail(self, wid: str, did: str, name: str, reason: str, replay: bool) -> None:
        await asyncio.to_thread(self.repo.update_document, did, status="failed", error=reason)
        await self.bus.emit(
            wid,
            EventType.EXTRACTION_FAILED,
            agent=AgentName.extractor,
            document_id=did,
            replay=replay,
            message=f"Could not process {name}: {reason}",
            data={"reason": reason, "document": name},
        )

    async def process(self, wid: str, did: str, sem: asyncio.Semaphore, *, replay: bool = False) -> ExtractedDocument | None:
        row = await asyncio.to_thread(self.repo.get_document, did)
        assert row is not None
        name = row.original_name
        await self.bus.emit(wid, EventType.EXTRACTION_STARTED, agent=AgentName.extractor, document_id=did, replay=replay, message=f"Reading {name}", data={"document": name})
        try:
            info = await asyncio.to_thread(pdfmod.analyze, Path(row.stored_path))
        except pdfmod.PdfError as e:
            await self._fail(wid, did, name, str(e), replay)
            return None
        except Exception as e:  # never let one document take the pack down
            log.exception("analyze failed")
            await self._fail(wid, did, name, f"unexpected read error ({type(e).__name__})", replay)
            return None

        timeout = self.settings.llm_timeout_seconds
        try:
            async with sem:
                doc_type, method = await classify(filename=name, first_page_text=info.texts[0], provider=self.provider, timeout=timeout)
                key = cache_key(row.sha256, doc_type.value, self.provider)
                cached = await asyncio.to_thread(self.repo.cache_get, key)
                if cached is not None:
                    doc = cached.model_copy(deep=True)
                    reset_verification(doc)
                    for fld in doc.all_fields():
                        fld.document_id, fld.source_document = did, name
                    doc.document_id, doc.source_document = did, name
                else:
                    doc = await self._call_llm(did, name, doc_type, info, row.stored_path)
        except (LLMError, TimeoutError) as e:
            await self._fail(wid, did, name, f"model call failed ({type(e).__name__})", replay)
            return None
        except Exception as e:
            log.exception("extraction failed")
            await self._fail(wid, did, name, f"unexpected extraction error ({type(e).__name__})", replay)
            return None

        injected = any(INJECTION_RX.search(t) for t in info.texts)
        if injected and PROMPT_INJECTION_NOTE not in doc.extraction_notes:
            doc.extraction_notes.append(PROMPT_INJECTION_NOTE)
        doc.page_count, doc.image_only_pages = info.page_count, info.image_only_pages
        await asyncio.to_thread(self.repo.update_document, did, doc_type=doc_type.value, page_count=info.page_count, status="extracted")
        await asyncio.to_thread(self.repo.save_extraction, doc, key, write_cache=cached is None)
        faults = [n[len(FAULT_NOTE) :] for n in doc.extraction_notes if n.startswith(FAULT_NOTE)]
        data: dict[str, Any] = {
            "document": name,
            "doc_type": doc_type.value,
            "classified_by": method,
            "field_count": len(doc.all_fields()),
            "cached": cached is not None,
            "provider": self.provider.name,
            "model": self.provider.model,
            "pages": info.page_count,
            "image_only_pages": info.image_only_pages,
            "possible_prompt_injection": injected,
        }
        if faults and cached is None:
            data["fault_injected"], data["fault"] = True, faults
        await self.bus.emit(
            wid,
            EventType.EXTRACTION_COMPLETED,
            agent=AgentName.extractor,
            document_id=did,
            replay=replay,
            message=f"Extracted {len(doc.all_fields())} field(s) from {name}" + (" (cached)" if cached is not None else ""),
            data=data,
        )
        return doc

    async def _call_llm(self, did: str, name: str, doc_type: DocType, info: pdfmod.PdfInfo, stored_path: str) -> ExtractedDocument:
        pages: dict[int, str] = {}
        images: list[bytes] = []
        for n, text in enumerate(info.texts, start=1):
            if n in info.image_only_pages:
                if self.provider.supports_vision:
                    images.append(await asyncio.to_thread(pdfmod.render_png, Path(stored_path), n, 200))
                    pages[n] = f"[image-only page: see attached image #{len(images)}]"
                else:
                    pages[n] = "[image-only page: no text layer]"
            else:
                pages[n] = text
        out = await asyncio.wait_for(
            self.provider.generate_structured(
                system=prompts.system_prompt(doc_type.value),
                user_text=prompts.build_extraction_user(doc_type.value, pages),
                images=images or None,
                schema=ExtractionOutput,
                timeout=self.settings.llm_timeout_seconds,
            ),
            timeout=self.settings.llm_timeout_seconds + 5,
        )
        return build_document(out, doc_id=did, source=name, doc_type=doc_type, provider=self.provider, page_count=info.page_count, image_only=info.image_only_pages)

    async def reextract(self, doc: ExtractedDocument, ref: str, *, pages: list[int] | None, objection: str) -> EvidenceField | None:
        """Re-read ONE field using only `pages` (or the whole document when None).

        Returns the new evidence or None when the model could not find a supported value.
        """
        row = await asyncio.to_thread(self.repo.get_document, doc.document_id)
        assert row is not None
        info = await asyncio.to_thread(pdfmod.analyze, Path(row.stored_path))
        wanted = [p for p in (pages or range(1, info.page_count + 1)) if 1 <= p <= info.page_count]
        page_text = {p: info.texts[p - 1] for p in wanted if p not in info.image_only_pages}
        if not page_text:
            return None
        old = get_ref(doc, ref)
        label = llm_field_name(doc, ref)
        out = await asyncio.wait_for(
            self.provider.generate_structured(
                system=prompts.REEXTRACT_SYSTEM,
                user_text=prompts.build_reextraction_user(doc.doc_type.value, label, page_text, old.value if old else None, objection),
                images=None,
                schema=FieldReextraction,
                timeout=self.settings.llm_timeout_seconds,
            ),
            timeout=self.settings.llm_timeout_seconds + 5,
        )
        ev = to_evidence(
            out.field,
            name=doc.source_document,
            doc_id=doc.document_id,
            source=doc.source_document,
            image_only=set(info.image_only_pages),
            field_name=old.field if old else out.field.field,
        )
        if ev is not None and ref.endswith(":name"):
            ev.field = f"certification:{ev.value}"
        return ev
