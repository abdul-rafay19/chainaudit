# ChainAudit.ai: Complete Context Handover

Read this first. It tells a new person (or a fresh Claude session) what exists, what is verified, what is NOT done, what to do next, and exactly how to build the frontend against the real backend.

Delivered with it: `chainaudit-backend.zip` (backend + demo data + docs + recordings). **The frontend is not built yet.** Section 7 is the complete build spec for it.

---

## 1. Status at a glance

| Area | State |
|---|---|
| Backend (Part A of the master prompt, Phases 0 to 9) | **Built and tested.** 98 tests green, ruff clean, mypy clean (54 files) |
| Demo data + expected outcomes + 4 recorded replays | Done, deterministic, committed |
| OpenAPI contract (`docs/openapi.json`) | Done, a test fails if it goes stale |
| Real LLM provider (Anthropic/OpenAI) | **Written, never run live.** Needs your API key and the checklist in section 5 |
| Frontend (Part B) | **Not started.** Full spec in section 7 |
| Deployment | **Not done** (no Dockerfile, no hosting) |
| Rehearsed demo | **Not done**, script is in section 8 |

Honest summary: the backend logic is solid and proven on the offline mock provider. The two biggest unknowns are (a) how a real model behaves in the extraction/audit prompts and (b) the whole frontend.

---

## 2. What is done (verified by tests)

Non-negotiables from the spec and how they are enforced:

| Rule | Enforcement |
|---|---|
| N1 deterministic compliance | `app/rules/engine.py` is pure (no I/O, no LLM, no clock). A test greps it for clock/LLM/network use |
| N2 Auditor cannot change a verdict | `FindingAuditor` exposes only `add_flag / mark_verified / mark_uncertain / request_reextraction`, uses `__slots__` and blocks other writes. Test also greps that only `agents/compliance/evaluator.py::apply_result` decides status |
| N3 evidence carries source/page/quote/confidence | Every value has them; unsupported values stay `null` (test over 6 different packs) |
| N4 nothing dispatched without approval | `POST /findings/{id}/dispatch` returns 409 `DISPATCH_NOT_APPROVED` unless decision is `approved` or `edited`. Demo outbox only, nothing is ever sent |
| N5 one append-only event table | SQLite triggers + ORM guard block UPDATE/DELETE (tested). Live trace, audit record and replay all read from it |
| N6 documents are untrusted | `<untrusted_document>` wrapping, schema-validated output, injection phrases escalate to review (tested with `injection_test.pdf`) |
| N7 labels | "Synthetic Demo Data", "Demo Buyer Framework", no "fraud"/"certified" anywhere (tested over API output) |
| N8 no vendor lock | Everything goes through the `LLMProvider` interface (mock, anthropic, openai) |

Behaviours proven end to end:
- Clean pack: 4 passing, all verified, workflow `COMPLETED`.
- Failed pack: `CHEM_MAX` not satisfied (17.4 > 15) + EN/Roman Urdu draft; `ID_CONSISTENCY` human review `CONFLICTING_VALUES` (BT-2047 vs BT-2041, genuine, **no re-extraction**); `AWAITING_REVIEW`.
- Auditor loop Case A: wrong value, quote not found, re-read that page, corrected, rules re-evaluated, finding flips back (`MOCK_FAULTS=lab_report.batch_id`).
- Decoy trap: "Previous report reference: BT-2041" is caught as `EXTRACTOR_GUESS_SUSPECTED`, whole document re-read, real value recovered.
- Re-extraction limit (2) escalates `REEXTRACTION_LIMIT`.
- Semantic check can make a finding `uncertain` but never changes its status.
- Rerun rules (`CHEM_MAX` 15 to 20): flips with `llm_calls == 0`, derived rule set is content-addressed and idempotent, base rule set untouched.
- SSE: late join, resume with `Last-Event-ID`, live fan-out with no gaps/duplicates, subscriber cleanup (tested against a real uvicorn server).
- Robustness: corrupt/encrypted/zero-page files fail alone; scan-only pages escalate `UNVERIFIABLE_SOURCE`; oversize 413, wrong type 415, extension mismatch 415, too many files 413; startup marks interrupted workflows `FAILED: interrupted`; workflow timeout becomes `FAILED`.

