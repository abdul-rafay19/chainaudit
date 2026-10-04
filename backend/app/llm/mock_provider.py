"""Deterministic OFFLINE mock provider.

Reads the text layer of the synthetic demo PDFs with regexes. It exists for tests,
offline development and a reliable demo fallback, and is labelled `mock` everywhere.
It cannot read images. Fault injection (MOCK_FAULTS) returns a WRONG value on the
FIRST attempt only, so the Auditor -> Extractor loop can be demonstrated; faulted
outputs carry a `fault_injected:<key>` note which the extractor turns into
`data.fault_injected=true` on events.
"""

from __future__ import annotations

import hashlib
import re
from typing import TypeVar

from dateutil import parser as dateparser
from pydantic import BaseModel

from app.llm.schemas import (
    ClassificationOutput,
    CorrectiveText,
    ExtractionOutput,
    FieldReextraction,
    LLMCertification,
    LLMField,
    SemanticCheckResult,
)

T = TypeVar("T", bound=BaseModel)
FAULT_NOTE = "fault_injected:"


def _doc_body(user_text: str) -> str:
    m = re.search(r"<untrusted_document>\n(.*?)\n</untrusted_document>", user_text, re.S)
    return m.group(1) if m else user_text


def _pages(user_text: str) -> dict[int, str]:
    body = _doc_body(user_text)
    parts = re.split(r"=== PAGE (\d+) ===\n?", body)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _clean(s: str) -> str:
    return " ".join(s.split())


def _find(pages: dict[int, str], pattern: str, flags: int = 0) -> tuple[int, re.Match[str]] | None:
    for n in sorted(pages):
        m = re.search(pattern, pages[n], flags)
        if m:
            return n, m
    return None


def _field(name: str, found: tuple[int, re.Match[str]] | None, *, group: int = 1, unit: str | None = None, number: bool = False, conf: float = 0.95) -> LLMField | None:
    if not found:
        return None
    n, m = found
    raw = m.group(group).strip()
    value: str | float = float(raw) if number else raw
    return LLMField(field=name, value=value, unit=unit, page=n, quote=_clean(m.group(0)), confidence=conf)


def _date_field(name: str, pages: dict[int, str], label: str) -> LLMField | None:
    found = _find(pages, rf"{label}\s*:\s*(\d{{4}}-\d{{2}}-\d{{2}})", re.I)
    if found:
        return _field(name, found)
    loose = _find(pages, rf"{label}\s*:\s*([^\n]+)", re.I)
    if loose:
        try:
            iso = dateparser.parse(loose[1].group(1), dayfirst=True).date().isoformat()
        except (ValueError, OverflowError):
            return None
        n, m = loose
        return LLMField(field=name, value=iso, unit=None, page=n, quote=_clean(m.group(0)), confidence=0.85)
    return None


def _supplier(pages: dict[int, str]) -> LLMField | None:
    return _field("supplier_id", _find(pages, r"Supplier ID\s*:\s*([A-Za-z]{2,}-?\d+)"))


def _batch(pages: dict[int, str]) -> LLMField | None:
    return _field("batch_id", _find(pages, r"Batch ID\s*:\s*([A-Za-z]{1,4}-?\d+)"))


def _chemical(pages: dict[int, str]) -> LLMField | None:
    found = _find(pages, r"Chemical concentration\s+([0-9]+(?:\.[0-9]+)?)\s*(ppm|mg/kg|%)", re.I)
    if not found:
        return None
    return _field("chemical_ppm", found, number=True, unit=found[1].group(2))


def _certs(pages: dict[int, str]) -> list[LLMCertification]:
    name = _field("certification", _find(pages, r"Certification\s*:\s*([A-Za-z0-9_]+)"))
    if not name:
        return []
    return [LLMCertification(name=name, issued_date=_date_field("issued_date", pages, "Issue Date"), valid_until=_date_field("valid_until", pages, "Valid Until"))]


def _doc_date(doc_type: str, pages: dict[int, str]) -> LLMField | None:
    label = {"lab_report": "Report Date", "supplier_declaration": "Declaration Date", "certificate": "Issue Date"}.get(doc_type)
    if label:
        f = _date_field("document_date", pages, label)
        if f:
            return f
    return _date_field("document_date", pages, r"(?:Report |Declaration |Issue |Document )?Date")


