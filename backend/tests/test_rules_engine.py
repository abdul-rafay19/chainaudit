import copy
from datetime import date

import pytest

from app.enums import ComplianceStatus as CS
from app.enums import EscalationReason as ER
from app.enums import Verification
from app.rules.engine import EvidenceBundle, IntakeIssue, evaluate
from app.rules.loader import RuleSetError, apply_overrides, base_id, derive_id, load_yaml, parse_content, to_content
from tests.helpers import cert, decl, lab

RS = load_yaml()


def run(*docs, intake=None, rs=RS):
    return {r.rule_id: r for r in evaluate(rs, EvidenceBundle(list(docs), intake or []))}


def clean_pack():
    return [lab(), cert(), decl()]


# ---------------------------------------------------------------- loader
def test_yaml_loads_and_labels():
    assert RS.tag == "Demo Buyer Framework v1.0"
    assert "Not the requirements of any real buyer" in RS.label
    assert [r.id for r in RS.rules] == ["CHEM_MAX", "CERT_REQUIRED", "DOC_FRESH", "ID_CONSISTENCY"]
    assert RS.reference_date == date(2026, 9, 30)


def test_loader_rejects_bad_rulesets():
    c = to_content(RS)
    bad = copy.deepcopy(c)
    bad["rules"][0]["type"] = "telepathy"
    with pytest.raises(RuleSetError):
        parse_content(bad)
    bad = copy.deepcopy(c)
    del bad["rules"][0]["limit"]
    with pytest.raises(RuleSetError, match="limit"):
        parse_content(bad)
    bad = copy.deepcopy(c)
    bad["rules"].append(copy.deepcopy(bad["rules"][0]))
    with pytest.raises(RuleSetError, match="unique"):
        parse_content(bad)
    bad = copy.deepcopy(c)
    bad["surprise"] = 1
    with pytest.raises(RuleSetError):
        parse_content(bad)


def test_overrides_create_derived_set_not_mutation():
    before = to_content(RS)
    d = apply_overrides(RS, {"CHEM_MAX": {"limit": 20}})
    assert to_content(RS) == before  # base untouched
    assert d.version == "1.0+override" and d.rules[0].limit == 20 and "CHEM_MAX.limit=20" in d.label
    assert derive_id(base_id(RS), to_content(d)).startswith("demo_buyer_framework@1.0+ovr-")
    for bad in ({"NOPE": {"limit": 1}}, {"CHEM_MAX": {"type": "x"}}, {"CHEM_MAX": {"colour": 1}}):
        with pytest.raises(RuleSetError):
            apply_overrides(RS, bad)
    with pytest.raises(RuleSetError):
        apply_overrides(RS, {"CHEM_MAX": {"limit": "not-a-number"}})


# ---------------------------------------------------------------- clean pack
def test_clean_pack_all_pass_with_templated_reasons():
    r = run(*clean_pack())
    assert {k: v.status for k, v in r.items()} == {k: CS.PASSING_CONFIGURED_CHECK for k in ("CHEM_MAX", "CERT_REQUIRED", "DOC_FRESH", "ID_CONSISTENCY")}
    assert r["CHEM_MAX"].reason == "12.1 ppm is within configured maximum of 15 ppm (rule CHEM_MAX, Demo Buyer Framework v1.0)"
    assert all(v.evidence for v in r.values())


def test_engine_is_deterministic_and_pure():
    a = [x.model_dump() if hasattr(x, "model_dump") else x for x in evaluate(RS, EvidenceBundle(clean_pack()))]
    b = [x.model_dump() if hasattr(x, "model_dump") else x for x in evaluate(RS, EvidenceBundle(clean_pack()))]
    assert a == b


# ---------------------------------------------------------------- CHEM_MAX
@pytest.mark.parametrize(
    "ppm,expected",
    [
        (15.0, CS.PASSING_CONFIGURED_CHECK),
        (14.99, CS.PASSING_CONFIGURED_CHECK),
        (15.01, CS.REQUIREMENT_NOT_SATISFIED),
        (17.4, CS.REQUIREMENT_NOT_SATISFIED),
        (0, CS.PASSING_CONFIGURED_CHECK),
    ],
)
def test_chem_boundaries(ppm, expected):
    assert run(lab(ppm=ppm), cert(), decl())["CHEM_MAX"].status == expected


def test_chem_fail_reason_text():
    assert run(lab(ppm=17.4), cert(), decl())["CHEM_MAX"].reason == "17.4 ppm exceeds configured maximum of 15 ppm (rule CHEM_MAX, Demo Buyer Framework v1.0)"


