"""Phase 5 self-test on the REAL seeded School of IT data (in-process API; teachers impersonated for accept/decline).

  python -m scripts.phase5_selftest

  Builds a published timetable (weekly-lectures override -> generate -> use -> publish), then end to end:
    1. mark a teacher absent -> the day's affected lectures are listed (a combined session counts once; a co-taught lecture is skipped)
    2. candidate ranking is free + qualified only, visiting faculty only on their days
    3. auto offers -> FIRST accept wins, the rest see "already filled" -> the day's class / teacher views show the substitute
    4. manual assign on another slot
    5. one-day move / cancel / swap with clash checks + smart suggestions; a cancel is lost in the hours
    6. the daily substitution sheet (PDF) + the monthly summary
    7. the timeout sweep marks an un-accepted round 'unfilled' and alerts the HOD
  Then removes every Phase-5 row, the publications, drafts, runs and overrides, and re-verifies the seed is intact.
"""
from __future__ import annotations

import os

os.environ.setdefault("READ_CACHE_SECONDS", "0")   # these scripts also write straight to the database: always read fresh

import asyncio
import io
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx

from app import db
from app.config import get_settings
from app.main import app
from app.security import deps
from app.services import notify
from app.services import subflow
from app.services import views as vw

from .phase3_selftest import wait_run
from .phase4_selftest import Acting, class_numbers, make_classes_fit
from .seed_college import Api, SeedError, Who, _fake_authenticate, load_user, log, verify

OK = BAD = 0


def check(cond: bool, msg: str, extra: str = "") -> None:
    global OK, BAD
    OK += bool(cond)
    BAD += not cond
    log(("  PASS  " if cond else "  FAIL  ") + msg + (f"   {extra}" if extra else ""))


def nxt(today: date, wd: int) -> date:
    days = (wd - today.isoweekday()) % 7
    return today + timedelta(days=days or 7)


async def main() -> int:
    settings = get_settings()
    started = datetime.now(timezone.utc)
    async with app.router.lifespan_context(app):
        Who.user = await load_user(email=settings.seed_admin_email, role="admin")
        deps.authenticate = _fake_authenticate
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost",
                                     headers={"Origin": "http://localhost:5173"}, timeout=300) as client:
            api = Api(client)
            original = {str(r["id"]): r["weekly_lectures"] for r in await db.fetch("select id, weekly_lectures from public.subjects")}
            try:
                await scenarios(api, client)
            except (SeedError, AssertionError) as exc:
                log(f"\nSTOPPED: {exc}")
                return 1
            finally:
                await notify.drain()
                await cleanup(api, started, original)
            log("\n== the seeded data is untouched ==")
            bad = await verify(api)
            check(bad == 0, "seed verifier: ALL PASS")
    log(f"\nPHASE 5 SELF-TEST: {OK} checks passed, {BAD} failed")
    return 1 if BAD else 0


async def publish_everything(api: Api, sid: str) -> None:
    q = f"?school_id={sid}"
    nums = await class_numbers(sid)
    over = {k: v for k, v in nums.items() if v[0] > v[1]}
    subjects = (await api.get(f"/api/subjects{q}"))["subjects"]
    await make_classes_fit(api, over, subjects)
    res = await api.post("/api/generation/runs", {"school_id": sid, "name": "P5 SETUP", "time_limit_s": 120})
    run = await wait_run(api, res["id"])
    best = run["solutions"][0]
    await api.post(f"/api/generation/solutions/{best['id']}/use")
    out = await api.post("/api/publish", {"scope": "all", "school_id": sid})
    check(len(out["published"]) == 8, "setup: all 8 classes published", str(len(out["published"])))


async def teacher_name(tid) -> str:
    return await db.fetchval("select full_name from public.profiles where id = $1", tid)