---

## 3. What is REMAINING (prioritised)

### P0: before you can demo
1. **Frontend**: nothing exists. Spec in section 7. It can start today using the recorded replays as fixtures (no waiting on anything).
2. **Live provider run**: the Anthropic/OpenAI code has never touched a real API. Run the checklist in section 5. Expect to tune prompts/`LLM_MODEL`; bump `EXTRACTOR_VERSION` in `app/agents/extraction/prompts.py` whenever you change a prompt (it is part of the cache key).
3. **Rehearse the demo** (section 8) and keep a mock/replay fallback ready in case Wi-Fi or the API key fails.

### P1: strongly recommended
4. **Deploy**: no Dockerfile or hosting config. Simplest: one small VM or Railway/Render for the backend (persistent disk for `data/` and the SQLite file), Vercel for the frontend, set `CORS_ORIGINS` to the frontend URL. Single process only (SQLite + in-process event fan-out).
5. **A few small backend additions the frontend would like** (none are required):
   - an endpoint to serve the original uploaded document (today only `evidence-image` renders pages);
   - `GET /api/suppliers`-style aggregation (today the Suppliers page must derive from `/api/workflows` + `/api/audit/{id}`);
   - if you change the API, run `make openapi` and tell the frontend.
6. **SSE heartbeat is configured (`ping=15`) but not covered by an automated test** (a 15 s wait). Verify manually once.

### P2: known limitations (say them out loud if asked)
- No authentication or multi-tenancy (stated in README).
- Additional evidence can only add documents, not replace one (an older conflicting document still counts).
- The mock provider only understands the generated demo layout and cannot read images; real vision extraction is untested.
- No accuracy claim exists. `scripts/evaluate.py` prints counts from planted synthetic flaws on the mock extractor (6/6 flaws detected, 2/2 controls clean). It is labelled "Internal synthetic evaluation, not production accuracy". Do not present it as accuracy.
- A one-character-off ID quote is a fuzzy match: flagged, capped at 0.70 confidence, never `verified`, but the Auditor must catch it.
- SQLite, single process, background work in asyncio tasks.

### Deviations from the master prompt (all one-liners in `docs/DECISIONS.md`)
Extra tables (`extraction_cache`, `semantic_cache`); `DOC_INTAKE:<doc_id>` findings for failed/unknown/scan/injection documents; content beats filename when classifying; later re-extraction attempts read the whole document; robustness PDFs are in `demo_data/robustness/`; escalation reasons for cases the locked enum does not name (unit mismatch to `CONFLICTING_VALUES`, injection/image-only to `UNVERIFIABLE_SOURCE`); reruns use cache-only semantic checks and reuse drafts so they truly make zero model calls.

---

## 4. What YOU do now (suggested order)

1. **Unzip and verify (10 min)**
   ```bash
   unzip chainaudit-backend.zip && cd chainaudit/backend
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   make test        # expect 98 passed
   make run         # http://localhost:8000/docs
   ```
2. **Hand the frontend to Fizza (or me)** with this file + `docs/openapi.json` + `demo_data/replays/*.json`. Frontend can start immediately.
3. **Live provider check (30 to 60 min)**: `pip install anthropic` (or `openai`), set `LLM_PROVIDER`, `LLM_MODEL`, key in `.env`, restart, run section 5.
4. **Commit everything to git** (a `.gitignore` is included). Commit `docs/openapi.json` and `demo_data/replays/`.
5. **Deploy** (P1.4), then point the frontend `NEXT_PUBLIC_API_URL` at it.
6. **Rehearse the 3-minute demo** (section 8) twice, once with the real provider and once on mock/replay.
7. Re-run `make test && make openapi` after any backend change.

Make targets: `make test | run | demo-data | openapi | lint`. Other scripts: `python scripts/export_run.py --record-demo` (re-record replays), `python scripts/evaluate.py`.

---

## 5. Live-provider checklist

