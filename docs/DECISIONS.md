# Decisions (one line each)

**Confidence formula (8.6):** `confidence = min(llm_confidence, 1.0, cap)`; caps: `not_found` 0.40, `fuzzy` 0.70, `vision_only` 0.75, suspected-guess 0.40; `exact`/`normalized` unchanged. The raw value is kept in `llm_confidence`, so recomputing is idempotent. It is a heuristic, never a calibrated probability.

- Extra table `extraction_cache` (key -> result) and `semantic_cache`; the spec's `extractions` table stays one row per document, so re-extraction can update the cache without rewriting other workflows' records.
- Cache hits reset verification/bbox: the Auditor always recomputes them. Re-extraction writes the corrected result back to the cache.
- Semantic-check cache key = rule + quotes + conclusion (reason with the rule-set version tag stripped) + provider + model; never per-upload ids. Injected test faults are never cached.
- Findings are created before the audit (spec order), then **deterministically re-evaluated** once the evidence is verified/corrected. Before/after values are logged in a `COMPLIANCE_CHECK_STARTED` event with `data.reevaluation=true`. The Auditor has no status-write path; only `agents/compliance/evaluator.py::apply_result` decides, from an engine result.
- Escalation reasons for cases the locked enum does not name: unit mismatch -> `CONFLICTING_VALUES`; missing unit -> `MISSING_REQUIRED_FIELD`; malformed date/number -> `LOW_EXTRACTION_CONFIDENCE`; future-dated report -> `CONFLICTING_VALUES`; prompt-injection text or image-only source -> `UNVERIFIABLE_SOURCE`.
- Document-level problems (failed read, unknown type, image-only pages, injection) become findings with `rule_id = DOC_INTAKE:<document_id>` so they reach the review queue.
- "Not found" conclusions (e.g. certification absent) carry null-value *absence markers* as evidence; the Auditor checks the document text really lacks the token (else `CONCLUSION_UNSUPPORTED`).
- Classification: page content beats filename when they disagree (`classified_by` records the method).
- Decoy defence: a verified identity quote containing previous/prior/reference/superseded/etc. raises `EXTRACTOR_GUESS_SUSPECTED` and triggers a whole-document re-read (the right value is not on the decoy's page). Known limit: a legitimate quote that happens to contain such a word is re-read once and then re-verified.
- Re-extraction reads the quoted page first; later attempts (or suspected guesses / missing pages) read the whole document. Max 2 attempts per (document, field), then `REEXTRACTION_LIMIT`. If the model returns null, the old (flagged, capped) evidence is kept rather than silently dropped.
- Fuzzy matching (>= 92) can accept a one-character-off ID quote; it is flagged `QUOTE_FUZZY_ONLY`, capped at 0.70 (< 0.80 review threshold) and never `verified`.
- `DATE_CONFLICT` means a logical date problem (issue date after validity end, document dated after the reference date). Different dates on different documents are normal.
- Rule reruns: overrides always apply to a content-addressed derived rule set (id `...+ovr-<hash>`; label lists differences from the ROOT set, so re-applying is idempotent). Reruns use cache-only semantic checks and reuse drafts for identical conclusions, so `llm_calls` is truly 0 for the demo flow. Human decisions carry over only for unchanged findings; `more_evidence` never carries over.
- Additional evidence is accepted only in `AWAITING_EVIDENCE` and adds documents (there is no document replacement). A conflicting older document therefore still counts.
- Replays are recordings of real runs, re-created as a new workflow (ids remapped) with every event `replay=true`; pacing is `REPLAY_STEP_SECONDS`. The workflow row is created in its final status immediately.
- Robustness PDFs live in `demo_data/robustness/` (`injection_test.pdf`, `scan_like.pdf`).
- The mock provider cannot read images; a scan-only pack yields a `DOC_INTAKE` `UNVERIFIABLE_SOURCE` finding. Real vision providers get PNG renders (200 dpi) and their evidence is `vision_only`.
- No authentication (hackathon demo), see README.
