# Deploy to Vercel (one project: static page + Python API)

1. Push this folder to a GitHub repo (`.env` is git-ignored; never commit your key).
2. vercel.com -> Add New -> Project -> import the repo. No build settings needed (`vercel.json` handles it).
3. Settings -> Environment Variables: `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`). Redeploy.
4. Open the deployment URL. Sample packs work without a key; PDF upload needs it.

Local test: `npm i -g vercel && vercel dev`
