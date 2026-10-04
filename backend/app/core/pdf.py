"""PyMuPDF helpers: text per page, text-layer coverage, quote search, highlighted render."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.core.textnorm import normalize

IMAGE_LIKE_CHARS = 30


class PdfError(Exception):
    """Unreadable / encrypted / empty document."""


@dataclass
class PdfInfo:
    page_count: int
    texts: list[str]  # index 0 = page 1
    image_only_pages: list[int]  # 1-based


def open_doc(path: Path | str) -> pymupdf.Document:
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:  # corrupt / unsupported
        raise PdfError(f"cannot open document: {type(e).__name__}") from e
    if doc.needs_pass or doc.is_encrypted:
        doc.close()
        raise PdfError("document is encrypted")
    if doc.page_count == 0:
        doc.close()
        raise PdfError("document has zero pages")
    return doc


def analyze(path: Path | str) -> PdfInfo:
    doc = open_doc(path)
    try:
        texts: list[str] = []
        image_only: list[int] = []
        for i in range(1, doc.page_count + 1):
            t = doc[i - 1].get_text("text") or ""
            texts.append(t)
            if len(t.strip()) < IMAGE_LIKE_CHARS:
                image_only.append(i)
        return PdfInfo(doc.page_count, texts, image_only)
    except PdfError:
        raise
    except Exception as e:
        raise PdfError(f"cannot read text layer: {type(e).__name__}") from e
    finally:
        doc.close()


def page_text(path: Path | str, page: int) -> str | None:
    """Text of a 1-based page, None if the page does not exist."""
    doc = open_doc(path)
    try:
        if page < 1 or page > doc.page_count:
            return None
        return doc[page - 1].get_text("text") or ""
    finally:
        doc.close()


def render_png(path: Path | str, page: int, dpi: int = 200) -> bytes:
    doc = open_doc(path)
    try:
        return doc[page - 1].get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()


def _union(rects: list[pymupdf.Rect]) -> list[float] | None:
    if not rects:
        return None
    r = pymupdf.Rect(rects[0])
    for x in rects[1:]:
        r |= x
    return [round(float(v), 2) for v in (r.x0, r.y0, r.x1, r.y1)]


def find_bbox(path: Path | str, page: int, quote: str | None, value: str | float | None = None) -> list[float] | None:
    """Locate a quote on a page. Falls back to the widest matching word span, then the value token."""
    doc = open_doc(path)
    try:
        if page < 1 or page > doc.page_count:
            return None
        pg = doc[page - 1]
        if quote:
            hits = pg.search_for(quote)
            if hits:
                return _union(list(hits))
            span = _word_span(pg, quote)
            if span:
                return _union(span)
        if value is not None:
            token = str(value)
            hits = pg.search_for(token)
            if hits:
                return _union(list(hits[:1]))
        return None
    finally:
        doc.close()


def _word_span(pg: pymupdf.Page, quote: str) -> list[pymupdf.Rect] | None:
    words = pg.get_text("words")  # (x0,y0,x1,y1,word,block,line,wordno)
    qtokens = normalize(quote).split(" ")
    if not qtokens or not words:
        return None
    wnorm = [normalize(w[4]) for w in words]
    best: tuple[int, int] | None = None
    best_len = 0
    for i in range(len(words)):
        j, k = i, 0
        while j < len(words) and k < len(qtokens):
            if wnorm[j] == qtokens[k]:
                j += 1
                k += 1
            elif k == 0:
                break
            else:
                break
        if k > best_len:
            best_len, best = k, (i, j)
    if best is None or best_len < max(1, min(2, len(qtokens))):
        return None
    return [pymupdf.Rect(w[:4]) for w in words[best[0] : best[1]]]


def render_highlighted(path: Path | str, page: int, bbox: list[float] | None, dpi: int = 150) -> bytes:
    """Render a page with a semi-transparent rectangle over bbox. The source file is never modified."""
    doc = open_doc(path)
    try:
        pg = doc[page - 1]
        if bbox:
            r = pymupdf.Rect(*bbox)
            r = pymupdf.Rect(r.x0 - 2, r.y0 - 2, r.x1 + 2, r.y1 + 2)
            pg.draw_rect(r, color=(0.95, 0.65, 0.0), fill=(1.0, 0.85, 0.2), fill_opacity=0.35, width=1.2, overlay=True)
        return pg.get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()
