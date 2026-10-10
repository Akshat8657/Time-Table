# TimeTablePro - Production Checklist (before going live on the internet)

Everything below is a **setting**, not a code change. Deployment itself is the next phase; this list makes sure the move is safe.

## 1. Backend `.env` (copy `.env.example`, fill every line)
- [ ] `APP_ENV=production` - the server then **refuses to start** if a setting below is still a laptop value, sends `Strict-Transport-Security`, and forces Secure cookies.
- [ ] `FRONTEND_ORIGINS=https://timetable.your-college.edu` - only your real website (https), no `localhost`.
- [ ] `ALLOWED_HOSTS=api.your-college.edu` (the name(s) the API is reached by).
- [ ] `COOKIE_SECURE=true` (automatic in production) and `COOKIE_SAMESITE=lax` when website + API share one site (e.g. `timetable.` and `api.` of the same domain); `none` only if they are on different sites.
- [ ] `TRUST_PROXY_HEADERS=true` **only** if the API sits behind your own reverse proxy / load balancer that **overwrites** `X-Forwarded-For`; otherwise `false`.
- [ ] `ENABLE_API_DOCS=false`.
- [ ] `SUPABASE_*` keys of the production project; `SUPABASE_URL` must be `https://`.
- [ ] `DB_POOL_MAX` - Supabase free tier allows **15 connections for everything together** (app + any script + the dashboard). On the laptop keep the default 6 (so a script can run beside the app); on a server where only the app connects, 8-10 is fine. Run **one** server process (the live bell, the AI confirm cards and the read cache live inside that process).
- [ ] `GEMINI_API_KEY` (free key) - optional; without it the assistant says "AI abhi available nahi hai" and everything else works.
- [ ] `VAPID_*` - generate once with `python -m scripts.gen_vapid` and **never change them** afterwards.
- [ ] `.env` is never committed, never zipped, never sent by email or chat.

## 2. Frontend
- [ ] `frontend/.env.production`: `VITE_API_BASE_URL=https://api.your-college.edu` (empty when the website and the API are served from the same address).
- [ ] Build with `npm run build` and serve the `frontend/dist` folder as static files (any free static host / your web server). The build already contains a Content-Security-Policy (`<meta>`): only our own scripts, only our API.
- [ ] The web server adds these headers to the website (they cannot be set from the page itself):
  `X-Frame-Options: DENY` (or `Content-Security-Policy: frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Strict-Transport-Security: max-age=31536000`.
- [ ] Serve every page of the single-page app from `index.html` (so `/teacher/home` etc. work after a refresh).

## 3. Supabase project
- [ ] Authentication -> Sign In / Providers -> **"Allow new users to sign up" = OFF** (the server warns at start-up if it is on).
- [ ] Authentication -> URL configuration: site URL = your website.
- [ ] Database migrations applied: `cd backend` -> `python -m scripts.migrate` (up to `014_refresh_token_reuse.sql`; `013_phase8_hardening.sql` makes the browser roles READ-only, through Row Level Security).
- [ ] First admin: `python -m scripts.seed_admin` (prints a temporary password once; first login forces a change).

## 4. HTTPS and the server
- [ ] HTTPS everywhere (free certificates: Let's Encrypt / your host). Plain http must redirect to https.
- [ ] Run the API with `uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-server-header --proxy-headers` behind the proxy, **one** worker.
- [ ] Logs (`logs\backend.log` locally) never contain passwords, tokens or keys - keep it that way; do not raise the log level to DEBUG in production.

## 5. After going live (5 minutes)
- [ ] Open the website, log in as admin, change the temporary password.
- [ ] `https://api.../api/health` answers `{"status":"ok"}`.
- [ ] Browser developer tools -> Console: no red "Content Security Policy" errors.
- [ ] Try `http://` - it must switch to `https://`.
- [ ] Make a backup of the database from the Supabase dashboard (Database -> Backups) and note the date.