def test_chem_decimal_exactness():  # float traps: 0.1+0.2 style values must not misjudge the limit
    assert run(lab(ppm=15.000000001), cert(), decl())["CHEM_MAX"].status == CS.REQUIREMENT_NOT_SATISFIED
    assert run(lab(ppm="15.00"), cert(), decl())["CHEM_MAX"].status == CS.PASSING_CONFIGURED_CHECK


def test_chem_unit_mismatch_missing_unit_missing_field():
    r = run(lab(unit="mg/kg"), cert(), decl())["CHEM_MAX"]
    assert r.status == CS.HUMAN_REVIEW_REQUIRED and "unit mismatch" in r.reason
    r = run(lab(unit=""), cert(), decl())["CHEM_MAX"]
    assert r.status == CS.HUMAN_REVIEW_REQUIRED and r.escalation_reason == ER.MISSING_REQUIRED_FIELD
    r = run(lab(ppm=None), cert(), decl())["CHEM_MAX"]
    assert r.status == CS.HUMAN_REVIEW_REQUIRED and r.escalation_reason == ER.MISSING_REQUIRED_FIELD and r.evidence
    r = run(lab(ppm="n/a"), cert(), decl())["CHEM_MAX"]
    assert r.status == CS.HUMAN_REVIEW_REQUIRED


def test_low_confidence_escalates_even_if_value_fails_or_passes():
    for ppm in (17.4, 12.1):
        r = run(lab(ppm=ppm, conf=0.79), cert(), decl())["CHEM_MAX"]
        assert r.status == CS.HUMAN_REVIEW_REQUIRED and r.escalation_reason == ER.LOW_EXTRACTION_CONFIDENCE
    assert run(lab(ppm=12.1, conf=0.80), cert(), decl())["CHEM_MAX"].status == CS.PASSING_CONFIGURED_CHECK


def test_vision_only_and_injection_never_auto_decided():
    r = run(lab(ver=Verification.vision_only, conf=0.75), cert(), decl())["CHEM_MAX"]
    assert r.escalation_reason == ER.UNVERIFIABLE_SOURCE
    r = run(lab(ppm=19.5, notes=["possible_prompt_injection"]), cert(), decl())["CHEM_MAX"]
    assert r.status == CS.HUMAN_REVIEW_REQUIRED and r.escalation_reason == ER.UNVERIFIABLE_SOURCE


# ---------------------------------------------------------------- CERT_REQUIRED
def test_cert_rules():
    assert run(lab(), cert(valid_until="2026-09-30"), decl())["CERT_REQUIRED"].status == CS.PASSING_CONFIGURED_CHECK  # on reference date
    r = run(lab(), cert(valid_until="2026-09-29"), decl())["CERT_REQUIRED"]
    assert r.status == CS.REQUIREMENT_NOT_SATISFIED and "expired on 2026-09-29" in r.reason
    r = run(lab(), decl())["CERT_REQUIRED"]  # no certificate at all
    assert r.status == CS.REQUIREMENT_NOT_SATISFIED and r.evidence and all(e.value is None for e in r.evidence)
    r = run(lab(), cert(cert_name="OTHER_CERT"), decl())["CERT_REQUIRED"]
    assert r.status == CS.REQUIREMENT_NOT_SATISFIED
    r = run(lab(), cert(valid_until=None), decl())["CERT_REQUIRED"]
    assert r.status == CS.HUMAN_REVIEW_REQUIRED and r.escalation_reason == ER.MISSING_REQUIRED_FIELD
    r = run(lab(), cert(valid_until="31/03/2027"), decl())["CERT_REQUIRED"]
    assert r.status == CS.HUMAN_REVIEW_REQUIRED
    r = run(lab(), cert(cert_name=" demo_cert_chem_l2 "), decl())["CERT_REQUIRED"]  # normalised name match
    assert r.status == CS.PASSING_CONFIGURED_CHECK
    r = run(lab(), cert(valid_until="2020-01-01"), cert(doc="c2", name="c2.pdf"), decl())["CERT_REQUIRED"]
    assert r.status == CS.PASSING_CONFIGURED_CHECK  # one valid certificate is enough


# ---------------------------------------------------------------- DOC_FRESH
@pytest.mark.parametrize(
    "d,status",
    [
        ("2026-09-18", CS.PASSING_CONFIGURED_CHECK),
        ("2026-08-01", CS.PASSING_CONFIGURED_CHECK),
        ("2026-07-31", CS.REQUIREMENT_NOT_SATISFIED),
        ("2026-09-30", CS.PASSING_CONFIGURED_CHECK),
        ("2026-10-01", CS.HUMAN_REVIEW_REQUIRED),
        ("18 Sept", CS.HUMAN_REVIEW_REQUIRED),
    ],
)
def test_doc_fresh_boundaries(d, status):
    # 2026-08-01 is exactly 60 days before 2026-09-30 -> pass; 2026-07-31 is 61 -> fail
    assert run(lab(date=d), cert(), decl())["DOC_FRESH"].status == status


