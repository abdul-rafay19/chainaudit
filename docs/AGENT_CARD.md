# ChainAudit.ai agent card

| Agent | Job | May decide compliance? | LLM? |
|---|---|---|---|
| Orchestrator | lifecycle, parallelism, failure handling, review/dispatch/rerun | No | No |
| Evidence Extraction | reads each document in parallel; values carry `source_document`, `page`, `quote`, `confidence`; unsupported values are `null` | No | Yes (schema-validated) |
| Compliance Evaluation | one finding per rule from the **deterministic rules engine** | **Only via the engine** | No |
| Evidence Auditor | verifies every quote against the PDF text, bbox highlight, cross-document identity, certificate dates; may add flags, request re-extraction, confirm, or escalate | **No (impossible in code)** | Semantic check only |
| Corrective drafter | EN + Roman Urdu draft, labelled "AI draft. Review before sending." | No | Yes |

Guarantees: documents are untrusted data (`<untrusted_document>`, schema-constrained output, injection phrases escalate to review); nothing is dispatched without a recorded human approval; the demo outbox never sends anything; every event is stored append-only and drives the live trace and the audit record.
Labels: "Synthetic Demo Data", "Demo Buyer Framework". No accuracy claim is made; `scripts/evaluate.py` reports counts from planted synthetic flaws only.
