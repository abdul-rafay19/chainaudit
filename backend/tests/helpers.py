"""Builders for engine/auditor unit tests (synthetic evidence only)."""

from __future__ import annotations

from app.enums import DocType, Verification
from app.schemas.domain import Certification, EvidenceField, ExtractedDocument


def ev(field: str, value, *, doc="d1", name="doc.pdf", page=1, quote=None, conf=0.95, unit=None, ver=Verification.exact) -> EvidenceField:
    return EvidenceField(
        field=field,
        value=value,
        unit=unit,
        source_document=name,
        document_id=doc,
        page=page,
        quote=quote if quote is not None else f"{field}: {value}",
        confidence=conf,
        llm_confidence=conf,
        verification=ver,
        verified=ver in (Verification.exact, Verification.normalized),
    )


def lab(
    doc="lab", name="lab.pdf", *, supplier="SUP-001", batch="BT-2047", ppm=12.1, unit="ppm", date="2026-09-18", conf=0.95, ver=Verification.exact, notes=None
) -> ExtractedDocument:
    return ExtractedDocument(
        document_id=doc,
        doc_type=DocType.lab_report,
        source_document=name,
        supplier_id=ev("supplier_id", supplier, doc=doc, name=name, conf=conf, ver=ver) if supplier else None,
        batch_id=ev("batch_id", batch, doc=doc, name=name, page=2, conf=conf, ver=ver) if batch else None,
        document_date=ev("document_date", date, doc=doc, name=name, conf=conf, quote=f"Report Date: {date}", ver=ver) if date else None,
        test_results=[ev("chemical_ppm", ppm, doc=doc, name=name, page=2, unit=unit, conf=conf, quote=f"Chemical concentration {ppm} {unit}", ver=ver)] if ppm is not None else [],
        extraction_notes=notes or [],
    )


def cert(doc="cert", name="cert.pdf", *, supplier="SUP-001", cert_name="DEMO_CERT_CHEM_L2", valid_until="2027-03-31", conf=0.95) -> ExtractedDocument:
    c = Certification(
        name=ev("certification:DEMO_CERT_CHEM_L2", cert_name, doc=doc, name=name, conf=conf, quote=f"Certification: {cert_name}"),
        valid_until=ev("valid_until", valid_until, doc=doc, name=name, conf=conf, quote=f"Valid Until: {valid_until}") if valid_until else None,
    )
    return ExtractedDocument(
        document_id=doc, doc_type=DocType.certificate, source_document=name, supplier_id=ev("supplier_id", supplier, doc=doc, name=name) if supplier else None, certifications=[c]
    )


def decl(doc="decl", name="decl.pdf", *, supplier="SUP-001", batch="BT-2047", date="2026-09-20") -> ExtractedDocument:
    return ExtractedDocument(
        document_id=doc,
        doc_type=DocType.supplier_declaration,
        source_document=name,
        supplier_id=ev("supplier_id", supplier, doc=doc, name=name) if supplier else None,
        batch_id=ev("batch_id", batch, doc=doc, name=name) if batch else None,
        document_date=ev("document_date", date, doc=doc, name=name, quote=f"Declaration Date: {date}") if date else None,
    )