1. `.env`: `LLM_PROVIDER=anthropic|openai`, `LLM_MODEL=<model you have access to>`, the key. `GET /api/health` must show your provider and `mock:false`. A missing key/model fails at startup with a clear message.
2. Upload the clean pack, then the failed pack; compare with `demo_data/expected/expected_outcomes.json`.
3. Upload `demo_data/robustness/injection_test.pdf`: value must stay `19.5` and the document escalates `UNVERIFIABLE_SOURCE`.
4. Upload `scan_like.pdf`: evidence should be `vision_only` and escalate; nothing auto-verified.
5. Watch the event stream for a real extractor mistake; confirm `AUDIT_CONFLICT_FOUND` then `REEXTRACTION_REQUESTED` appear. (The mock fault `MOCK_FAULTS` is mock-only.)
6. Run `rerun-rules` and confirm `llm_calls: 0`.
7. If a real model returns quotes with different whitespace/hyphenation, that is fine (normalised matching). If it returns paraphrased quotes you will see `QUOTE_NOT_FOUND`/fuzzy flags and re-extractions: tighten the prompt in `prompts.py` and bump `EXTRACTOR_VERSION`.

---

## 6. Backend context (how it works)

### 6.1 Pipeline
```
upload -> validate (magic bytes, size, count) -> store (sha256 path) -> WORKFLOW_CREATED, DOCUMENT_RECEIVED
EXTRACTING   one extractor task per document (asyncio.gather + semaphore); classify; cache; LLM; flag injection
EVALUATING   rules engine -> ONE finding per rule (+ DOC_INTAKE findings)        -> FINDING_CREATED
AUDITING     verify every evidence field vs PDF text (loop A), re-evaluate, cross-doc checks (case B),
             cert validity, absence checks, semantic check, outcome mapping       -> AUDIT_* / HUMAN_REVIEW_REQUIRED
             corrective drafts for verified non-passing findings (+ genuine conflicts)
AWAITING_REVIEW | COMPLETED   human review -> COMPLETED when every required review is decided
```
Statuses: `CREATED, EXTRACTING, EVALUATING, AUDITING, AWAITING_REVIEW, AWAITING_EVIDENCE, COMPLETED, FAILED`. Transitions are validated in one function (`core/store.py::validate_transition`).

### 6.2 Finding outcome mapping (Auditor)
| Compliance | Audit | Result |
|---|---|---|
| PASSING_CONFIGURED_CHECK | verified | no review needed |
| REQUIREMENT_NOT_SATISFIED | verified | draft + review |
| HUMAN_REVIEW_REQUIRED | any | review, escalation reason shown |
| any | uncertain | review, escalation reason shown |

### 6.3 Rules (`rules/rule_sets/demo_buyer_framework_v1.yaml`, reference date 2026-09-30)
`CHEM_MAX` (chemical_ppm <= 15 ppm), `CERT_REQUIRED` (DEMO_CERT_CHEM_L2 valid on reference date), `DOC_FRESH` (lab report <= 60 days old), `ID_CONSISTENCY` (supplier_id and batch_id consistent across documents). Min extraction confidence 0.80. Rule-set row ids look like `demo_buyer_framework@1.0`; derived ones `demo_buyer_framework@1.0+ovr-<hash>`.

### 6.4 Confidence
`confidence = min(llm_confidence, 1.0, cap)`; caps `not_found` 0.40, `fuzzy` 0.70, `vision_only` 0.75, suspected guess 0.40. It is a heuristic, never a calibrated probability: do not label it "probability" in the UI.

### 6.5 Code map
```
backend/app/
  main.py container.py config.py db.py enums.py replay.py
  api/        routes.py events.py health.py builders.py deps.py
  core/       events.py(EventBus) store.py(Repo) pdf.py textnorm.py storage.py errors.py logging.py
  llm/        base.py mock_provider.py anthropic_provider.py openai_provider.py counting.py schemas.py factory.py
  agents/     extraction/ compliance/ auditor/(checks, state_machine, semantic, auditor) corrective/
  rules/      engine.py models.py loader.py rule_sets/*.yaml
  orchestrator/workflow.py   (lifecycle, review, dispatch, rerun)
backend/scripts/ generate_demo_data.py export_openapi.py export_run.py evaluate.py
backend/tests/   98 tests
demo_data/       clean_supplier/ failed_supplier/ robustness/ expected/ replays/
docs/            openapi.json API_CONTRACT.md DECISIONS.md AGENT_CARD.md HANDOVER.md
```

