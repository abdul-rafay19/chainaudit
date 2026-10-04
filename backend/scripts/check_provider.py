"""10-second smoke test for a real LLM provider (groq / nvidia / gemini / openai / anthropic).
Run from backend/:  python scripts/check_provider.py
Checks: (1) the key and model answer, (2) structured JSON output works, (3) quotes are copied verbatim."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.llm.factory import build_provider  # noqa: E402
from app.llm.schemas import ExtractionOutput  # noqa: E402

DOC = """TEST REPORT (synthetic demo)
Supplier ID: SUP-002
Batch ID: BT-2047
Report date: 2026-09-20
Chemical concentration 17.4 ppm
"""
SYSTEM = ("You extract fields from a supplier document. Return null for anything not in the document. "
          "Every quote MUST be copied exactly, character for character, from the document. Never guess.")


async def main() -> int:
    s = get_settings()
    p = build_provider(s)
    print(f"provider={p.name} model={p.model}")
    try:
        t = await p.generate_text(system="Reply with one word.", user_text="Say OK.", timeout=30)
        print("1. text call: OK ->", t.strip()[:40])
    except Exception as e:  # noqa: BLE001
        print("1. text call FAILED:", type(e).__name__, e)
        print("   Check the API key, LLM_MODEL spelling, and that you ran: pip install openai")
        return 1
    try:
        out = await p.generate_structured(system=SYSTEM, user_text=f"<untrusted_document>\n{DOC}\n</untrusted_document>", images=None, schema=ExtractionOutput, timeout=60)
    except Exception as e:  # noqa: BLE001
        print("2. structured JSON FAILED:", type(e).__name__, e)
        print("   Try another LLM_MODEL (pick one that supports JSON output).")
        return 1
    print("2. structured JSON: OK")
    fields = [x for x in [out.supplier_id, out.batch_id, out.document_date, *out.test_results] if x]
    bad = 0
    for f in fields:
        verbatim = bool(f.quote) and f.quote in DOC
        bad += 0 if verbatim else 1
        print(f"   {f.field}={f.value} page={f.page} verbatim_quote={'yes' if verbatim else 'NO'}")
    chem = next((x for x in out.test_results if x.value in (17.4, "17.4")), None)
    print("3. chemical 17.4 found:", "yes" if chem else "NO")
    print("RESULT:", "PASS" if chem and bad == 0 else f"CHECK ({bad} non-verbatim quotes)")
    return 0 if chem and bad == 0 else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
