from pathlib import Path

import pytest

from app.core import pdf as pdfmod
from app.core.errors import AppError
from app.core.storage import sanitize_display_name, sniff_kind, store_bytes, validate_upload
from app.core.textnorm import match_quote, normalize, normalize_id, parse_iso_date, value_in_quote
from app.enums import Verification

DEMO = Path(__file__).resolve().parents[2] / "demo_data"
LAB = DEMO / "clean_supplier" / "lab_report_BT-2047.pdf"
FAILED_LAB = DEMO / "failed_supplier" / "lab_report_BT-2047.pdf"


# ---------------------------------------------------------------- textnorm
def test_normalize_basics():
    assert normalize("  Hello\u00a0 WORLD\n\tfoo ") == "hello world foo"
    assert normalize("BT\u20132047") == "bt-2047"
    assert normalize("trans-\nport") == "transport"  # de-hyphenated line break
    assert normalize_id(" bt - 2047".replace(" - ", "-")) == "BT-2047"
    assert normalize_id("sup 001") == "SUP001"


def test_match_quote_levels():
    page = "Batch ID: BT-2047\nChemical concentration\n12.1\nppm\n"
    assert match_quote("Batch ID: BT-2047", page).kind == Verification.exact
    assert match_quote("Chemical concentration 12.1 ppm", page).kind == Verification.normalized
    assert match_quote("BATCH ID: bt-2047", page).kind == Verification.normalized
    assert match_quote("Batch ID: BT-2048", page).kind != Verification.exact
    assert match_quote("Previous report reference: BT-2041", page).kind == Verification.not_found
    assert match_quote("", page).kind == Verification.not_found
    assert match_quote(None, page).kind == Verification.not_found


def test_match_quote_fuzzy_is_not_exact():
    page = "The quick brown fox jumps over the lazy dog near the riverbank today."
    m = match_quote("The quick brown fox jumps ovr the lazy dog near the riverbank today.", page)
    assert m.kind == Verification.fuzzy and m.score >= 92


def test_value_in_quote():
    assert value_in_quote("chemical_ppm", 12.1, "Chemical concentration 12.1 ppm")
    assert value_in_quote("chemical_ppm", 12.1, "result: 12.10 ppm")
    assert not value_in_quote("chemical_ppm", 17.4, "Chemical concentration 12.1 ppm")
    assert value_in_quote("batch_id", "BT-2047", "Batch ID: BT-2047")
    assert not value_in_quote("batch_id", "BT-2041", "Batch ID: BT-2047")
    assert value_in_quote("document_date", "2026-09-18", "Report Date: 2026-09-18")
    assert value_in_quote("document_date", "2026-09-18", "Reported on 18 September 2026")
    assert not value_in_quote("document_date", "2026-09-19", "Report Date: 2026-09-18")
    assert value_in_quote("certification:DEMO_CERT_CHEM_L2", "DEMO_CERT_CHEM_L2", "Certification: DEMO_CERT_CHEM_L2")
    assert not value_in_quote("batch_id", None, "x")


def test_parse_iso_date_strict():
    assert str(parse_iso_date("2026-09-18")) == "2026-09-18"
    assert parse_iso_date("18/09/2026") is None
    assert parse_iso_date("2026-13-40") is None
    assert parse_iso_date(None) is None


# ---------------------------------------------------------------- storage
def test_sniff_and_validate():
    assert sniff_kind(b"%PDF-1.7 ...") == "pdf"
    assert sniff_kind(b"\x89PNG\r\n\x1a\nxxxx") == "png"
    assert sniff_kind(b"\xff\xd8\xff\xe0") == "jpg"
    assert sniff_kind(b"MZ\x90\x00") is None
    v = validate_upload("../../etc/passwd.pdf", LAB.read_bytes())
    assert v.display_name == "passwd.pdf" and v.kind == "pdf"
    with pytest.raises(AppError) as e:
        validate_upload("evil.pdf", b"MZ not a pdf at all")
    assert e.value.status == 415
    with pytest.raises(AppError) as e:
        validate_upload("renamed.png", LAB.read_bytes())  # PDF bytes with .png extension
    assert e.value.code == "EXTENSION_MISMATCH"
    with pytest.raises(AppError) as e:
        validate_upload("empty.pdf", b"")
    assert e.value.status == 400


def test_sanitize_and_hash_paths(tmp_path):
    assert sanitize_display_name("a/b\\c<>.pdf") == "c.pdf"
    assert sanitize_display_name(None) == "unnamed"
    v = validate_upload("..\\..\\x.pdf", LAB.read_bytes())
    p = store_bytes(tmp_path, "wf_abc123", v)
    assert p.parent == (tmp_path / "wf_abc123").resolve() and p.name == f"{v.sha256}.pdf"
    with pytest.raises(ValueError):
        store_bytes(tmp_path, "../escape", v)


# ---------------------------------------------------------------- pdf
def test_pdf_analyze_and_scan():
    info = pdfmod.analyze(LAB)
    assert info.page_count == 2 and info.image_only_pages == []
    assert "Supplier ID: SUP-001" in info.texts[0]
    assert "BT-2047" in info.texts[1]
    scan = pdfmod.analyze(DEMO / "robustness" / "scan_like.pdf")
    assert scan.image_only_pages == [1]


def test_pdf_errors(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\nthis is not a valid pdf")
    with pytest.raises(pdfmod.PdfError):
        pdfmod.analyze(bad)
    import pymupdf

    enc = tmp_path / "enc.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "secret text here for the encrypted document test")
    d.save(str(enc), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    with pytest.raises(pdfmod.PdfError, match="encrypted"):
        pdfmod.analyze(enc)


def test_bbox_and_highlight_render():
    bbox = pdfmod.find_bbox(LAB, 2, "Batch ID: BT-2047", "BT-2047")
    assert bbox and bbox[2] > bbox[0] and bbox[3] > bbox[1]
    multi = pdfmod.find_bbox(LAB, 2, "Chemical concentration 12.1 ppm", 12.1)  # spans separate cells
    assert multi is not None
    assert pdfmod.find_bbox(LAB, 2, "nonexistent phrase xyz", None) is None
    assert pdfmod.find_bbox(LAB, 99, "x", None) is None
    plain = pdfmod.render_highlighted(LAB, 2, None)
    hl = pdfmod.render_highlighted(LAB, 2, bbox)
    assert plain[:8] == b"\x89PNG\r\n\x1a\n" and hl[:8] == b"\x89PNG\r\n\x1a\n"
    assert plain != hl  # highlight actually changed pixels
    assert pdfmod.page_text(LAB, 3) is None
    assert "Previous report reference: BT-2041" in pdfmod.page_text(FAILED_LAB, 1)
