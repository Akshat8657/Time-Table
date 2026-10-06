"""Removes the throw-away things the self-tests create, after a test run that was stopped half-way.

    cd backend
    .\.venv\Scripts\python.exe ..\tools\cleanup_test_data.py

Always removed (safe): accounts like t-*@selftest.invalid / e2e-*@selftest.invalid, schools named 'ZZ Test School*',
their courses/subjects/slots, holidays titled 'ZZ ...', and audit rows written by those test accounts.

Only when you ask for it (these change REAL settings, so they are OFF by default):
    --reset-terms    deletes ALL academic terms (and with them the assignments / locked slots of those terms!)
    --reset-timing   puts the college timing back to the Phase 1 defaults (10:00-17:00, 60 min, 5 min break, Mon-Fri)
    --reset-holidays puts the standard India holiday list back to the official 34 entries

Prints only counts - never any secret.
"""
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import get_settings  # noqa: E402
from app.data.india_holidays import INDIA_HOLIDAYS, note_for  # noqa: E402
from app.db import connect_one  # noqa: E402
from app.supabase_auth import SupabaseAuth  # noqa: E402


async def main() -> None:
    conn = await connect_one()
    auth = SupabaseAuth(get_settings())
    try:
        ids = [r["id"] for r in await conn.fetch(
            "select id from auth.users where email like 't-%@selftest.invalid' or email like 'e2e-%@selftest.invalid'")]
        await conn.execute("delete from public.calendar_entries where created_by = any($1::uuid[]) or title like 'ZZ %'", ids)
        await conn.execute("delete from public.audit_log where actor_email like '%@selftest.invalid'")
        # courses first: a school that still owns courses cannot be removed (this also removes subjects, assignments, slots)
        await conn.execute("delete from public.courses where school_id in (select id from public.schools where name like 'ZZ Test School%')")
        for uid in ids:
            await auth.admin_delete_user(str(uid))
        await conn.execute("delete from public.schools where name like 'ZZ Test School%'")
        print("removed test accounts:", len(ids))

        if "--reset-timing" in sys.argv:
            await conn.execute("update public.college_settings set college_start='10:00', college_end='17:00', lecture_minutes=60, break_minutes=5, days_per_week=5")
            print("college timing reset to the Phase 1 defaults")
        if "--reset-terms" in sys.argv:
            await conn.execute("delete from public.academic_terms")
            print("all academic terms deleted")
        if "--reset-holidays" in sys.argv:
            await conn.execute("delete from public.calendar_entries where is_preloaded and school_id is null")
            for title, d in INDIA_HOLIDAYS:
                await conn.execute(
                    "insert into public.calendar_entries (school_id, kind, title, date_from, date_to, full_day, note, is_preloaded) "
                    "values (null,'holiday',$1,$2,$2,true,$3,true)", title, d, note_for(d))
            print("standard India holiday list restored")

        for label, sql in (
            ("schools", "select count(*) from public.schools"), ("courses", "select count(*) from public.courses"),
            ("terms", "select count(*) from public.academic_terms"), ("holidays", "select count(*) from public.calendar_entries"),
            ("audit rows", "select count(*) from public.audit_log"), ("profiles", "select count(*) from public.profiles"),
            ("subjects", "select count(*) from public.subjects"),
        ):
            print(f"  {label}: {await conn.fetchval(sql)}")
    finally:
        await auth.aclose()
        await conn.close()


asyncio.run(main())
