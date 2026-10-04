# Deploy (free, no card)
Frontend -> Vercel (Root Directory = `frontend`). Backend -> Render free (Blueprint from `render.yaml`).
1. Push the repo to GitHub. NEVER commit `.env` or `.env.local` (keys!).
2. Render: New > Blueprint > pick the repo. Set GROQ_API_KEY and CORS_ORIGINS (your Vercel URL, no trailing slash). Region cannot be changed later.
3. Vercel: env `NEXT_PUBLIC_API_URL=https://<service>.onrender.com`, `NEXT_PUBLIC_MOCK=0`. Redeploy after changing env.
4. Recorded runs work with no backend at all. Only file upload needs the live backend, which sleeps after ~15 idle minutes (about 1 minute to wake) and loses its local files on each spin-down.