### 6.6 Config (`.env`)
`LLM_PROVIDER` (mock|anthropic|openai), `LLM_MODEL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DATABASE_URL`, `UPLOAD_DIR`, `RENDER_DIR`, `CORS_ORIGINS`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `EXTRACTION_CONCURRENCY`, `MAX_UPLOAD_MB`, `MAX_FILES_PER_UPLOAD`, `MAX_REEXTRACTIONS_PER_FIELD`, `WORKFLOW_TIMEOUT_SECONDS`, `MOCK_FAULTS`, `REPLAY_DIR`, `REPLAY_STEP_SECONDS` (replay pacing, default 0.35 s per event).

---

## 7. THE FRONTEND: complete build handover

Goal: a premium enterprise "ChainAudit Control Center", not a Streamlit-style prototype. A judge should understand in 10 seconds what is happening and trust it because every claim is traceable to a source.

### 7.1 Stack
Next.js (App Router) + TypeScript strict + Tailwind + shadcn/ui + Framer Motion + TanStack Query + `openapi-typescript` (generate `lib/api-types.ts` from `docs/openapi.json`; never hand-write API types). Env: `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_MOCK=1` for fixture mode. Backend CORS default allows `http://localhost:3000` and exposes `X-Highlight`.

### 7.2 Fixtures for day one (no backend needed)
`demo_data/replays/*.json` are recordings of **real runs**. Each has `workflow` (status, rule_set), `documents`, `extractions`, `findings` and the full `events` array. Names: `clean_supplier`, `failed_supplier`, `auditor_loop_fault_injection`, `decoy_trap_fault_injection`. Build mock mode by serving these through a fake `EventSource` and fake endpoints. Pacing is one event per ~350 ms. Because they are real recordings the UI is built against true payloads.

### 7.3 API surface (all under `/api`)
| Call | Use |
|---|---|
| `GET /health` | provider/model/`mock`/`fault_injection` for the "provider badge" |
| `POST /evidence/upload` (multipart `files` repeated, optional `rule_set_id`) | returns `{workflow_id, document_ids}` |
| `POST /workflows/{id}/evidence` | add files, only when `AWAITING_EVIDENCE` (else 409 `WORKFLOW_STATE`) |
| `GET /workflows`, `GET /workflows/{id}` | list; detail = documents, extractions, findings (incl. `superseded`), `rule_set`, `outcome`, `labels` |
| `GET /workflows/{id}/events` (SSE), `/events/history` | live trace; history for Audit Log |
| `GET /findings?workflow_id&status&needs_review` | review queue (`needs_review=true` = requires review AND undecided) |
| `GET /findings/{id}` | `{finding, events, audit_checks}` |
| `GET /findings/{id}/evidence-image?evidence_index=0` | PNG; header `X-Highlight: bbox|none`; 404 for absence evidence (value null) |
| `POST /reviews/{finding_id}` `{decision, edited_text?, comment?, reviewer}` | `approved|edited|rejected|more_evidence`; `edited` needs `edited_text` (422) |
| `POST /findings/{id}/dispatch` `{channel: demo_whatsapp|demo_email, language: en|roman_ur}` | demo outbox only |
| `POST /workflows/{id}/rerun-rules` `{overrides:{CHEM_MAX:{limit:20}}}` or `{rule_set_id}` | returns `changed[]`, `llm_calls` |
| `GET /rules`, `GET /rule-sets/{id}` | active / specific rule set (`content.rules[]` holds editable fields like `limit`) |
| `GET /audit/{id}` | full chain: supplier_ids, batch_ids, documents, extractions, rule_set, findings, reviews, corrective_actions, outbox, events |
| `GET /replays`, `POST /replays/{name}/start` | bundled recordings; start returns a NEW `workflow_id`, all events `replay=true` |

