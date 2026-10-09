# PROGRESS NOTES — TimeTablePro (checkpoint)

**We are in:** **PHASE 1** (data-setup screens) — **everything is built and tested; only the final hand-over (silent start, backup, report) is left / was just done.**
**Source of truth:** `TIMETABLE_MASTER_PLAN.md` (**V9**). The real college data (`COLLEGE_SEED_DATA.md` V3) IS SEEDED since 2026-10-09 (see section 00) - do not seed twice; `python -m scripts.seed_college` is idempotent.

> Hinglish: Phase 1 ka backend, saari screens aur tests ban chuke hain. Combined sessions bhi bane aur asli School of IT ka data seed ho chuka hai.
> Ab user ke review/approval ka wait hai; uske baad Phase 2.

## 00. COMBINED SESSIONS + REAL DATA SEEDED (2026-10-09)  <- latest
Master plan is now **V9**, seed file **V3 FINAL** (read both before anything else).
- **Combined sessions (plan V9, Section 8)**: one locked slot can cover SEVERAL classes together, one teacher, counted as ONE session. Migration `006_combined_slots.sql`:
  `fixed_slots.combined_group_id` (rows of one session share it; one row per covered class so every class stays occupied) + the teacher exclusion constraint now ignores rows of the SAME session (`(coalesce(combined_group_id,id)) WITH <>`).
  API: `POST /api/fixed-slots` takes `semester_ids: [...]` (or the old single `semester_id`); `PATCH` can change the class set (`semester_ids`); `id` of a combined slot = its group id; list returns `combined`, `semester_ids`, `classes[]`. Teacher/co-teacher come from each class's PE/SOL assignment (must agree, else "different teachers" - choose one). Workload, deactivate-check and timing warnings count a combined session ONCE. Screen: Fixed Slots dialog has class checkboxes + "Combined session" box, grid cells/table/cards show "Combined - N classes". Tests: `test_p1_api_e.py` (24).
- **Seeded** with `python -m scripts.seed_college` (in-process API call as the real admin = validation + audit log; idempotent) and checked with `python -m scripts.seed_college --verify` (read-only report, ALL PASS):
  Odd 2026 (01 Jul - 30 Nov 2026), Mon-Sat college week, day-wise schedules incl. OFF days, 16 accounts (1 HOD + 13 regular + 2 visiting Mon-Wed; none for Prof. Anurag Dixit; dummy `@school-it.test` emails), 89 subjects in 8 classes (labs = own 1-credit subject, 4 B.Sc. track groups, AEC 3 unassigned, Minor 7 co-teacher), B.Sc. Sem 5 + 7 marked "no PE/SOL", 5 fixed sessions (3 combined).
  Temporary passwords: `seed_accounts.txt` in the project root (git-ignored, excluded from backups by `tools/make_backup.py`, never printed in chat).
- **Decisions the seed file left open (flag in every report):** (1) SOL III: B.Sc. Sem 3 and M.Sc. SOL III were BOTH Thu P3+P4 with Ms. Subarna Ghosh -> seeded as ONE combined session of 3 classes (same logic as the PE note). (2) Semester hours not in the seed -> 15 h per credit, lab block 30, PE/SOL 30 (editable). (3) The visiting-faculty form requires a weekly-hours limit -> 18 (= 3 days x 6 periods = no extra limit). (4) OJT 1 (6 cr) and the M.Sc. Internships (4 cr) are NOT subjects (no weekly slots); credit totals in the seed file include them. (5) M.Sc. "Elective-I/II/III/V/VI" have only ONE running option -> flagged elective but not put in an elective group (a group needs 2+ options). (6) "3 theory + 1 lab block" subjects = a theory subject + a `... LAB` subject (1 credit). (7) External Faculty = ONE account for all track majors (it teaches both parallel track subjects and 4 classes: Phase 3 must treat it as several people or split the account).
- **NEVER run `e2e_setup.py cleanup` any more** (scratchpad): it deletes the active term and resets timing. Use `e2e_admin.py create|cleanup` (admin login only). `tools/cleanup_test_data.py --reset-*` flags also change REAL settings.

## 0. SUPABASE MOVED TO MUMBAI (2026-10-09, earlier the same day)
The database now lives in a NEW free Supabase project `timetablepro-mumbai` (ref `riiuhmuieheaidmlalah`, South Asia / Mumbai, pooler host `aws-0-ap-south-1`). The old Tokyo project (ref `ggvsazqdvfqdhiainxzq`) was DELETED by the user on 2026-10-09 - there is no going back, and nothing is needed from it.
- Speed (scratchpad `bench.py`): one query 155 ms -> 58 ms, opening a connection 1050 ms -> 495 ms, Supabase Auth HTTPS call 220 ms -> 95 ms.
- Rebuilt on the new project: migrations 001-005 (School of IT, 3 courses, 13 semesters, timing 10:00-16:50 / 60+5 min / Mon-Fri, lunch 13:10-13:40, 34 India holidays) + admin account (`python -m scripts.seed_admin`; same email, NEW temp password, must change at first login). Everything else is empty (no terms/subjects/assignments/slots/audit rows).
- Verified: health check OK, live login OK (wrong password 401, admin login 200, JWT accepted via the new JWKS ES256, logout kills the token), Auth settings reachable.
- The user once pasted `.../rest/v1/` after the Project URL; the correct form is just `https://<ref>.supabase.co` (fixed in `.env`).
- Public sign-up is OFF on the new project (the user switched it off; checked with `auth/v1/settings` -> `disable_signup=true`, and `test_public_signup_is_closed` passes).
- Full backend test suite on the Mumbai database: **338 passed** (337 in one session in 11 min 28 s - half the old 22 min - plus `test_public_signup_is_closed` after sign-up was switched off). Note: that one test needs the test server started by an earlier test; run the whole `tests/test_api_integration.py` file, not the single test.
- The 4 `# OLD_SUPABASE_*` backup lines were removed from `.env` after verification. `.env` now holds only the new project's 4 values.
- Admin temp password: shown once to the user in the chat summary (never stored in a file). First login forces a new password.

## 1. Project quick facts
- Folder `D:\Timetable Management` — `backend\` (Python FastAPI), `frontend\` (React + Vite), `tools\`, `start.bat`/`stop.bat`,
  shortcuts `Start TimeTablePro.lnk` / `Stop TimeTablePro.lnk` (silent launcher — do NOT break it; `TT_NO_BROWSER=1` = start without opening a tab, `TT_NO_POPUP=1` = no error popup).
- Python: `backend\.venv\Scripts\python.exe`. DB = Supabase (Mumbai `ap-south-1` session pooler, port 5432). Never print keys from `.env`.
- Database changes: `cd backend` → `.\.venv\Scripts\python.exe -m scripts.migrate` (applied up to **006**).
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
- Seeding of `COLLEGE_SEED_DATA.md` V3 is DONE (section 00).
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