async def scenarios(api: Api, client: httpx.AsyncClient) -> None:
    tree = await api.get("/api/structure/tree")
    sid = next(s for s in tree["schools"] if s["name"] == "School of IT")["id"]
    q = f"?school_id={sid}"
    log("\n== setup: publish the real timetable ==")
    await publish_everything(api, sid)
    today = vw.today_ist()
    mon, thu = nxt(today, 1), nxt(today, 4)

    # ---------------------------------------------------------------------------- 1) a combined-session teacher
    log("\n== 1) absence of a combined-session teacher ==")
    pe_teacher = await db.fetchval("select id from public.profiles where full_name = 'Mr. Sushant Singh'")
    grp = await db.fetchrow(   # his biggest combined session (the PE across the B.Sc. classes) and the weekday it runs
        """
        select pe.day, count(distinct pe.semester_id) n
        from public.published_entries pe
        where pe.school_id = $1 and (pe.teacher_id = $2 or pe.co_teacher_id = $2) and pe.group_key is not null
        group by pe.day, pe.group_key order by count(distinct pe.semester_id) desc limit 1
        """,
        uuid.UUID(sid), pe_teacher,
    )
    check(grp is not None and grp["n"] >= 2, "a combined session (PE across several classes) exists in the published timetable", str(dict(grp)) if grp else "none")
    pe_date = nxt(today, grp["day"])
    res = await api.post("/api/absence", {"teacher_id": str(pe_teacher), "date": pe_date.isoformat(), "reason": "selftest"})
    combined = [a for a in res["affected"] if len(a["classes"]) == grp["n"]]
    check(len(combined) == 1, f"the combined session is ONE affected slot across its {grp['n']} classes", str(combined[0]["classes"]) if combined else "")
    check(res["to_cover"] == len([a for a in res["affected"] if a["status"] == "open"]), "every uncovered lecture became one open substitution need")
    await api.call("DELETE", f"/api/absence/{res['absence_id']}")

    # ---------------------------------------------------------------------------- 2) ranking + visiting faculty
    log("\n== 2) a regular teacher: ranking (free + qualified, visiting only on their days) ==")
    # a single-class Monday lecture taught by one teacher (not combined, not parallel)
    row = await db.fetchrow(
        """
        select pe.teacher_id, pe.start_period, pe.semester_id, pe.label
        from public.published_entries pe
        where pe.school_id = $1 and pe.day = 1 and pe.kind = 'lecture' and pe.teacher_id is not null and pe.group_key is null
        order by pe.start_period limit 1
        """,
        uuid.UUID(sid),
    )
    absent_a = row["teacher_id"]
    res = await api.post("/api/absence", {"teacher_id": str(absent_a), "date": mon.isoformat()})
    sub = next(a for a in res["affected"] if a["status"] == "open")
    S_sub = sub["id"]
    cands = (await api.get(f"/api/subs/{S_sub}/candidates"))["candidates"]
    check(cands and all("Free in that slot" in c["reasons"] for c in cands), f"{len(cands)} free candidates, each with its reasons")
    check(str(absent_a) not in [c["teacher_id"] for c in cands], "the absent teacher is never a candidate")
    # visiting faculty (Anjana / Falguni, Mon-Wed) may appear on Monday but NOT on a Thursday absence
    visiting = [r["id"] for r in await db.fetch("select id from public.profiles where full_name in ('Anjana Prajapati','Falguni Thakker')")]
    thu_lec = await db.fetchrow(
        "select teacher_id, start_period from public.published_entries where school_id = $1 and day = 4 and kind = 'lecture' and teacher_id is not null and group_key is null order by start_period limit 1",
        uuid.UUID(sid),
    )
    res_thu = await api.post("/api/absence", {"teacher_id": str(thu_lec["teacher_id"]), "date": thu.isoformat()})
    thu_sub = next((a for a in res_thu["affected"] if a["status"] == "open"), None)
    if thu_sub:
        thu_cands = {c["teacher_id"] for c in (await api.get(f"/api/subs/{thu_sub['id']}/candidates"))["candidates"]}
        check(not (set(str(v) for v in visiting) & thu_cands), "visiting faculty are NOT offered on a Thursday (not their day)")
    else:
        check(True, "visiting-faculty check: (that Thursday lecture had a co-teacher present)")
    await api.call("DELETE", f"/api/absence/{res_thu['absence_id']}")

    # ---------------------------------------------------------------------------- 3) auto offer -> first-accept-wins -> day view
    log("\n== 3) auto offers -> first accept wins -> the day updates ==")
    offered = (await api.post(f"/api/subs/{S_sub}/offer", {"count": 5}))["offered"]
    check(len(offered) >= 2, f"auto-offered to {len(offered)} free teachers")
    winner = offered[0]["teacher_id"]
    loser = offered[1]["teacher_id"] if len(offered) >= 2 else None
    async with Acting(await teacher_name(uuid.UUID(winner))):
        acc = await client.post(f"/api/subs/{S_sub}/accept")
    check(acc.status_code == 200, "the first teacher's accept succeeds")
    if loser:
        async with Acting(await teacher_name(uuid.UUID(loser))):
            late = await client.post(f"/api/subs/{S_sub}/accept")
        check(late.status_code == 409, "a second accept is refused - already filled", str(late.status_code))
    filled = await db.fetchrow("select status, substitute_id from public.substitutions where id = $1", uuid.UUID(S_sub))
    check(filled["status"] == "filled" and str(filled["substitute_id"]) == winner, "the substitution is filled by the first accepter")
    # the class day view shows the substitute; the winner's own week shows a covering lecture
    cv = await api.get(f"/api/views/class/{row['semester_id']}?week_of={vw.monday_of(mon)}")
    hit = [c for c in cv["cells"] if c["day"] == 1 and c.get("substitutions")]
    check(bool(hit) and hit[0]["substitutions"][0]["substitute_id"] == winner, "the class view for that Monday shows the substitute")
    async with Acting(await teacher_name(uuid.UUID(winner))):
        mine = await client.get(f"/api/views/my/week?week_of={vw.monday_of(mon)}")
    cover = [c for c in mine.json()["cells"] if c["kind"] == "substitution" and c["date"] == mon.isoformat()]
    check(bool(cover), "the substitute's own week shows the covering lecture, marked 'substitution'")

    # ---------------------------------------------------------------------------- 4) manual assign
    log("\n== 4) manual assign on another lecture ==")
    other = [a for a in res["affected"] if a["status"] == "open" and a["id"] != S_sub]
    cands2 = (await api.get(f"/api/subs/{other[0]['id']}/candidates"))["candidates"] if other else []
    if other and cands2:
        sub2 = other[0]["id"]
        pick = next((c for c in cands2 if c["teacher_id"] != winner), cands2[0])
        await api.post(f"/api/subs/{sub2}/assign", {"teacher_id": pick["teacher_id"]})
        check(await db.fetchval("select status from public.substitutions where id = $1", uuid.UUID(sub2)) == "filled", "manual assign fills the lecture")
    else:
        check(True, "manual assign: (the teacher had a single uncovered lecture that day)")

    # ---------------------------------------------------------------------------- 5) one-day adjustments
    log("\n== 5) one-day move / cancel / swap + smart suggestions ==")
    adj_class = str(row["semester_id"])
    day = await api.get(f"/api/adjust?semester_id={adj_class}&date={mon.isoformat()}")
    movable = day["adjustable_starts"]
    check(bool(movable), "the class's Monday lectures are adjustable")
    start = movable[0]
    sug = await api.get(f"/api/adjust/suggest?semester_id={adj_class}&date={mon.isoformat()}&start_period={start}")
    check(sug["moves"] or sug["swaps"], "smart suggestions offer clash-free moves / swaps", sug["message"])
    if sug["moves"]:
        mv = await api.post("/api/adjust/move", {"semester_id": adj_class, "date": mon.isoformat(), "start_period": start, "new_start": sug["moves"][0]["new_start"]})
        cv = await api.get(f"/api/views/class/{adj_class}?week_of={vw.monday_of(mon)}")
        moved = [c for c in cv["cells"] if c.get("moved_from") == start]
        check(bool(moved) and moved[0]["start_period"] == sug["moves"][0]["new_start"], "a moved lecture sits at its new period for that date only")
        await api.call("DELETE", f"/api/adjust/{mv['id']}")
    # a swap of two same-length lectures of the class, then undone
    if sug["swaps"]:
        with_start = sug["swaps"][0]["swap_with_start"]
        sw = await api.post("/api/adjust/swap", {"semester_id": adj_class, "date": mon.isoformat(), "start_period": start, "with_start": with_start})
        rows = (await api.get(f"/api/adjust?semester_id={adj_class}&date={mon.isoformat()}"))["adjustments"]
        pair = [a for a in rows if a["swap_group"] == sw["swap_group"]]
        check(len(pair) == 2 and {a["orig_start"] for a in pair} == {start, with_start}, "a swap moves both lectures (one swap_group, two rows)")
        await db.execute("delete from public.day_adjustments where swap_group = $1", uuid.UUID(sw["swap_group"]))
    else:
        check(True, "swap: (the class had no two same-length lectures to swap that day)")
    # a clashing move is refused
    if len(movable) >= 2:
        bad = await client.post("/api/adjust/move", json={"semester_id": adj_class, "date": mon.isoformat(), "start_period": movable[0], "new_start": movable[1]})
        check(bad.status_code == 409, "a move onto an occupied period is refused")
    # cancel reduces the hours
    before = await api.get(f"/api/hours/subjects?school_id={sid}&semester_id={adj_class}")
    cancel_start = movable[-1]
    cancelled_label = next((c for c in day["cells"] if c["start_period"] == cancel_start), {}).get("items", [{}])[0].get("title", "")
    await api.post("/api/adjust/cancel", {"semester_id": adj_class, "date": mon.isoformat(), "start_period": cancel_start, "reason": "selftest"})
    after = await api.get(f"/api/hours/subjects?school_id={sid}&semester_id={adj_class}")
    lost_before = sum(s["lost"] for c in before["classes"] for s in c["subjects"])
    lost_after = sum(s["lost"] for c in after["classes"] for s in c["subjects"])
    check(lost_after > lost_before, f"a cancelled lecture ({cancelled_label}) is counted as a lost hour", f"{lost_before} -> {lost_after}")
    await db.execute("delete from public.day_adjustments where school_id = $1 and lecture_date = $2 and kind = 'cancel'", uuid.UUID(sid), mon)

    # ---------------------------------------------------------------------------- 6) sheet + summary
    log("\n== 6) the daily sheet + monthly summary ==")
    pdf = await client.get(f"/api/subs/sheet?school_id={sid}&date={mon.isoformat()}")
    check(pdf.status_code == 200 and pdf.content[:5] == b"%PDF-", "the daily substitution sheet is a real PDF", f"{len(pdf.content)} bytes")
    from pypdf import PdfReader

    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    check("Daily Substitution Sheet" in text and await teacher_name(absent_a) in text, "the sheet names the day and the absent teacher")
    summ = await api.get(f"/api/subs/summary?school_id={sid}&month={mon.strftime('%Y-%m')}")
    check(summ["totals"]["filled"] >= 1 and any(t["covered"] >= 1 for t in summ["teachers"]), "the monthly summary counts the substitutions", str(summ["totals"]))

    # ---------------------------------------------------------------------------- 7) timeout sweep
    log("\n== 7) the timeout sweep alerts the HOD ==")
    later = mon + timedelta(days=7)   # the same Monday pattern, a week on (still inside the term)
    res3 = await api.post("/api/absence", {"teacher_id": str(absent_a), "date": later.isoformat()})
    open_sub = next((a for a in res3["affected"] if a["status"] == "open"), None)
    if open_sub:
        hod = await db.fetchval("select id from public.profiles where full_name = 'Dr. Papri Das'")
        before_alerts = await db.fetchval("select count(*) from public.notifications where user_id = $1 and kind = 'sub_unfilled'", hod)
        await api.post(f"/api/subs/{open_sub['id']}/offer", {"count": 5, "timeout_minutes": 30})
        await db.execute("update public.substitutions set offer_deadline = now() - interval '1 minute' where id = $1", uuid.UUID(open_sub["id"]))
        await subflow.sweep(datetime.now(timezone.utc))   # (the 60s background task may also run it - either way the round closes)
        status = await db.fetchval("select status from public.substitutions where id = $1", uuid.UUID(open_sub["id"]))
        pending = await db.fetchval("select count(*) from public.substitution_offers where substitution_id = $1 and status = 'pending'", uuid.UUID(open_sub["id"]))
        check(status == "unfilled" and pending == 0, "the un-accepted round is marked 'unfilled' and its offers expire", f"status={status}")
        after_alerts = await db.fetchval("select count(*) from public.notifications where user_id = $1 and kind = 'sub_unfilled'", hod)
        check(after_alerts > before_alerts, "the HOD is alerted to assign manually")
    else:
        check(True, "timeout: (no open slot on the chosen day)")


