# ChainAudit frontend (F0 + F1)
```
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_MOCK=1 runs offline on recorded replays
npm run types                      # generates lib/api-types.ts from docs/openapi.json
npm run dev
```
Done: scaffold, tokens, trust strip, SSE hook + pure reducer, Agent Trace, Auditor loop banner, finding cards, replay picker, upload.
Next (F2-F5): finding drawer + evidence viewer, review queue, rules rerun flip, audit record page.

F2-F4 added: finding drawer + evidence viewer (conflict side-by-side), review actions + gated demo outbox, rules re-check with verdict ghosting, /review queue.
Offline mode computes decisions/re-check locally and says so. Still to do: audit record page, thin pages, generated API types.
