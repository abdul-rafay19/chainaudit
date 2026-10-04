"""Document type classification: filename keywords, first-page keywords, then a cheap LLM call."""

from __future__ import annotations

import re

from app.agents.extraction.prompts import CLASSIFY_SYSTEM, build_classify_user
from app.enums import DocType
from app.llm.base import LLMError, LLMProvider
from app.llm.schemas import ClassificationOutput

_NAME = [
    (re.compile(r"cert", re.I), DocType.certificate),
    (re.compile(r"declar|decl[_\-. ]", re.I), DocType.supplier_declaration),
    (re.compile(r"lab|test|report|result", re.I), DocType.lab_report),
]
_PAGE = [
    (re.compile(r"certificate of|certification body", re.I), DocType.certificate),
    (re.compile(r"supplier declaration", re.I), DocType.supplier_declaration),
    (re.compile(r"test report|test results|laborator", re.I), DocType.lab_report),
]


def by_filename(name: str) -> DocType | None:
    return next((t for rx, t in _NAME if rx.search(name)), None)


def by_text(text: str) -> DocType | None:
    return next((t for rx, t in _PAGE if rx.search(text)), None)


async def classify(*, filename: str, first_page_text: str, provider: LLMProvider, timeout: float) -> tuple[DocType, str]:
    """Returns (type, method). Content beats filename when they disagree."""
    n, p = by_filename(filename), by_text(first_page_text)
    if p is not None:
        return p, "page_keywords" if n is None or n == p else "page_keywords_overrode_filename"
    if n is not None:
        return n, "filename"
    if first_page_text.strip():
        try:
            out = await provider.generate_structured(
                system=CLASSIFY_SYSTEM, user_text=build_classify_user(filename, first_page_text), images=None, schema=ClassificationOutput, timeout=timeout
            )
            if out.confidence >= 0.6 and out.doc_type != "unknown":
                return DocType(out.doc_type), "llm"
        except LLMError:
            pass
    return DocType.unknown, "unclassified"