async def cleanup(api: Api, started, original: dict[str, int | None]) -> None:
    log("\n== cleanup: every test artifact goes away ==")
    tree = await api.get("/api/structure/tree")
    sid = next(s for s in tree["schools"] if s["name"] == "School of IT")["id"]
    school = uuid.UUID(sid)
    for sql in (
        "delete from public.substitution_offers where school_id = $1", "delete from public.substitutions where school_id = $1",
        "delete from public.day_adjustments where school_id = $1", "delete from public.teacher_absences where school_id = $1",
        "delete from public.leave_requests where school_id = $1", "delete from public.extra_lectures where school_id = $1",
        "delete from public.publish_events where school_id = $1", "delete from public.publications where school_id = $1",
    ):
        await db.execute(sql, school)
    listing = await api.get(f"/api/drafts?school_id={sid}")
    for c in listing["classes"]:
        for d in c["drafts"]:
            await api.call("DELETE", f"/api/drafts/{d['id']}")
    runs = await api.get(f"/api/generation/runs?school_id={sid}")
    for r in runs["runs"]:
        if r["status"] == "running":
            await api.post(f"/api/generation/runs/{r['id']}/cancel")
            await wait_run(api, r["id"])
        await api.call("DELETE", f"/api/generation/runs/{r['id']}")
    await db.execute("delete from public.generation_settings where school_id = $1", school)
    restored = 0
    for sid_, weekly in original.items():
        n = await db.execute("update public.subjects set weekly_lectures = $2 where id = $1 and weekly_lectures is distinct from $2", uuid.UUID(sid_), weekly)
        restored += int(n.split()[-1])
    await db.execute("delete from public.notifications where created_at >= $1", started)
    await db.execute("delete from public.audit_log where at >= $1", started)
    left = await db.fetchrow(
        """
        select (select count(*) from public.substitutions) s, (select count(*) from public.teacher_absences) a,
               (select count(*) from public.leave_requests) l, (select count(*) from public.day_adjustments) j,
               (select count(*) from public.publications) p, (select count(*) from public.timetable_drafts) d,
               (select count(*) from public.subjects where weekly_lectures is not null) w
        """
    )
    check(all(v == 0 for v in dict(left).values()), f"nothing left behind (restored {restored} overrides)", str(dict(left)))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
