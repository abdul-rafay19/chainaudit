"""Address a single EvidenceField inside an ExtractedDocument with a stable string ref."""

from __future__ import annotations

from app.schemas.domain import EvidenceField, ExtractedDocument

TOP = ("supplier_id", "batch_id", "document_date")


def iter_refs(doc: ExtractedDocument) -> list[tuple[str, EvidenceField]]:
    out: list[tuple[str, EvidenceField]] = []
    for name in TOP:
        ev = getattr(doc, name)
        if ev is not None:
            out.append((name, ev))
    out.extend((f"test:{i}", ev) for i, ev in enumerate(doc.test_results))
    for i, c in enumerate(doc.certifications):
        out.append((f"cert:{i}:name", c.name))
        if c.issued_date:
            out.append((f"cert:{i}:issued_date", c.issued_date))
        if c.valid_until:
            out.append((f"cert:{i}:valid_until", c.valid_until))
    out.extend((f"other:{i}", ev) for i, ev in enumerate(doc.other))
    return out


def get_ref(doc: ExtractedDocument, ref: str) -> EvidenceField | None:
    return dict(iter_refs(doc)).get(ref)


def set_ref(doc: ExtractedDocument, ref: str, ev: EvidenceField) -> None:
    parts = ref.split(":")
    if ref in TOP:
        setattr(doc, ref, ev)
    elif parts[0] == "test":
        doc.test_results[int(parts[1])] = ev
    elif parts[0] == "other":
        doc.other[int(parts[1])] = ev
    elif parts[0] == "cert":
        cert = doc.certifications[int(parts[1])]
        setattr(cert, parts[2], ev)
    else:
        raise KeyError(ref)


def llm_field_name(doc: ExtractedDocument, ref: str) -> str:
    """Field name used in re-extraction prompts."""
    if ref in TOP:
        return ref
    parts = ref.split(":")
    if parts[0] == "cert":
        return "certification" if parts[2] == "name" else parts[2]
    ev = get_ref(doc, ref)
    return ev.field if ev else ref
