# PROGRESS NOTES — TimeTablePro (checkpoint)

**Last saved:** 2026-10-06 (checkpoint, work paused by the user)
**We are in:** **PHASE 1** (data-setup screens) — **backend is written, frontend is NOT started.**
**Source of truth:** `TIMETABLE_MASTER_PLAN.md` (V4). Real college data to load later: `COLLEGE_SEED_DATA.md`.

> Hinglish summary: Phase 0 poora ho chuka hai. Phase 1 ka **backend (database + API) likha ja chuka hai** aur zyadatar tests pass hain.
> **Screens (frontend), baaki tests, Excel/E2E check, backup zip aur Phase 1 report abhi baaki hain.**

---

## 1. How the project is organised (quick)

- Folder: `D:\Timetable Management` — `backend\` (Python FastAPI), `frontend\` (React + Vite), `tools\`, `start.bat` / `stop.bat`,
  shortcuts `Start TimeTablePro.lnk` / `Stop TimeTablePro.lnk` (silent launcher — do NOT break it).
- Python: `backend\.venv\Scripts\python.exe` (Python 3.13). Database = Supabase (project ref in memory notes), reached from `.env` (never print keys).
- Run all tests: `cd backend` then `.\.venv\Scripts\python.exe -m pytest -q`
  (needs the real internet/Supabase; **Supabase allows ~30 password sign-ins per 5 min per IP → never run the full suite twice within 5 minutes**).
- Apply database changes: `cd backend` then `.\.venv\Scripts\python.exe -m scripts.migrate` (already applied up to **003**).
- Phase backup: `python tools\make_backup.py Phase1` → `D:\TimetableBackups\Phase1.zip`.
- If a test run is stopped half-way, clean its leftovers: `cd backend` then `.\.venv\Scripts\python.exe ..\tools\cleanup_test_data.py`
  (safe by default; flags `--reset-terms / --reset-timing / --reset-holidays` change REAL settings — use only on purpose).

## 2. Rules that always apply (from the user)

- **Single-agent only** (no Workflow / Agent tool). Plan wins over prompts. All logic in Python. Real keys only in `.env`.
- Explain to the user in **simple Hinglish, short numbered steps** (user is a non-coder). UI text in English.
- After each phase: self-test → backup zip → report in the EXACT Section-20 format
  (`=== PHASE X REPORT ===`, `✅ Kya kya bana:`, `🧪 Maine kya test kiya + result:`, `🔐 Security me kya lagaya:`,
  `⚠️ Koi problem ya pending cheez:`, `📦 Backup:`, `▶️ Ab aage kya:`) → then STOP and wait.
- No room management anywhere. HOD is also a teacher. HOD is locked to his own school (API + RLS, not only UI).
- When Phase 1 is finished: **start the app silently and open http://localhost:5173 for the user** (one tab), then backup, then report.
- Never log in as the real admin in tests. Test accounts are `t-*@selftest.invalid` / `e2e-*@selftest.invalid` and must be deleted afterwards.

## 3. What is DONE

### Phase 0 (finished earlier, working — do not redo)
Login (email + password, no public signup), forced password change, sessions (8 h idle, remember-me), cookie consent, 3 role dashboards,
security base, silent launcher (zero visible windows), 80 backend tests passing, backup `D:\TimetableBackups\Phase0.zip`.

### Phase 1 — database (applied to Supabase)
- `backend\migrations\002_phase1.sql` — tables: `academic_terms`, `courses`, `semesters`, `term_semesters`, `college_settings`, `school_timing`,
  `subjects`, `elective_groups`, `teacher_subjects`, `assignments`, `fixed_slots`, `calendar_entries`, `calendar_entry_classes`, `profile_photos`, `audit_log`.
  RLS on all tables (read-only for logged-in users, own school only; admin reads all; `audit_log` admin-only; all writes go through the backend).
  DB-level safety: composite same-school foreign keys; `assignments (teacher_id, subject_id) → teacher_subjects` (a teacher can't get a subject he can't teach);
  exclusion constraints stop overlapping locked slots (per class and per teacher).
  Seed: School of IT courses (B.Sc. IT 7 sems, M.Sc. IT 3, M.Sc. AI 3), `college_settings` default (10:00–17:00, 60 min, 5 min break, Mon–Fri),
  School of IT lunch 13:00–13:45, **34 preloaded India holidays (2026 + 2027)**. No term is active yet (user will choose).
- `backend\migrations\003_subject_special_kind.sql` — `subjects.special_kind` (PE / SOL / null).

### Phase 1 — backend code (all written; app imports fine)
- Helpers: `app\audit.py` (audit log written in the same transaction), `app\scope.py` (HOD school-locking: 403 for another `school_id`, 404 for foreign ids),
  `app\validation.py`, `app\pgerrors.py` (database errors → friendly messages; handler registered in `main.py`),
  `app\security\passwords.py` (`generate_temp_password`), `app\data\india_holidays.py`.
- Services: `app\services\grid.py` (period-grid builder), `terms.py`, `timing.py` (+ slot rules), `accounts.py` (create/reset/deactivate via Supabase admin API), `excel.py`.
- Routers (all registered in `main.py`): `terms`, `structure`, `users`, `profile`, `subjects` (+ elective groups & same-credits warning), `assignments`,
  `timing`, `fixed_slots`, `calendar`, `imports` (Excel templates + dry-run + import of valid rows only).
- `requirements.txt` additions: `openpyxl`, `Pillow`, `python-multipart`.

### Phase 1 — tests written
- `tests\test_p1_units.py` — 41 tests (grid, slot rules, validators, Excel reading) → **all pass**.
- `tests\test_p1_api_a.py` — 41 tests (terms, structure, accounts + temp-password login/deactivate/reset, profile limits, photos) → **all pass**.
- `tests\test_p1_api_b.py` — written (subjects, electives, assignments, timing, fixed slots, holidays, semester dates).
  Last runs: **about 51 tests passed**, run was stopped by the user before it finished (see Known issues).
- `tests\conftest.py` — new `p1` fixture (own test school "ZZ Test School P…", test HOD + teacher, snapshot + restore of term / college timing / holiday list),
  helper `mail_for()`; `Factory.create(..., school_id=)`.
- Bugs found by tests and already fixed: (1) visiting-teacher hour-limit warning counted load before commit (now after commit);
  (2) test helper clash in `subj()`.

## 4. What is PENDING in Phase 1

1. **Finish & pass `test_p1_api_b.py`** (see Known issues).
2. **Write test part C**: Excel import (teachers + subjects: dry-run, row-wise errors, only valid rows imported, credentials returned once),
   audit-log entries (no passwords inside), direct-REST RLS checks for all new tables (anon denied; HOD sees own school only; no writes for anyone; `audit_log` admin-only;
   `profile_photos` unreachable), and a "HOD school-locking matrix" over every endpoint.
3. Re-run the **old 80 Phase 0 tests** once (conftest was changed; not yet re-verified) — wait ≥ 5 minutes between big runs.
4. **Frontend (nothing built yet):** term context + header chip; UI parts (Modal, ConfirmDialog, Select/Date/Time fields, DataTable + SearchBox, Badge, Tabs, Toast,
   one-time credentials dialog, authed image); pages: Active Term (+ semester start/end dates), College Setup (admin edit) / School Structure (HOD read-only),
   Teachers (list/create/edit/deactivate/reset password/import), My Profile (teacher), Subjects & Credits (+ elective groups), Assignment,
   Timing Setup (admin) / School Timing (HOD) with period-grid preview, Fixed Slots weekly grid (PE/SOL), Holidays & Events, Excel import dialogs.
   Update `frontend\src\lib\nav.ts` (new items, `live` flags) and `App.tsx` (route → page mapping). Polished UI, searchable lists.
5. Browser E2E of the new screens with throw-away `e2e-*@selftest.invalid` accounts, then delete them.
6. Start the app silently + open the browser for the user → `python tools\make_backup.py Phase1` (+ secret scan of the zip) → **PHASE 1 REPORT** → STOP.
7. Update memory notes (`C:\Users\aksha\.claude\projects\D--Timetable-Management\memory\`) after Phase 1.

## 5. VERY NEXT STEP when we resume

1. Read this file, then `TIMETABLE_MASTER_PLAN.md` (fully) and `COLLEGE_SEED_DATA.md`.
2. Ask the user (short Hinglish question) how to handle the **new findings in section 7 below** (they change a few Phase 1 rules).
3. Run the part-B tests **in the background** and poll the output file (they are slow, see below):
   `cd backend` → `.\.venv\Scripts\python.exe -m pytest tests/test_p1_api_b.py -x -v -o faulthandler_timeout=120 > run_b.txt 2>&1` (run in background, then read `run_b.txt`).
4. Then follow section 4 in order (tests part C → frontend → E2E → start app → backup → report).

## 6. Known issues / things to watch

- **Part-B test run looked stuck** once (no new server log lines for many minutes around the fixed-slot tests) and later ran again to ~51 passing tests
  before it was stopped. Unclear whether it is just slow (every request makes several round trips to the remote database) or a real hang.
  Suspect to check first: `app\db.py` `pool().acquire()` has **no timeout** — if a connection ever leaks the next request would wait forever.
  Consider adding `timeout=` to `acquire()` and checking `evaluate()` in `routers\fixed_slots.py` / `problems_for_term()` for nested pool use.
- A killed test run skips its own clean-up → use `tools\cleanup_test_data.py`. State right now: **clean** (no test accounts, no test schools, no terms,
  34 holidays, college timing at defaults, no audit rows). The real admin account exists and was never used by tests.
- Phase 0 test files were not re-run after the Phase 1 `conftest.py` change.

## 7. IMPORTANT new input: `COLLEGE_SEED_DATA.md` (real School of IT data) — conflicts with current Phase 1 code

Found at checkpoint time. Decisions needed when we resume (do NOT silently change; ask the user, then adapt code + tests):

1. **Real timing:** college 10:00–16:50, lecture 60 min, break 5 min, **lunch 13:10–13:40 (30 min)**. Our seeded defaults are 10:00–17:00 and lunch 13:00–13:45.
   The grid builder already gives the exact real periods with these values (P1 10:00 … P3 12:10–13:10 | lunch | P4 13:40–14:40, P5 14:45–15:45, P6 15:50–16:50).
   → Set these values when seeding (or change the defaults).
2. **Double blocks AROUND lunch must be allowed** (e.g. SOL III = Thursday P3+P4 with lunch between). Current code **refuses** a double block across lunch
   (`services\timing.py` `slot_problems`, `services\grid.py` `is_continuous`) and the tests expect that refusal
   (`test_p1_units.py`: across-lunch + continuity tests; `test_p1_api_b.py`: across-lunch, "slots become flagged…", PATCH across lunch).
   → Change the rule: only refuse slots outside the day, after early-release, on non-working days, or overlapping; show a gentle note when a block runs around lunch.
3. **Day-wise end times and off days per semester** (e.g. B.Sc. Sem 5 ends 2:40 on Mon–Wed; Sem 7 has OJT Thu–Sat = no classes; Saturday off for most).
   Current model has only ONE `early_release_time` per semester. → needs a small extension (per-semester per-day end time / off-day), new migration `004`, API + screen.
4. **Lab credits:** "its LAB = 1 double block per week" and labs are separate 1-credit rows. Current subject hint says "labs should have even credits" → would wrongly warn on every 1-credit lab. Remove/reword.
5. **Shared teachers / combined classes:** several subjects list two teachers ("Maitri Hingu / Prof. Anurag Dixit") and some subjects/slots are shared across classes
   (e.g. SOL combined for all Sem 1 classes, "shared" subjects). Current model = one teacher per subject per term and a subject belongs to one class. → ask the user how to model.
6. Other: Active term = **Odd 2026**, default dates 01 Jul 2026 – 30 Nov 2026 (❓ not confirmed); HOD = Dr. Papri Das; dummy teacher emails `@school-it.test`;
   many ❓ items must be confirmed with the user before seeding. Seeding happens AFTER Phase 1 is complete.

## 8. Decisions taken in Phase 1 (so a fresh session does not undo them)

- Active term is **college-wide**; both **admin and HOD** can change it; no term is pre-selected; old terms stay as history; exactly one is active.
- "Class" = a `semesters` row (course + number); divisions can be added later. Only the active term's odd/even semesters appear in lists; creating/editing for other parity → 409.
- Assignments, locked slots and semester dates belong to a **term**; subjects do not. One teacher per subject per term.
- Credits = weekly lectures. **PE/SOL**: always forced to a 2-period double block; credits don't decide their count; `subjects.special_kind` marks them.
- HOD school-lock: explicit other `school_id` → **403**; foreign entity id → **404**. HOD can't manage other HODs/admin; nobody can manage admin accounts via the users API.
- New accounts: random temporary password (12 chars, shown **once**, never stored or logged); no e-mail sending; first login forces a password change.
  Deactivate = DB flag + Supabase ban + sessions revoked; reactivate unbans; reset-password = new temp password + all sessions revoked.
- Teachers can edit only name, phone, specialization, subjects they can teach, photo (extra fields → 422). Photos are re-encoded to 256×256 JPEG in the database.
- Deactivating a teacher, or removing a subject from his list, is blocked while he has assignments / locked slots in the active term.
- All writes go through the backend; direct REST writes are denied for every role (no write grants).
- Audit log recorded for every create/edit/delete from Phase 1 on (viewing screen = Phase 6).
- Holiday list = official DoPT gazetted lists for 2026/2027 (lunar dates are editable); admin can restore missing ones (`/api/calendar/restore-india-list`).

## 9. Test accounts / data right now

- None left. (Throw-away accounts `t-…@selftest.invalid` are created and removed by the tests themselves.)
- Real admin: created in Phase 0 (email in `.env` as `SEED_ADMIN_EMAIL`); its temporary password was shown once in the Phase 0 report. Not stored here.
