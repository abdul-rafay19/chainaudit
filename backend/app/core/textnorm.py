"""Normalisation + deterministic quote / value matching. No LLM, no I/O."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dateutil import parser as dateparser
from rapidfuzz import fuzz

from app.enums import Verification

FUZZY_THRESHOLD = 92.0
_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2212"), "-")
_QUOTES = {ord("\u2018"): "'", ord("\u2019"): "'", ord("\u201c"): '"', ord("\u201d"): '"'}


def normalize(s: str) -> str:
    """NFKC, de-hyphenate line breaks, unify dashes/quotes, collapse whitespace, casefold."""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u00ad", "")
    s = s.translate(_DASHES).translate(_QUOTES)
    s = re.sub(r"(?<=[A-Za-z0-9])-[ \t]*\r?\n[ \t]*(?=[A-Za-z0-9])", "", s)  # de-hyphenate wrapped words/ids
    s = re.sub(r"\s+", " ", s).strip()
    return s.casefold()


def normalize_id(s: str) -> str:
    """Identity normalisation: trim, uppercase, strip inner whitespace."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "").translate(_DASHES)).upper()


@dataclass(frozen=True)
class QuoteMatch:
    kind: Verification
    score: float


def match_quote(quote: str | None, page_text: str) -> QuoteMatch:
    """exact (verbatim substring) | normalized | fuzzy (>=92, NOT verified) | not_found."""
    if not quote or not quote.strip() or not page_text:
        return QuoteMatch(Verification.not_found, 0.0)
    if quote in page_text:
        return QuoteMatch(Verification.exact, 100.0)
    nq, np_ = normalize(quote), normalize(page_text)
    if nq and nq in np_:
        return QuoteMatch(Verification.normalized, 100.0)
    if len(nq) >= 8:
        score = float(fuzz.partial_ratio(nq, np_))
        if score >= FUZZY_THRESHOLD:
            return QuoteMatch(Verification.fuzzy, score)
        return QuoteMatch(Verification.not_found, score)
    return QuoteMatch(Verification.not_found, 0.0)


# ---------------------------------------------------------------- values
_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_DATE_PATTERNS = [
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    re.compile(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"),
    re.compile(rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}\.?,?\s+\d{{4}}", re.I),
    re.compile(rf"{_MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}", re.I),
]
DATE_FIELDS = {"document_date", "issued_date", "valid_until"}


def to_decimal(v: object) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def parse_iso_date(v: object) -> date | None:
    """Strict ISO (YYYY-MM-DD, optional time part). Anything else is 'unparseable'."""
    if not isinstance(v, str):
        return None
    t = v.strip()
    try:
        return date.fromisoformat(t[:10]) if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ].*)?", t) else None
    except ValueError:
        return None


def _dates_in(text: str) -> list[date]:
    out: list[date] = []
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            try:
                d = dateparser.parse(m.group(0), dayfirst=not re.match(r"\d{4}-", m.group(0)), default=datetime(1900, 1, 1))
                out.append(d.date())
            except (ValueError, OverflowError):
                continue
    return out


def value_in_quote(field: str, value: object, quote: str | None) -> bool:
    """Does the extracted value genuinely appear in the quote?"""
    if value is None or quote is None:
        return False
    base_field = field.split(":")[0]
    if base_field in DATE_FIELDS:
        target = parse_iso_date(value)
        return target is not None and target in _dates_in(quote)
    dec = to_decimal(value) if isinstance(value, int | float) else None
    if dec is not None:
        return any((n := to_decimal(tok)) is not None and n == dec for tok in _NUM.findall(quote))
    nv, nq = normalize_id(str(value)), normalize_id(quote)
    return bool(nv) and nv in nq