Errors are always `{"error":{"code","message","details?"}}`. Useful codes: `DISPATCH_NOT_APPROVED, DECISION_ALREADY_RECORDED, REVIEW_NOT_REQUIRED, EDITED_TEXT_REQUIRED, WORKFLOW_STATE, FINDING_SUPERSEDED, INVALID_OVERRIDES, FILE_TOO_LARGE, UNSUPPORTED_FILE_TYPE, EXTENSION_MISMATCH, TOO_MANY_FILES`.

### 7.4 Core data shapes
**Finding**: `finding_id, workflow_id, evaluation_number, rule_id, rule_title, compliance_status, reason (built by code), escalation_reason|null, evidence[], auditor_flags[], audit_status (pending|verified|uncertain), requires_human_review, review_decision|null, corrective_action|null {en, roman_ur, status:"draft", label:"AI draft. Review before sending."}, superseded, audit_notes[], rule_set_label`.
**EvidenceField**: `field, value (string|number|null), unit, source_document, document_id, page, quote, confidence, llm_confidence, verification (unverified|exact|normalized|fuzzy|not_found|vision_only), verified, bbox [x0,y0,x1,y1] in PDF points`.
Special `rule_id`s: `DOC_INTAKE:<document_id>` are document-level problems (failed read, unknown type, image-only, injection); they have empty evidence plus an escalation reason. Evidence with `value: null` is an "absence marker" (we looked here, it was not there).
**Important**: after `rerun-rules` or added evidence, old findings get `superseded: true` and **new finding ids appear**. Always filter `!superseded` for the live view and refetch.

### 7.5 SSE hook contract (`useWorkflowEvents(workflowId)`)
- `EventSource` on `/api/workflows/{id}/events`. Each message: `id: <seq>`, `event: <EventType>`, `data: <StoredEvent JSON>`.
- Keep `lastSeq`; **de-duplicate by `seq`**; on error reconnect (native EventSource sends `Last-Event-ID`; for manual reconnect pass `?after=<lastSeq>`).
- State is a pure reducer over events: `events -> agent lanes, finding cards, loop state`. No `setTimeout` fake progress. For late joiners the server replays full history first, so the same reducer renders a finished run.
- `StoredEvent`: `id, workflow_id, seq, type, agent (orchestrator|extractor|compliance|auditor|corrective|reviewer), timestamp, finding_id, document_id, message, data, replay`.

### 7.6 Event catalogue (the `data` keys you can rely on)
| Type | Key `data` fields |
|---|---|
| WORKFLOW_CREATED | `rule_set, rule_set_label, provider, model, mock, labels, document_count` |
| DOCUMENT_RECEIVED | `document, sha256, size, mime` (has `document_id` on the event) |
| EXTRACTION_STARTED / COMPLETED / FAILED | completed: `document, doc_type, classified_by, field_count, cached, pages, image_only_pages, possible_prompt_injection`, plus `fault_injected, fault[]` when injected; failed: `reason` |
| COMPLIANCE_CHECK_STARTED | `evaluation_number, rule_set, rule_count, reference_date`; OR re-evaluation: `reevaluation:true, reason, changes[{finding_id, rule_id, before{compliance_status,reason}, after{...}}]` |
| FINDING_CREATED | `rule_id, rule_title, compliance_status, escalation_reason, reason` (has `finding_id`) |
| AUDIT_STARTED / AUDIT_COMPLETED | `findings, documents` / `verified, uncertain, needs_review, reextractions, conflicts` |
| AUDIT_CHECK_RESULT | `check` in `evidence|cross_document|certificate_validity|absence|semantic|date`. Evidence checks: `field, ref, passed, verification, confidence, page, value, quote, bbox, flags[], checks[{name,passed,detail}], document`. Semantic: `passed, rationale, concerns, cached, available, fault_injected?` |
| AUDIT_CONFLICT_FOUND | `case:"A"`: `field, document, page, previous_value, quote, flags, attempts_used, limit, limit_reached`. `case:"B"` (genuine) / `"unresolved"`: `field, genuine, values[{document,value,page,quote,verified}], flag, reextraction_attempted:false, finding_ids[]` |
| REEXTRACTION_REQUESTED | `field, page_hint|null, attempt, limit, mode: "page"|"document", document` |
| REEXTRACTION_COMPLETED | `field, old_value, new_value, changed, found, page, attempt, document` |
| HUMAN_REVIEW_REQUIRED | `rule_id, compliance_status, audit_status, escalation_reason, reason` |
| CORRECTIVE_ACTION_DRAFTED | `rule_id, label, template_fallback` |
| REVIEW_DECISION_RECORDED | `decision, reviewer, rule_id` |
| RULES_RERUN_STARTED / COMPLETED | started: `rule_set_id, rule_set, overrides`; completed: `changed[{rule_id,before,after}], llm_calls, duration_ms, extraction_rerun:false` |
| DISPATCH_RECORDED | `channel, language, outbox_id, demo_only:true` |
| WORKFLOW_COMPLETED / FAILED | `findings` / `reason` |

