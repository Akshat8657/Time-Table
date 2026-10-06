# PROGRESS NOTES — TimeTablePro (checkpoint)

**We are in:** **PHASE 1** (data-setup screens) — **everything is built and tested; only the final hand-over (silent start, backup, report) is left / was just done.**
**Source of truth:** `TIMETABLE_MASTER_PLAN.md` (**V8**). Real college data to load LATER (after Phase 1 is approved): `COLLEGE_SEED_DATA.md` — **do not seed it yet.**

> Hinglish: Phase 1 ka backend, saari screens aur tests ban chuke hain. Ab sirf hand-over bacha hai: app silently start karke browser kholna,
> `Phase1.zip` backup, aur PHASE 1 REPORT. Seed data abhi load NAHI karna (Phase 1 approve hone ke baad alag step).

## 1. Project quick facts
- Folder `D:\Timetable Management` — `backend\` (Python FastAPI), `frontend\` (React + Vite), `tools\`, `start.bat`/`stop.bat`,
  shortcuts `Start TimeTablePro.lnk` / `Stop TimeTablePro.lnk` (silent launcher — do NOT break it; `TT_NO_BROWSER=1` = start without opening a tab, `TT_NO_POPUP=1` = no error popup).
- Python: `backend\.venv\Scripts\python.exe`. DB = Supabase (Tokyo pooler, session mode port 5432). Never print keys from `.env`.
- Database changes: `cd backend` → `.\.venv\Scripts\python.exe -m scripts.migrate` (applied up to **005**).
- Tests: `cd backend` → `.\.venv\Scripts\python.exe -m pytest -q`. They hit the REAL Supabase and are slow (≈ 0.5-2 s per request).
  **Run big test files in the background and read the output file** (output is buffered). Supabase allows ~30 password sign-ins per 5 minutes: never run the whole suite twice within 5 minutes.
- Test leftovers cleaner: `cd backend` → `.\.venv\Scripts\python.exe ..\tools\cleanup_test_data.py` (flags `--reset-terms --reset-timing --reset-holidays` change REAL settings — only on purpose).
- Backup: `python tools\make_backup.py Phase1` → `D:\TimetableBackups\Phase1.zip` (delete `frontend\public\__e2e__\` first if it exists!).

## 2. Rules that always apply
- **Single-agent only.** Plan wins over prompts. All logic in Python. Real keys only in `.env`. Explain to the user in simple Hinglish (non-coder). UI text English.
- After each phase: self-test → backup → report in the EXACT Section-20 format → STOP and wait.
- No room management. HOD is also a teacher. HOD locked to own school (API + RLS).
- Never log in as the real admin in tests. Throw-away accounts: `t-*@selftest.invalid` (pytest) and `e2e-*@selftest.invalid` (browser test) — always delete afterwards.
- Test school names: pytest uses `ZZ Test School ...` (the test harness DELETES every school with that prefix!); the browser test uses `E2E Demo School` (never name it ZZ...).

## 3. Decisions of the user for V8 (all built)
1. Double blocks: "back-to-back" = 2 consecutive periods; the short break or LUNCH in between is fine (SOL III = P3 + P4). A gentle note is shown, nothing is refused.
2. Defaults match the real college: **10:00-16:50**, 60 min lectures, 5 min breaks → 6 periods; School of IT lunch **13:10-13:40** (new schools get 13:10-13:40 too). All editable.
3. **Day-wise schedule per semester**: each weekday has its own end time or is fully OFF (`semester_days` table; replaces the single early-release time).
4. Labs: no credits-vs-hours warning ever (typically 1 credit, always one 2-period block).
5. Assignment = primary teacher + optional **co-teacher** (both appear on timetable; clash/protection checks apply to both).
6. (added after `COLLEGE_SEED_DATA.md` V2) A class can be marked **"no PE / SOL"** (`semesters.no_pe_sol`, migration 005; B.Sc. Sem 5 and 7 have none):
   the Fixed Slots checklist stops nagging, the dashboard counts only the other classes, and a PE/SOL slot on such a class is refused (422). Cannot be marked while PE/SOL slots exist (409).

## 4. What is DONE
- **Migrations 002-005** (tables, RLS, seeds, `special_kind`, `semester_days`, `co_teacher_id`, real defaults, `no_pe_sol`). RLS on every table, read-only for logged-in users, all writes via backend.
- **Backend** (`backend\app\`): routers `terms, structure, users, profile, subjects, assignments, timing, fixed_slots, calendar, imports, dashboards`;
  services `grid, terms, timing, accounts, excel`; `audit.py`, `scope.py` (HOD school lock), `validation.py`, `pgerrors.py`, `data\india_holidays.py`.
- **Speed layer** (found: every DB round trip cost ≈ 0.5 s): statement cache + no pool reset (query ≈ 150 ms), warm pool + keep-alive, pool acquire timeout (no more silent hangs),
  in-memory **session cache** (`SESSION_CACHE_SECONDS`, default 5, cleared at once on logout/deactivate/reset/password change; tests use 0), parallel reads in list endpoints, slow-request log.
- **Frontend** (`frontend\src\`): Active Term (+ semester dates), College Setup (admin) / School Structure (HOD read-only), Teachers (+ Excel import, one-time temp passwords),
  My Profile (photo upload), Subjects & Credits (+ elective groups, Excel import), Assignment (+ co-teacher + workload), Timing (college hours, live period-grid preview, lunch, day-wise class schedule),
  Fixed Slots (weekly grid with PE/SOL double blocks, live "is it possible?" check), Holidays & Events, live dashboards (term, setup checklist, upcoming).
- **Tests**: `test_p1_units.py` (47), `test_p1_api_a.py` (41), `test_p1_api_b.py` (97, all pass), `test_p1_api_c.py` (import, audit, "every route protected", school-lock matrix, direct-REST RLS, null fields),
  `test_p1_api_d.py` (class without PE/SOL), `test_p1_session_cache.py` (second server on port 8002).
- Browser (DOM-level, window hidden so no screenshots) check done for: admin term/structure/teachers/subjects(+import)/assignment/timing/fixed slots/holidays, HOD dashboard + all screens, teacher profile + photo.
  Bugs found and fixed by it: `null` for optional fields crashed (fixed + regression tests), hidden table labels widened the page (fixed).

## 5. What is LEFT (in order)
Nothing in Phase 1 except waiting for the user's review. NEXT (only after the user approves Phase 1): seed `COLLEGE_SEED_DATA.md` (separate step), then Phase 2.
(Final hand-over steps: full test run green -> silent start with ONE browser tab -> `python tools\make_backup.py Phase1` -> secret scan -> report.)

## 6. Notes for the report / next steps (not built, by decision)
- Seeding `COLLEGE_SEED_DATA.md` is a separate step after approval. It will need: working days **Mon-Sat** (Saturday OFF per class; OJT days OFF), Active term Odd 2026, many ❓ items to ask the user.
- Still open from the seed file: subjects shared across several classes / combined classes (e.g. SOL for all Sem 1 classes) are not modelled (a subject belongs to one class); co-teachers are supported.
- Audit log viewing screen = Phase 6 (table + recording exist now). Holiday lists = official DoPT lists (lunar dates editable).
- Known limits: photo/Excel limits (5 MB / 2 MB, 100 teachers per import); one teacher + one co-teacher per subject.

## 7. Decisions taken in Phase 1 (do not undo)
- Active term is college-wide; admin AND HOD can change it; none is pre-selected; old terms stay as history. "Class" = course + semester.
- Only the active term's odd/even semesters appear/can be used (409 otherwise). Assignments, locked slots, semester dates belong to a term.
- PE/SOL: always a 2-period block; credits don't decide their count (`subjects.special_kind`).
- HOD school lock: other `school_id` → 403; foreign ids → 404; HOD cannot manage other HODs/admin; nobody manages admin accounts via the users API.
- New accounts: random temp password shown ONCE (never stored/logged), first login forces change; deactivate = flag + Supabase ban + sessions revoked; reset = new temp password + sessions revoked.
- Teachers can edit only name/phone/specialization/subjects they can teach/photo. Photos re-encoded to 256×256 JPEG in the DB.
- Deactivating a person / removing a subject from their list is blocked while they (or as co-teacher) have assignments or locked slots in the active term.
- Direct REST writes are denied for every role; audit rows are written in the same transaction as the change.
