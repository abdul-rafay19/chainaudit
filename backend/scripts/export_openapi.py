"""Write docs/openapi.json (the contract) and docs/API_CONTRACT.md (generated summary)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["CHAINAUDIT_NO_AUTOAPP"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

DOCS = Path(__file__).resolve().parents[2] / "docs"

NOTES = """# ChainAudit.ai API contract

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
"""


def main() -> None:
    app = create_app(Settings(_env_file=None, database_url="sqlite://", llm_provider="mock"))
    spec = app.openapi()
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "openapi.json").write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")

    def ref(s: dict | None) -> str:
        if not s:
            return ""
        if "$ref" in s:
            return f"`{s['$ref'].split('/')[-1]}`"
        if s.get("type") == "array":
            return f"`{ref(s['items']).strip('`')}[]`"
        return f"`{s.get('type', 'any')}`"

    rows = ["| Method | Path | Request body | Response | Summary |", "|---|---|---|---|---|"]
    for path, ops in sorted(spec["paths"].items()):
        for method, op in ops.items():
            body = op.get("requestBody", {}).get("content", {})
            req = next((ref(v.get("schema")) for v in body.values()), "")
            ok = op["responses"].get("200", {}).get("content", {})
            resp = next((ref(v.get("schema")) for k, v in ok.items() if "json" in k), "SSE stream" if "text/event-stream" in ok else ("`image/png`" if "image/png" in ok else ""))
            rows.append(f"| {method.upper()} | `{path}` | {req} | {resp} | {op.get('summary', '')} |")
    enums = sorted(n for n, s in spec["components"]["schemas"].items() if "enum" in s)
    text = NOTES + "\n".join(rows) + "\n\n## Enums exported\n\n" + ", ".join(f"`{e}`" for e in enums) + "\n"
    (DOCS / "API_CONTRACT.md").write_text(text)
    print(f"wrote {DOCS / 'openapi.json'} ({len(spec['paths'])} paths) and API_CONTRACT.md")


if __name__ == "__main__":
    main()
