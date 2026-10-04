"""Internal synthetic evaluation: runs packs with KNOWN planted flaws and counts outcomes.

Labelled "Internal synthetic evaluation, not production accuracy". Uses the offline mock provider unless configured otherwise.
Numbers are computed from real runs here, never typed in.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["CHAINAUDIT_NO_AUTOAPP"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import Settings  # noqa: E402
from app.container import Container  # noqa: E402
from app.core.storage import validate_upload  # noqa: E402
from generate_demo_data import certificate, declaration, lab_report  # noqa: E402

PASS, FAIL, REV = "PASSING_CONFIGURED_CHECK", "REQUIREMENT_NOT_SATISFIED", "HUMAN_REVIEW_REQUIRED"


def pack(tmp: Path, name: str, *, ppm="12.1", date="2026-09-18", valid_until="2027-03-31", decl_batch="BT-2047", with_cert=True):
    d = tmp / name
    d.mkdir(exist_ok=True)
    lab_report(d / "lab_report_BT-2047.pdf", supplier="SUP-009", supplier_name="Eval Mills", batch="BT-2047", ppm=ppm, date=date)
    if with_cert:
        certificate(d / "certificate_SUP-009.pdf", supplier="SUP-009", issued="2026-01-01", valid_until=valid_until)
    declaration(d / f"supplier_declaration_{decl_batch}.pdf", supplier="SUP-009", batch=decl_batch, date="2026-09-20")
    return sorted(d.glob("*.pdf"))


# (name, kwargs, rule that should be flagged, expected status)
CASES = [
    ("control_clean", {}, None, None),
    ("chem_over_limit", {"ppm": "15.01"}, "CHEM_MAX", FAIL),
    ("chem_at_limit_ok", {"ppm": "15.0"}, None, None),
    ("stale_report", {"date": "2026-07-01"}, "DOC_FRESH", FAIL),
    ("expired_cert", {"valid_until": "2026-09-29"}, "CERT_REQUIRED", FAIL),
    ("missing_cert", {"with_cert": False}, "CERT_REQUIRED", FAIL),
    ("batch_mismatch", {"decl_batch": "BT-2041"}, "ID_CONSISTENCY", REV),
    ("future_dated_report", {"date": "2026-10-05"}, "DOC_FRESH", REV),
]


async def main() -> None:
    rows = []
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for name, kw, rule, expected in CASES:
            s = Settings(_env_file=None, database_url=f"sqlite:///{tmp}/{name}.db", upload_dir=tmp / name / "up", render_dir=tmp / name / "rn", llm_provider="mock")
            c = Container(s)
            c.startup()
            wid, _ = await c.orchestrator.create_from_uploads([validate_upload(p.name, p.read_bytes()) for p in pack(tmp, name, **kw)], None)
            await c.orchestrator.wait_idle()
            f = {x.rule_id: x for x in c.repo.findings(wid, include_superseded=False)}
            wrong = [r for r, x in f.items() if (x.compliance_status.value != (expected if r == rule else PASS))]
            to_review = sum(x.requires_human_review for x in f.values())
            rows.append((name, rule or "(none planted)", expected or "all pass", "DETECTED" if rule and not wrong else ("CLEAN" if not rule and not wrong else "MISSED/WRONG"), to_review))
            c.db.dispose()
    print("Internal synthetic evaluation, not production accuracy\n")
    print(f"{'case':22}{'planted flaw':18}{'expected':28}{'result':14}{'sent to review'}")
    for r in rows:
        print(f"{r[0]:22}{r[1]:18}{r[2]:28}{r[3]:14}{r[4]}")
    planted = [r for r in rows if r[1] != "(none planted)"]
    print(f"\nplanted flaws detected: {sum(r[3] == 'DETECTED' for r in planted)}/{len(planted)}; controls clean: {sum(r[3] == 'CLEAN' for r in rows)}/{len(rows) - len(planted)}")


if __name__ == "__main__":
    asyncio.run(main())
