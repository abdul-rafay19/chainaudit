import asyncio

from app.agents.extraction.classifier import by_filename, by_text, classify
from app.agents.extraction.extractor import ExtractionAgent
from app.enums import DocType, EventType, Verification
from app.llm.mock_provider import MockProvider
from app.llm.schemas import ExtractionOutput, LLMField
from app.schemas.refs import iter_refs
from tests.conftest import DEMO


def agent(env, provider=None):
    return ExtractionAgent(provider or env.provider, env.repo, env.bus, env.settings)


async def run_docs(env, ag, paths, wid=None):
    wid = wid or env.workflow()
    dids = [env.add_doc(wid, p) for p in paths]
    sem = asyncio.Semaphore(4)
    docs = await asyncio.gather(*(ag.process(wid, d, sem) for d in dids))
    return wid, dids, docs


def test_classifier_rules():
    assert by_filename("certificate_SUP-001.pdf") == DocType.certificate
    assert by_filename("supplier_declaration_BT-2047.pdf") == DocType.supplier_declaration
    assert by_filename("lab_report_BT-2047.pdf") == DocType.lab_report
    assert by_filename("scan0001.pdf") is None
    assert by_text("CERTIFICATE OF CONFORMITY (DEMO)") == DocType.certificate


async def test_classifier_content_beats_filename_and_llm_fallback():
    p = MockProvider()
    t, how = await classify(filename="lab_report.pdf", first_page_text="CERTIFICATE OF CONFORMITY", provider=p, timeout=5)
    assert t == DocType.certificate and how == "page_keywords_overrode_filename"
    t, how = await classify(filename="x.pdf", first_page_text="nothing recognisable here at all", provider=p, timeout=5)
    assert t == DocType.unknown and how == "unclassified"


async def test_extract_clean_pack_fields_are_complete_and_sourced(env):
    wid, dids, docs = await run_docs(
        env, agent(env), [DEMO / "clean_supplier" / n for n in ("lab_report_BT-2047.pdf", "certificate_SUP-001.pdf", "supplier_declaration_BT-2047.pdf")]
    )
    lab, cert, decl = docs
    assert lab.doc_type == DocType.lab_report and cert.doc_type == DocType.certificate and decl.doc_type == DocType.supplier_declaration
    assert lab.supplier_id.value == "SUP-001" and lab.supplier_id.page == 1
    assert lab.batch_id.value == "BT-2047" and lab.batch_id.page == 2
    assert lab.document_date.value == "2026-09-18"
    t = lab.test_results[0]
    assert t.field == "chemical_ppm" and t.value == 12.1 and t.unit == "ppm" and t.page == 2
    c = cert.certifications[0]
    assert c.name.value == "DEMO_CERT_CHEM_L2" and c.valid_until.value == "2027-03-31" and c.issued_date.value == "2026-03-31"
    assert decl.batch_id.value == "BT-2047" and decl.document_date.value == "2026-09-20"
    for _, e in [r for d in docs for r in iter_refs(d)]:  # N3: every value carries source/page/quote/confidence
        assert e.source_document and e.document_id and e.page and e.quote and 0 < e.confidence <= 1
        assert e.verification == Verification.unverified
    events = env.bus.history_sync(wid)
    assert [e.type for e in events].count(EventType.EXTRACTION_STARTED) == 3
    assert [e.type for e in events].count(EventType.EXTRACTION_COMPLETED) == 3
    assert all(e.document_id for e in events)


async def test_decoy_is_not_picked_by_mock(env):
    _, _, (lab, *_rest) = await run_docs(env, agent(env), [DEMO / "failed_supplier" / "lab_report_BT-2047.pdf"])
    assert lab.batch_id.value == "BT-2047" and lab.test_results[0].value == 17.4


async def test_second_run_is_cached_and_makes_no_llm_calls(env):
    ag = agent(env)
    path = DEMO / "clean_supplier" / "lab_report_BT-2047.pdf"
    await run_docs(env, ag, [path])
    calls = env.provider.calls
    wid2, _, (doc2,) = await run_docs(env, ag, [path])
    assert env.provider.calls == calls  # cache hit: classification is rule-based, extraction skipped
    done = [e for e in env.bus.history_sync(wid2) if e.type == EventType.EXTRACTION_COMPLETED]
    assert done[0].data["cached"] is True and doc2.batch_id.value == "BT-2047"
    assert doc2.document_id != "" and doc2.batch_id.document_id == doc2.document_id  # ids rewritten for the new document