Typical ordering: created, received x N, extraction started x N (parallel), completed x N, compliance started, finding created x rules, audit started, per-field check results, [conflict, reextraction requested, completed], re-evaluation, cross-document and semantic checks, human review required, corrective drafted, audit completed. Note the order of parallel extractor events is not deterministic.

### 7.7 Design direction ("amazing" without being gimmicky)
- **Mood**: calm, forensic, trustworthy. Think audit ledger meets mission control. Light theme default (paper-white `#FAFAF8` surface, ink `#0E1116`), dark mode supported via tokens.
- **Type**: Geist or Inter Tight for UI, JetBrains Mono for quotes, ids, values and `rule_id`s (mono = "this is evidence/data").
- **Status colours (consistent everywhere)**: green `#1F9D6B` passing, red `#D64545` not satisfied, amber `#E0A100` human review, slate for pending, blue accent `#3B5BDB` for actions. Each agent gets a quiet hue for its lane (orchestrator slate, extractor teal, compliance indigo, auditor violet, corrective amber, reviewer blue).
- **Signature moments** (spend your polish here):
  1. **Agent Trace**: lanes for Orchestrator, one lane per document (parallel extractors), Compliance, Auditor. Nodes change state (queued, working, complete, conflict, escalated). Findings slide in as `FINDING_CREATED` arrives.
  2. **The loop made visible**: `AUDIT_CONFLICT_FOUND` (red pulse on the extractor node) to `REEXTRACTION_REQUESTED` (arrow back to the extractor lane, "re-reading page 2") to `REEXTRACTION_COMPLETED` (value chip animates `BT-2041` to `BT-2047`) to auditor re-check turns green. If `mode:"document"` say "re-reading the whole document".
  3. **Evidence viewer**: page image beside quote/doc/page; if `X-Highlight: bbox` the highlight is already drawn in the PNG, so pair it with a subtle pulse ring. Fallback to quote + doc + page when `none`.
  4. **Verdict flip** on rerun: the finding card morphs red to green with the old verdict ghosted and the banner "Re-evaluated cached evidence. No extraction re-run. 0 model calls." (`llm_calls`, `duration_ms`).
  5. **Conflict side-by-side** for Case B: two source pages next to each other (BT-2047 p.2 vs BT-2041 p.1), caption "Potential inconsistency requiring human review".
  6. **Provenance chain** on the Audit page: Supplier, Batch, Documents, Evidence, Rules, Findings, Reviewer decision, Corrective action, as a clean vertical/horizontal chain with the event timeline beneath.
- **Motion**: restrained. Framer Motion for state transitions only; respect `prefers-reduced-motion`; no layout shift as events stream (reserve space, animate opacity/transform).
- **Always-visible trust strip** (top bar): provider badge (`mock` vs real, from `/health`), "Synthetic Demo Data", "Demo Buyer Framework v1.0", and `REPLAY` / "Fault injection (test mode)" when applicable.
- **Copy rules**: never write "fraud" or "certified". Use "Requirement not satisfied", "Potential inconsistency requiring human review". Show `AI draft. Review before sending.` on every corrective action. Never call confidence a "probability".
- **Escalation reasons in plain language**: LOW_EXTRACTION_CONFIDENCE "We could not read this value reliably"; CONFLICTING_VALUES "Documents disagree with each other"; MISSING_REQUIRED_FIELD "A required value is missing"; UNSUPPORTED_CONCLUSION "The auditor could not confirm the conclusion from the quote"; EXTRACTION_FAILED "We could not read this file"; REEXTRACTION_LIMIT "Re-reading did not resolve it"; UNVERIFIABLE_SOURCE "This source cannot be checked against document text (scan or suspicious content)"; UNKNOWN_DOCUMENT_TYPE "We could not tell what kind of document this is".

