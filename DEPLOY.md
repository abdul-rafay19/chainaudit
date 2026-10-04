# Deploy (free, no card)
Layout: `backend/` (FastAPI) + `frontend/` (Next.js) + `demo_data/` + `docs/` in one repo.

## What we run
- **Frontend:** Vercel, Root Directory = `frontend`.
- **Backend:** SnapDeploy free container built from the root `Dockerfile` (port 8000).

## Order
1. Push to GitHub. Never commit `.env` or `.env.local` (check with `git status` and `git check-ignore -v backend/.env`).
2. Backend host env vars: `LLM_PROVIDER`, `GROQ_API_KEY`, `LLM_MODEL`, `EXTRACTION_CONCURRENCY=1`, `LLM_MAX_RETRIES=3`, `CORS_ORIGINS=https://<your-app>.vercel.app,http://localhost:3000`. Use the short production domain, not a per-deployment URL. Do not set `DATABASE_URL` to a Postgres URL (the app uses SQLite).
3. Check `https://<backend>/api/health`: `provider` and `mock:false`.
4. Vercel env: `NEXT_PUBLIC_API_URL=https://<backend>` and `NEXT_PUBLIC_MOCK=0`, then **Redeploy** (these are baked in at build time).
5. Keep a second Vercel project with `NEXT_PUBLIC_MOCK=1` as a backup that needs no backend.

## Gotchas
- SnapDeploy may show a "PostgreSQL required" prompt because it sees SQLAlchemy. It is a false alarm: choose the external option instead of creating a database.
- Free containers sleep after about 15 idle minutes and wake in about a minute. Recorded runs never need the backend.
- Local files (SQLite, uploads) are lost on restart or redeploy.
- Uploads from the public site use your LLM key. Keep it only in the backend host's env vars.

## Other hosts
`render.yaml` is a Render Blueprint (the free tier needed a card for us). Switching hosts only needs `NEXT_PUBLIC_API_URL` and `CORS_ORIGINS` updated, then a Vercel redeploy.
