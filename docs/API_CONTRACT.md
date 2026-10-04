# ChainAudit.ai API contract

Generated from `docs/openapi.json` by `make openapi`. **`openapi.json` is the contract.** Do not make breaking
changes without regenerating both files and telling the frontend.

## Conventions

- **Errors** always have the shape `{"error": {"code": "...", "message": "...", "details": ...?}}`.
  Statuses: 400 bad request, 404 not found, 409 conflict/illegal state, 413 too large, 415 unsupported type, 422 validation, 500 internal.
- **Labels the UI must show:** `Synthetic Demo Data`, `Demo Buyer Framework v…` (from `rule_set`), `REPLAY` when `replay=true`,
  `Fault injection (test mode)` when an event has `data.fault_injected`, `AI draft. Review before sending.` on corrective actions,
  and the active provider (`/api/health` → `provider`, `mock`).
- **Never** use the words "fraud" or "certified" in UI copy.
- **Enums** (`ComplianceStatus`, `AuditStatus`, `ReviewDecision`, `EscalationReason`, `WorkflowStatus`, `AuditorFlag`, `EventType`, ...) are exported as OpenAPI components; generate TypeScript unions from them.
- **Compliance verdicts** are produced only by deterministic Python rules. The Auditor can flag, request re-extraction, confirm or escalate.

## Server-Sent Events: `GET /api/workflows/{id}/events`

Each message: `id: <seq>`, `event: <EventType>`, `data: <StoredEvent JSON>`. On connect the server first replays stored events with
`seq > Last-Event-ID` (header) or `?after=`, then streams live events with no gaps or duplicates. A comment heartbeat is sent every
15 s. `seq` is monotonic **per workflow**; de-duplicate on it. Reconnect with `Last-Event-ID: <last seq>`.
`GET /api/workflows/{id}/events/history` returns the same events as JSON.

Useful `data` keys: `fault_injected`, `cached`, `case` (`A` re-extraction, `B` genuine conflict), `old_value`/`new_value` (REEXTRACTION_COMPLETED),
`changes` (re-evaluation after evidence changed), `llm_calls` and `changed` (RULES_RERUN_COMPLETED).

## Endpoints
| Method | Path | Request body | Response | Summary |
|---|---|---|---|---|
| GET | `/api/audit/{workflow_id}` |  | `AuditRecord` | Audit |
| POST | `/api/evidence/upload` | `Body_upload_api_evidence_upload_post` | `UploadResponse` | Upload |
| GET | `/api/findings` |  | `Finding[]` | List Findings |
| GET | `/api/findings/{finding_id}` |  | `FindingDetail` | Get Finding |
| POST | `/api/findings/{finding_id}/dispatch` | `any` | `DispatchResponse` | Dispatch |
| GET | `/api/findings/{finding_id}/evidence-image` |  |  | Evidence Image |
| GET | `/api/health` |  | `HealthResponse` | Health |
| GET | `/api/replays` |  | `ReplayInfo[]` | Replays |
| POST | `/api/replays/{name}/start` |  | `ReplayStartResponse` | Replay Start |
| POST | `/api/reviews/{finding_id}` | `ReviewRequest` | `ReviewResponse` | Review |
| GET | `/api/rule-sets/{rule_set_id}` |  | `RuleSetInfo` | Get Rule Set |
| GET | `/api/rules` |  | `RuleSetInfo` | Active Rules |
| GET | `/api/workflows` |  | `WorkflowSummary[]` | List Workflows |
| GET | `/api/workflows/{workflow_id}` |  | `WorkflowDetail` | Get Workflow |
| GET | `/api/workflows/{workflow_id}/events` |  |  | Stream Events |
| GET | `/api/workflows/{workflow_id}/events/history` |  | `StoredEvent[]` | Event History |
| POST | `/api/workflows/{workflow_id}/evidence` | `Body_add_evidence_api_workflows__workflow_id__evidence_post` | `UploadResponse` | Add Evidence |
| POST | `/api/workflows/{workflow_id}/rerun-rules` | `RerunRequest` | `RerunResponse` | Rerun Rules |

## Enums exported

`AgentName`, `AuditStatus`, `AuditorFlag`, `ComplianceStatus`, `DocType`, `EscalationReason`, `EventType`, `ReviewDecision`, `Verification`, `WorkflowStatus`