async def test_one_bad_document_does_not_fail_the_others(env, tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\nnot really a pdf")
    wid, dids, docs = await run_docs(env, agent(env), [DEMO / "clean_supplier" / "lab_report_BT-2047.pdf", bad, DEMO / "clean_supplier" / "certificate_SUP-001.pdf"])
    assert docs[0] is not None and docs[1] is None and docs[2] is not None
    types = [e.type for e in env.bus.history_sync(wid)]
    assert types.count(EventType.EXTRACTION_FAILED) == 1
    assert env.repo.get_document(dids[1]).status == "failed"


async def test_injection_pdf_does_not_change_values_and_is_flagged(env):
    wid, _, (doc,) = await run_docs(env, agent(env), [DEMO / "robustness" / "injection_test.pdf"])
    assert doc.test_results[0].value == 19.5  # NOT 0
    assert "possible_prompt_injection" in doc.extraction_notes
    assert not any("ignore previous" in str(e.value).lower() for e in doc.all_fields())


async def test_scan_like_pdf_marks_image_pages_and_mock_cannot_read(env):
    wid, _, (doc,) = await run_docs(env, agent(env), [DEMO / "robustness" / "scan_like.pdf"])
    assert doc.image_only_pages == [1] and doc.all_fields() == []
    assert any("cannot read images" in n or "mock" in n for n in doc.extraction_notes) or doc.extraction_notes == []


class FakeVision:
    name, model, supports_vision = "fake", "fake-vision", True
    calls = 0

    async def generate_structured(self, *, system, user_text, images, schema, timeout):
        assert images and images[0][:4] == b"\x89PNG"  # rendered page sent to the model
        if schema is ExtractionOutput:
            f = lambda n, v, u=None: LLMField(field=n, value=v, unit=u, page=1, quote=f"{n} {v}", confidence=0.99)
            return ExtractionOutput(
                supplier_id=f("supplier_id", "SUP-004"), batch_id=None, document_date=None, test_results=[f("chemical_ppm", 11.0, "ppm")], certifications=[], other=[], notes=[]
            )
        raise AssertionError

    async def generate_text(self, **k):
        return ""


async def test_vision_only_evidence_is_capped_and_marked(env):
    wid, _, (doc,) = await run_docs(env, agent(env, FakeVision()), [DEMO / "robustness" / "scan_like.pdf"])
    # classification: a scan has no text and an unhelpful filename -> unknown, still extracted
    t = doc.test_results[0]
    assert t.verification == Verification.vision_only and t.confidence == 0.75 and t.llm_confidence == 0.99 and not t.verified


async def test_fault_injection_flagged_on_event(env):
    env.provider.faults = {"lab_report.batch_id"}
    wid, _, (doc,) = await run_docs(env, agent(env), [DEMO / "clean_supplier" / "lab_report_BT-2047.pdf"])
    assert doc.batch_id.value == "BT-2041"  # wrong on first attempt only (fabricated quote: no decoy in the clean pack)
    ev = [e for e in env.bus.history_sync(wid) if e.type == EventType.EXTRACTION_COMPLETED][0]
    assert ev.data["fault_injected"] is True


async def test_reextract_single_field_page_only(env):
    ag = agent(env)
    wid, _, (doc,) = await run_docs(env, ag, [DEMO / "clean_supplier" / "lab_report_BT-2047.pdf"])
    new = await ag.reextract(doc, "batch_id", pages=[2], objection="quote not found on page 2")
    assert new.value == "BT-2047" and new.page == 2 and new.document_id == doc.document_id
    none = await ag.reextract(doc, "batch_id", pages=[1], objection="x")  # page 1 has no batch id
    assert none is None


async def test_provider_failure_is_isolated(env):
    class Boom(MockProvider):
        async def generate_structured(self, **k):
            from app.llm.base import LLMError

            raise LLMError("down")

    wid, _, (doc,) = await run_docs(env, agent(env, Boom()), [DEMO / "clean_supplier" / "lab_report_BT-2047.pdf"])
    assert doc is None
    assert [e.type for e in env.bus.history_sync(wid)][-1] == EventType.EXTRACTION_FAILED
