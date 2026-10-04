# ChainAudit.ai: backend

Reconciles textile-supplier evidence (PDF/PNG/JPG) against configured buyer requirements, traces every finding to its source
(page, quote, highlighted bbox), has an independent **Auditor** challenge the findings, and prepares corrective action for
**human approval**. Demo Buyer Framework on Synthetic Demo Data.

## 10-minute run guide

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # LLM_PROVIDER=mock works fully offline
make demo-data                  # (re)generate the synthetic PDFs, deterministic
make test                       # 98 tests, no network needed
make run                        # http://localhost:8000/docs
```

Try it:
```bash
D=../demo_data/failed_supplier
curl -F files=@$D/lab_report_BT-2047.pdf -F files=@$D/certificate_SUP-002.pdf -F files=@$D/supplier_declaration_BT-2041.pdf localhost:8000/api/evidence/upload
curl -N localhost:8000/api/workflows/<id>/events                     # live SSE trace
curl -X POST localhost:8000/api/workflows/<id>/rerun-rules -H 'content-type: application/json' -d '{"overrides":{"CHEM_MAX":{"limit":20}}}'
curl -X POST localhost:8000/api/replays/auditor_loop_fault_injection/start   # watch the Auditor -> Extractor loop
```

Fault injection (mock only): `MOCK_FAULTS=lab_report.batch_id make run`, flagged `data.fault_injected=true` ("Fault injection (test mode)").

## Real providers
`LLM_PROVIDER` is one of `mock | groq | nvidia | gemini | openai | anthropic`. `groq`, `nvidia` and `gemini` use their OpenAI-compatible endpoints (free tiers exist). Run `pip install openai` (or `anthropic`), set `LLM_MODEL` (never hard-coded) and the matching `*_API_KEY`, then smoke-test with `python scripts/check_provider.py`. Missing key/model fails at startup with a clear message. Provider code was written against the installed SDK signatures (checked by a test), **but has not been run against a live API**: use the manual checklist below.

## Layout and contract
`docs/openapi.json` is the contract (`make openapi` regenerates it and `docs/API_CONTRACT.md`; a test fails if it is stale). See `docs/DECISIONS.md` and `docs/AGENT_CARD.md`.

## Known limitations
- **No authentication or multi-tenancy** (hackathon demo). CORS is restricted to `CORS_ORIGINS`.
- SQLite, single process. Background workflows are asyncio tasks; a crash marks running workflows `FAILED: interrupted` on next start.
- The mock provider is regex-based and only understands the generated demo layout. It cannot read images.
- Real-model behaviour (accuracy, vision extraction, structured-output quirks) is unmeasured. No accuracy number is claimed.
- Additional evidence adds documents; it cannot replace or remove one.

## Manual checklist for a live provider
1. `LLM_PROVIDER=groq|nvidia|gemini|openai|anthropic`, key + `LLM_MODEL`; run `python scripts/check_provider.py`; `GET /api/health` shows the provider and `mock:false`.
2. Upload both packs; compare findings with `demo_data/expected/expected_outcomes.json`.
3. Upload `demo_data/robustness/injection_test.pdf`: value stays 19.5, document escalates `UNVERIFIABLE_SOURCE`.
4. Upload `scan_like.pdf`: evidence is `vision_only` and escalates; nothing is auto-verified.
5. Confirm a wrong/guessed quote triggers `AUDIT_CONFLICT_FOUND` then `REEXTRACTION_REQUESTED`.
6. Confirm `rerun-rules` reports `llm_calls: 0`.