### 7.8 Screens (build in this order)
1. **Workflow run page `/workflows/[id]`**: dropzone start state; live Agent Trace; findings appear live; rules panel; status chips. Include a Replay picker (`GET /replays`, start returns a new id, navigate to it).
2. **Finding detail** (drawer/page): rule, status, code-built reason, evidence list, evidence viewer, auditor checks (from `audit_checks`, pass/fail), escalation reason in plain language, flags as chips, Case B side-by-side.
3. **Review Queue**: `GET /findings?needs_review=true`; the four actions (Approve, Edit, Reject, Request more evidence); corrective-action editor with English / Roman Urdu tabs; "Send to demo outbox" enabled only after approve/edit; optimistic UI that reconciles with server errors (409/422 shapes above).
4. **Rules panel**: shows `rule_set.content.rules`; editable numeric `limit` for `CHEM_MAX`; calls `rerun-rules` with `overrides`; animates the flip; shows `changed[]`, `llm_calls`, `duration_ms`. Also let the user switch back via `rule_set_id` (`rule_set.derived_from`).
5. **Audit record `/audit/[id]`**: the provenance chain + event timeline from `/events/history`.
6. **Thin pages** (clearly labelled demo numbers): Overview (counts from `/workflows`), Suppliers (derive from `/audit/{id}.supplier_ids`), Evidence, Findings list, Audit Log. Navigation: Overview, Suppliers, Evidence, Findings, Review Queue, Audit Log.

### 7.9 Quality bar and pitfalls
- Empty, loading and error states everywhere; keyboard accessible; responsive to tablet; consistent tokens.
- Treat `superseded` findings as history, not live.
- The review queue's `needs_review` already excludes decided findings.
- Evidence index must exist and have a page; absence evidence and `DOC_INTAKE` findings have no page image (handle the 404 gracefully).
- Use `workflow.status` for the page state; use events for the animation. After terminal events (`WORKFLOW_COMPLETED`, `WORKFLOW_FAILED`, `AUDIT_COMPLETED`) refetch `/workflows/{id}`.
- Replay workflows are real workflows (findings/evidence images work); they just have `replay:true` events and the `REPLAY` label.
- Upload as `multipart/form-data` with repeated `files`. Accept PDF/PNG/JPG only; the server rejects the rest with the error codes above.

### 7.10 Frontend phases and acceptance
| Phase | Deliverable | Done when |
|---|---|---|
| F0 | scaffold, tokens, layout, generated types, mock mode on replay fixtures | app runs offline with `NEXT_PUBLIC_MOCK=1` |
| F1 | SSE hook + Agent Trace | `failed_supplier` replay animates from events only; reload mid-run resumes without duplicates |
| F2 | finding drawer + evidence viewer | highlighted page, fallback, Case B side-by-side |
| F3 | review queue + actions + outbox | approve/edit/reject/more-evidence, errors reconcile, dispatch gated |
| F4 | rules panel + rerun | 15 to 20 flips `CHEM_MAX`; shows 0 model calls |
| F5 | audit record + thin pages | chain + timeline render for all four replays |
| F6 | integrate real backend | contract mismatches are reported, not patched around silently |
| F7 | deploy build + scripted walk of both scenarios | demo script (section 8) runs clean |