def test_doc_fresh_missing_date_and_no_lab():
    r = run(lab(date=None), cert(), decl())["DOC_FRESH"]
    assert r.status == CS.HUMAN_REVIEW_REQUIRED and r.escalation_reason == ER.MISSING_REQUIRED_FIELD
    r = run(cert(), decl())["DOC_FRESH"]
    assert r.status == CS.HUMAN_REVIEW_REQUIRED and r.escalation_reason == ER.MISSING_REQUIRED_FIELD
    assert run(lab(date="2026-10-01"), cert(), decl())["DOC_FRESH"].escalation_reason == ER.CONFLICTING_VALUES  # future-dated


def test_declaration_age_is_not_checked():  # document_types: [lab_report] only
    assert run(lab(), cert(), decl(date="2020-01-01"))["DOC_FRESH"].status == CS.PASSING_CONFIGURED_CHECK


# ---------------------------------------------------------------- ID_CONSISTENCY
def test_consistency_conflict_is_review_never_fraud():
    r = run(lab(supplier="SUP-002"), cert(supplier="SUP-002"), decl(supplier="SUP-002", batch="BT-2041"))["ID_CONSISTENCY"]
    assert r.status == CS.HUMAN_REVIEW_REQUIRED and r.escalation_reason == ER.CONFLICTING_VALUES
    assert "batch_id differs across documents" in r.reason and "BT-2047" in r.reason and "BT-2041" in r.reason
    assert "fraud" not in r.reason.lower()
    assert {e.value for e in r.evidence if e.field == "batch_id"} == {"BT-2047", "BT-2041"}


def test_consistency_normalisation_and_supplier_conflict():
    assert run(lab(batch="bt-2047"), cert(), decl(batch=" BT-2047 "))["ID_CONSISTENCY"].status == CS.PASSING_CONFIGURED_CHECK
    assert run(lab(batch="BT 2047"), cert(), decl(batch="BT2047"))["ID_CONSISTENCY"].status == CS.PASSING_CONFIGURED_CHECK
    r = run(lab(supplier="SUP-001"), cert(supplier="SUP-009"), decl())["ID_CONSISTENCY"]
    assert r.status == CS.HUMAN_REVIEW_REQUIRED and "supplier_id differs" in r.reason


def test_consistency_missing_all_values():
    r = run(lab(supplier=None, batch=None), decl(supplier=None, batch=None), cert(supplier=None))["ID_CONSISTENCY"]
    assert r.status == CS.HUMAN_REVIEW_REQUIRED and r.escalation_reason == ER.MISSING_REQUIRED_FIELD


# ---------------------------------------------------------------- intake + overrides
def test_intake_issues_become_review_results():
    issues = [
        IntakeIssue("d9", "broken.pdf", "extraction_failed", "encrypted"),
        IntakeIssue("d8", "mystery.pdf", "unknown_type"),
        IntakeIssue("d7", "scan.pdf", "image_only", "pages 1"),
        IntakeIssue("d6", "inj.pdf", "prompt_injection"),
    ]
    r = run(*clean_pack(), intake=issues)
    assert r["DOC_INTAKE:d9"].escalation_reason == ER.EXTRACTION_FAILED
    assert r["DOC_INTAKE:d8"].escalation_reason == ER.UNKNOWN_DOCUMENT_TYPE
    assert r["DOC_INTAKE:d7"].escalation_reason == ER.UNVERIFIABLE_SOURCE
    assert r["DOC_INTAKE:d6"].escalation_reason == ER.UNVERIFIABLE_SOURCE
    assert all(v.status == CS.HUMAN_REVIEW_REQUIRED for k, v in r.items() if k.startswith("DOC_INTAKE"))


def test_override_flips_chem_only():
    pack = [lab(supplier="SUP-002", ppm=17.4), cert(supplier="SUP-002"), decl(supplier="SUP-002", batch="BT-2041")]
    before = run(*pack)
    after = run(*pack, rs=apply_overrides(RS, {"CHEM_MAX": {"limit": 20}}))
    assert before["CHEM_MAX"].status == CS.REQUIREMENT_NOT_SATISFIED
    assert after["CHEM_MAX"].status == CS.PASSING_CONFIGURED_CHECK
    assert after["ID_CONSISTENCY"].status == before["ID_CONSISTENCY"].status == CS.HUMAN_REVIEW_REQUIRED
    assert "20 ppm" in after["CHEM_MAX"].reason and "1.0+override" in after["CHEM_MAX"].reason