class MockProvider:
    name = "mock"
    model = "mock-regex-v1"
    supports_vision = False

    def __init__(self, faults: set[str] | None = None) -> None:
        self.faults = set(faults or ())
        self._fired: set[tuple[str, str]] = set()
        self.calls = 0  # asserted by tests (e.g. rerun-rules must make 0 LLM calls)

    # ---- fault helper -------------------------------------------------
    def _fault_once(self, key: str, user_text: str) -> bool:
        sig = (key, hashlib.sha256(user_text.encode()).hexdigest())
        if key in self.faults and sig not in self._fired:
            self._fired.add(sig)
            return True
        return False

    # ---- provider API --------------------------------------------------
    async def generate_structured(self, *, system: str, user_text: str, images: list[bytes] | None, schema: type[T], timeout: float) -> T:
        self.calls += 1
        if schema is ExtractionOutput:
            return self._extract(user_text, bool(images))  # type: ignore[return-value]
        if schema is FieldReextraction:
            return self._reextract(user_text)  # type: ignore[return-value]
        if schema is ClassificationOutput:
            return self._classify(user_text)  # type: ignore[return-value]
        if schema is SemanticCheckResult:
            return self._semantic(user_text)  # type: ignore[return-value]
        if schema is CorrectiveText:
            return self._corrective(user_text)  # type: ignore[return-value]
        raise ValueError(f"mock provider does not support schema {schema.__name__}")

    async def generate_text(self, *, system: str, user_text: str, timeout: float) -> str:
        self.calls += 1
        return "mock provider text response"

    # ---- implementations ----------------------------------------------
    def _extract(self, user_text: str, has_images: bool) -> ExtractionOutput:
        m = re.search(r"Document type: (\w+)", user_text)
        dt = m.group(1) if m else "unknown"
        pages = _pages(user_text)
        notes: list[str] = []
        if has_images:
            notes.append("mock provider cannot read images; image-only pages were not extracted")
        out = ExtractionOutput(
            supplier_id=_supplier(pages),
            batch_id=_batch(pages) if dt in {"lab_report", "supplier_declaration", "unknown"} else None,
            document_date=_doc_date(dt, pages),
            test_results=[f for f in [_chemical(pages)] if f] if dt == "lab_report" else [],
            certifications=_certs(pages) if dt == "certificate" else [],
            other=[],
            notes=notes,
        )
        if dt == "lab_report" and out.batch_id and self._fault_once("lab_report.batch_id", user_text):
            out.batch_id = self._wrong_batch(pages, out.batch_id)
            out.notes.append(FAULT_NOTE + "lab_report.batch_id")
        return out

    @staticmethod
    def _wrong_batch(pages: dict[int, str], true: LLMField) -> LLMField:
        decoy = _find(pages, r"(?:Previous|Prior|Reference|Ref)[^\n:]*:\s*([A-Za-z]{1,4}-?\d+)", re.I)
        if decoy:  # a real, verbatim line from the page that is NOT this report's batch
            return LLMField(field="batch_id", value=decoy[1].group(1), unit=None, page=decoy[0], quote=_clean(decoy[1].group(0)), confidence=0.9)
        digits = re.search(r"(\d+)$", str(true.value))
        wrong = f"BT-{int(digits.group(1)) - 6 if digits else 1}"
        return LLMField(field="batch_id", value=wrong, unit=None, page=true.page, quote=f"Batch ID: {wrong}", confidence=0.9)

    def _reextract(self, user_text: str) -> FieldReextraction:
        m = re.search(r"Re-extract ONLY this field: (\w+)", user_text)
        fname = m.group(1) if m else ""
        dm = re.search(r"Document type: (\w+)", user_text)
        dt = dm.group(1) if dm else "unknown"
        pages = _pages(user_text)
        f: LLMField | None = None
        if fname == "supplier_id":
            f = _supplier(pages)
        elif fname == "batch_id":
            f = _batch(pages)
        elif fname == "document_date":
            f = _doc_date(dt, pages)
        elif fname == "chemical_ppm":
            f = _chemical(pages)
        elif fname in {"valid_until", "issued_date"}:
            f = _date_field(fname, pages, "Valid Until" if fname == "valid_until" else "Issue Date")
        elif fname == "certification":
            f = _field("certification", _find(pages, r"Certification\s*:\s*([A-Za-z0-9_]+)"))
        return FieldReextraction(field=f or LLMField(field=fname, value=None, unit=None, page=None, quote=None, confidence=0.0))

    @staticmethod
    def _classify(user_text: str) -> ClassificationOutput:
        t = _doc_body(user_text).lower()
        for needle, kind in (("certificate of", "certificate"), ("supplier declaration", "supplier_declaration"), ("test report", "lab_report"), ("test results", "lab_report")):
            if needle in t:
                return ClassificationOutput(doc_type=kind, confidence=0.9)  # type: ignore[arg-type]
        return ClassificationOutput(doc_type="unknown", confidence=0.3)

    def _semantic(self, user_text: str) -> SemanticCheckResult:
        rule = re.search(r"Rule id: (\S+)", user_text)
        quote = re.search(r"Quote: (.*)", user_text)
        rid = rule.group(1) if rule else ""
        if f"semantic.{rid}" in self.faults and self._fault_once(f"semantic.{rid}", user_text):
            return SemanticCheckResult(supported=False, rationale="Injected test fault: conclusion treated as unsupported.", concerns=[FAULT_NOTE + f"semantic.{rid}"])
        ok = bool(quote and quote.group(1).strip() and quote.group(1).strip() != "None")
        return SemanticCheckResult(
            supported=ok, rationale="Mock check: a quote is present." if ok else "Mock check: no quote to support the conclusion.", concerns=[] if ok else ["no supporting quote"]
        )

    @staticmethod
    def _corrective(user_text: str) -> CorrectiveText:
        def g(label: str) -> str:
            m = re.search(rf"^{label}: (.*)$", user_text, re.M)
            return m.group(1).strip() if m else ""

        rule, reason, ev, fw, sup = g("Rule"), g("Finding"), g("Evidence"), g("Framework"), g("Supplier")
        who = f" ({sup})" if sup else ""
        en = (
            f"Hello{who},\n\nDuring our review of the documents you submitted, we noted the following: {reason}\n"
            f"Source: {ev}\nThis was assessed against the configured check {rule} ({fw}).\n\n"
            "Could you please confirm whether this is correct and, if not, send updated or corrected documents? "
            "If there is context we should know about, we are happy to review it.\n\nThank you."
        )
        ur = (
            f"Assalam o Alaikum{who},\n\nAap ke jama karaye gaye documents ke jaiza mein humein yeh baat nazar aayi: {reason}\n"
            f"Zariya: {ev}\nYeh jaiza configured check {rule} ({fw}) ke mutabiq kiya gaya hai.\n\n"
            "Barae meharbani tasdeeq karein ke yeh durust hai ya nahin, aur agar nahin to updated ya durust documents bhej dein. "
            "Agar koi aisi baat hai jo humein maloom honi chahiye to hum use dekhne ke liye tayyar hain.\n\nShukriya."
        )
        return CorrectiveText(en=en, roman_ur=ur)
