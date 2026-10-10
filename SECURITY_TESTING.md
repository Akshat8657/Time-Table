# TimeTablePro - Phase 8 penetration-style test (findings list)

We attacked our own app the way an outsider or a curious insider would. Every attack is now an automatic test
(`backend/tests/test_p8_security.py`, plus the older security tests) - all of them must stay **refused**.

## Found -> fixed

| # | What we tried | What we found | Fixed by |
|---|---|---|---|
| 1 | A forged token (HS256, made-up secret) sent to **logout** | Logout read tokens "even if expired" WITHOUT checking them, so a forged token naming someone's session could end that person's login | HS256 tokens are refused outright (this project signs with ES256 keys); an unchecked token is never read, not even for logout (`security/jwt_verify.py`) |
| 2 | 30+ wrong passwords, each with a made-up `X-Forwarded-For` address | The per-computer login limit trusted the FIRST forwarded address on every local request - a script could invent a new "computer" per guess | Forwarded addresses are believed only behind our own proxy (`TRUST_PROXY_HEADERS`) and then only the LAST entry (`routers/auth.py`) |
| 3 | Direct writes to the Supabase data API (PostgREST) with a normal login token | A teacher could edit their own name / phone / photo / specialization, an HOD his school's profiles, the admin add or rename schools - **without validation and without an audit line** | Migration `013_phase8_hardening.sql`: write policies dropped, every write privilege of the browser roles revoked (also for future tables, functions, sequences). The browser roles can only READ, through RLS |
| 4 | Re-using an OLD login cookie (refresh token) 11 s after it was swapped | Still accepted (Supabase lets the previous token be used again) - a stolen cookie could keep a session alive | Migration `014_refresh_token_reuse.sql` + `security/sessions.py`: a fingerprint of the newest and the previous token is kept; the previous one works for 10 s only (two tabs refreshing at once), anything older **ends the whole session** |
| 5 | A NUL character (`%00`) in search boxes (teachers, audit log, holidays) | Server error 500 | Refused with a friendly 422 before it reaches the database (`main.py`) |
| 6 | A 9 MB JSON body, and a 10 MB streamed (chunked) body | No overall size limit - the whole body would be read into memory | `MAX_REQUEST_MB` (default 8): declared or streamed, anything bigger gets 413 before it is read (`main.py`) |
| 7 | A 6000 x 6000 picture (36 megapixels, a few KB as PNG) as profile photo | Accepted and fully unpacked (~150 MB of memory per upload) - Pillow only warns below 2x its limit | The pixel count is checked from the file header BEFORE decoding; over 25 megapixels is refused (`routers/profile.py`) |
| 8 | Registering many Web Push addresses, garbage keys, pressing "send a test" repeatedly | No limit per person (one notification could fan out to thousands of addresses), keys not checked, test button unlimited | Max 5 newest devices per person, keys must be base64url, 3 tests a minute (`routers/notifications.py`) |
| 9 | A forged `school_id` on the leave list as an HOD | Ignored silently (returned his own school) - not a leak, but inconsistent | Now the same 403 as every other screen (`routers/leave.py`) |
| 10 | Response headers | The test server showed `server: uvicorn`; no HSTS for production; no CSP for the website | Tests run with `--no-server-header` like the launcher; `APP_ENV=production` adds HSTS; COOP + `X-Permitted-Cross-Domain-Policies` added; production build gets a Content-Security-Policy |

## Tried -> already safe (nothing to fix)
- **Tokens**: `alg=none` (any case), one changed signature character, edited role / user id / expiry with the old signature, unknown key id, garbage, 5000-character token -> always 401.
- **Sessions**: replay after logout (access token AND cookie dead at once), session fixation (a planted cookie is never adopted), lock-out after repeated wrong passwords, the same message for "unknown email" and "wrong password".
- **IDOR**: a schema-driven attacker built a VALID request for every route that takes an id (81 requests) with every id pointing at another school -> all refused, and a fingerprint of the victim school was identical afterwards. A teacher of another school: same. Real-school ids, read-only: all 403/404.
- **Role escalation**: teacher -> role / school fields refused (422); HOD -> cannot create HODs (403) or admins (422); HOD -> admin account not even visible (404); "mark absent" with someone else's id only ever marks yourself.
- **SQL injection**: 8 payloads (incl. `UNION SELECT` from auth.users and `pg_sleep`) in every query parameter of every list screen -> no error, no delay, nothing changed (every query is parameterised).
- **Stored XSS / formula injection**: `<script>`, `<img onerror>`, `=HYPERLINK(...)`, `=cmd|...` as names -> stored as text, sent as JSON with `nosniff`, shown as text by React, escaped in PDFs, kept as TEXT (never a formula) in every Excel export.
- **Excel uploads**: a picture renamed `.xlsx`, a zip bomb (60 MB of zeros), `.xlsm`, `.xls`, empty file, CSV text, 1200 rows, 3 MB file, formulas -> all refused or read as plain values.
- **Photos**: decompression bomb (81 MP), SVG with script, HTML named `.jpg`, 6 MB file -> refused; a normal photo is re-encoded and its hidden camera data removed.
- **Web Push SSRF**: `http://127.0.0.1`, `file://`, `javascript:`, cloud metadata address, look-alike hosts -> refused.
- **Supabase data API on ALL 42 tables**: nobody signed in reads nothing; teacher / HOD / admin cannot insert, update or delete anything; a teacher reads nothing of another school; GraphQL and RPC doors shut.
- **CORS / origin / host**: foreign origins (incl. `localhost:5173.evil.example`, `null`, another port, https variant) get no CORS header and 403 on writes; a foreign Host header -> 400.
- **AI prompt injection** (re-test): "ignore your rules", fake system prompts, JSON pretending to be a function call -> refused; nothing outside the fixed function list can ever run.