### 7.11 Ready-to-paste prompt for the frontend session
> You are a senior frontend engineer building the **ChainAudit Control Center**: a premium enterprise SaaS UI (calm, forensic, trustworthy; not a Streamlit prototype). Read `HANDOVER.md` section 7 and `docs/openapi.json`; generate `lib/api-types.ts` with `openapi-typescript` (never hand-write API types). Stack: Next.js App Router, TypeScript strict, Tailwind, shadcn/ui, Framer Motion, TanStack Query. Build mock mode (`NEXT_PUBLIC_MOCK=1`) from `demo_data/replays/*.json` (real recorded events and snapshots). Rules: the live agent trace is driven ONLY by SSE events (`useWorkflowEvents`, reconnect with Last-Event-ID, de-dupe by `seq`, reducer over events), no fake timers; always show "Synthetic Demo Data", "Demo Buyer Framework v…", provider badge, REPLAY and "Fault injection (test mode)" labels, and "AI draft. Review before sending." on drafts; never use the words "fraud" or "certified"; map `EscalationReason` to the plain-language strings in section 7.7; do not invent endpoints; when something is unspecified choose the simplest option and note it in `docs/DECISIONS.md`. Work in phases F0 to F7 from section 7.10, stop and report after each with what was built, how it was verified, and any API mismatch. Spend the polish budget on the agent trace and the visible Auditor-to-Extractor loop, the evidence viewer, and the verdict flip on rule rerun.

---

## 8. The demo (3 minutes, judge-proof)

Setup: backend on `mock` for reliability (or live provider if the checklist passed), frontend open, provider badge visible.
1. **(20 s) The problem**: fragmented supplier PDFs, buyer requirements, no traceability.
2. **(60 s) Failed supplier**: drop the 3 files in `demo_data/failed_supplier/`. Watch parallel extractors, findings appearing, the Auditor checking. Open `CHEM_MAX`: highlighted "17.4 ppm" on page 2, "exceeds configured maximum of 15 ppm". Open `ID_CONSISTENCY`: BT-2047 vs BT-2041 side by side, labelled "potential inconsistency requiring human review", and say the Auditor deliberately did **not** try to "fix" it because both quotes are real.
3. **(45 s) The loop**: start the replay `auditor_loop_fault_injection` (labelled Fault injection (test mode)): wrong value, quote not found, re-read page 2, corrected, finding flips back. Say: the Auditor can challenge and request a re-read but can never change a verdict.
4. **(30 s) Human in the loop**: approve `CHEM_MAX`, show the English/Roman Urdu draft ("AI draft. Review before sending."), send to the **demo outbox**. Show dispatch is blocked before approval.
5. **(25 s) Rules rerun**: change 15 to 20, the card flips green, banner says re-evaluated cached evidence, 0 model calls.
Backup: if anything fails, start a replay: same visuals from recorded real events.

Honest talking points: compliance decisions are deterministic code (not an LLM); every value cites page + quote and is checked against the PDF text; unsupported values are null; the demo uses synthetic data and a demo framework; we make no accuracy claim.

---

## 9. Troubleshooting
- **Startup fails with a config message**: provider key/model missing for the chosen `LLM_PROVIDER`. Use `mock` to run offline.
- **SSE shows nothing in a proxy**: disable buffering (nginx `proxy_buffering off`); keep a single backend process.
- **Frontend cannot read `X-Highlight`**: set `CORS_ORIGINS` to the frontend origin (the header is already exposed).
- **Fault injection does nothing**: `MOCK_FAULTS` only applies to the `mock` provider and fires once per document; a cached document will not fault again (use a new file or fresh DB).
- **Prompt change has no effect**: bump `EXTRACTOR_VERSION` (cache key).
- **Workflow stuck/`FAILED: interrupted`**: the process restarted mid-run; re-upload.
- **`docs/openapi.json is stale` test failure**: run `make openapi`.

## 10. Glossary
**Finding** one rule's result for a workflow. **Evidence** an extracted value with source, page, quote, confidence. **Auditor** independent agent that verifies evidence and can flag/escalate but never decide compliance. **Case A** extraction error (quote not found) triggers re-extraction. **Case B** genuine conflict (both quotes verify), escalated without re-extraction. **Superseded** an old finding replaced by a newer evaluation. **Derived rule set** an immutable copy of a rule set with overrides. **Replay** a recorded real run re-created as a new workflow with `replay=true` events.
