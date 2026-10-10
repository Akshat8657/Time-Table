"""Load + concurrency test of a RUNNING TimeTablePro (free: plain Python asyncio + httpx).

  python -m scripts.loadtest setup      throw-away logins (all at once = the "burst logins" test) + a small "E2E Load School"
  python -m scripts.loadtest run LABEL  the scenarios below; results -> loadtest_LABEL.json next to the state file
  python -m scripts.loadtest cleanup    everything the test made disappears (accounts, the load school, its rows, the run)

Scenarios (a realistic college day, against http://127.0.0.1:8000):
  S1  150 people at once reading published timetables (teachers: today / week / bell; HOD: master, class, teacher views,
      free-teacher finder) for 60 s, each with 0.5-2 s "thinking" between clicks
  S2  17 teachers open their Home screen at the same moment (all the calls that screen makes, together)
  S3  absences -> auto popup offers -> every offered teacher presses Accept at the same moment (first accept must win)
  S4  an automatic generation run of the real school while 60 people keep browsing (the run is deleted afterwards)

The REAL school is only read (S4 writes a generation run that is removed again). Tokens / passwords stay in the system temp
folder, never in the project.
"""
from __future__ import annotations

import asyncio
import json
import random
import statistics
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from app import db
from app.config import get_settings
from app.supabase_auth import SupabaseAuth

BASE = "http://127.0.0.1:8000"
ORIGIN = "http://localhost:5173"
STATE = Path(tempfile.gettempdir()) / "timetablepro_loadtest.json"
LOAD_SCHOOL = "E2E Load School"
REAL_TEACHERS, LOAD_TEACHERS = 15, 8


def log(msg: str = "") -> None:
    print(msg, flush=True)


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p / 100 * (len(s) - 1)))))
    return s[k]


class Stats:
    def __init__(self) -> None:
        self.times: dict[str, list[float]] = defaultdict(list)
        self.errors: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))

    def add(self, name: str, seconds: float, status: int) -> None:
        self.times[name].append(seconds)
        if status >= 400 and status not in (409,):
            self.errors[name][status] += 1

    def table(self) -> list[dict[str, Any]]:
        rows = []
        for name, ts in sorted(self.times.items()):
            rows.append({"what": name, "n": len(ts), "p50_ms": round(pct(ts, 50) * 1000), "p95_ms": round(pct(ts, 95) * 1000),
                         "max_ms": round(max(ts) * 1000), "errors": dict(self.errors.get(name, {}))})
        return rows


def show(title: str, rows: list[dict[str, Any]]) -> None:
    log(f"\n  {title}")
    log(f"    {'what':<34}{'n':>6}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}  errors")
    for r in rows:
        log(f"    {r['what']:<34}{r['n']:>6}{r['p50_ms']:>9}{r['p95_ms']:>9}{r['max_ms']:>9}  {r['errors'] or ''}")


async def timed(client: httpx.AsyncClient, stats: Stats, name: str, method: str, path: str, token: str, **kw) -> httpx.Response | None:
    t0 = time.perf_counter()
    try:
        r = await client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)
        stats.add(name, time.perf_counter() - t0, r.status_code)
        return r
    except httpx.HTTPError:
        stats.add(name, time.perf_counter() - t0, 599)
        return None


# ====================================================================================================== setup / cleanup
async def _wipe(auth: SupabaseAuth) -> None:
    for d in await db.fetch("select id from public.schools where name = $1", LOAD_SCHOOL):
        sid = d["id"]
        for sql in ("delete from public.substitution_offers where school_id = $1", "delete from public.substitutions where school_id = $1",
                    "delete from public.teacher_absences where school_id = $1", "delete from public.publish_events where school_id = $1",
                    "delete from public.publications where school_id = $1", "delete from public.timetable_drafts where school_id = $1",
                    "delete from public.notifications where school_id = $1", "delete from public.audit_log where school_id = $1",
                    "delete from public.courses where school_id = $1"):
            await db.execute(sql, sid)
        for r in await db.fetch("select id from public.profiles where school_id = $1", sid):
            await db.execute("delete from public.audit_log where actor_id = $1", r["id"])
            await auth.admin_delete_user(str(r["id"]))
        await db.execute("delete from public.schools where id = $1", sid)
    for r in await db.fetch("select id from auth.users where email like 'e2e-load-%@selftest.invalid'"):
        await db.execute("delete from public.notifications where user_id = $1", r["id"])
        await db.execute("delete from public.audit_log where actor_id = $1", r["id"])
        await auth.admin_delete_user(str(r["id"]))


async def setup() -> None:
    st = get_settings()
    await db.init_pool()
    auth = SupabaseAuth(st)
    try:
        await _wipe(auth)
        real = await db.fetchval("select id from public.schools where name = 'School of IT'")
        load = await db.fetchval("insert into public.schools (name) values ($1) returning id", LOAD_SCHOOL)
        people: list[dict[str, Any]] = []
        plan = [("admin", None, "e2e-load-admin")] + [("hod", real, "e2e-load-hod")] + [("teacher", real, f"e2e-load-t{i:02d}") for i in range(REAL_TEACHERS)]
        plan += [("hod", load, "e2e-load-lhod")] + [("teacher", load, f"e2e-load-l{i:02d}") for i in range(LOAD_TEACHERS)]
        for role, school, key in plan:
            email, pw = f"{key}@selftest.invalid", f"Ld-{uuid.uuid4().hex[:10]}-9x"
            u = await auth.admin_create_user(email=email, password=pw)
            await db.execute("insert into public.profiles (id, full_name, email, role, school_id, must_change_password) values ($1,$2,$3,$4,$5,false)",
                             uuid.UUID(u["id"]), f"Load {key[9:].upper()}", email, role, school)
            people.append({"key": key, "email": email, "password": pw, "role": role, "id": u["id"], "school": str(school) if school else None,
                           "where": "load" if school == load else ("real" if school else None)})
        # a published class in the load school (Monday + Tuesday lectures for the first two teachers)
        cid = await db.fetchval("insert into public.courses (school_id, name, code, num_semesters) values ($1, 'Load Course', 'LDC', 5) returning id", load)
        sems = {r["number"]: r["id"] for r in await db.fetch("insert into public.semesters (school_id, course_id, number) select $1, $2, g from generate_series(1,5) g returning id, number", load, cid)}
        term = await db.fetchval("select id from public.academic_terms where is_active")
        today = date.today()
        await db.execute("insert into public.term_semesters (term_id, semester_id, school_id, start_date, end_date) values ($1,$2,$3,$4,$5)", term, sems[1], load, today - timedelta(days=30), today + timedelta(days=150))
    finally:
        await auth.aclose()
        await db.close_pool()
    # ---- the burst: everybody logs in at the same moment
    stats = Stats()
    async with httpx.AsyncClient(base_url=BASE, headers={"Origin": ORIGIN}, timeout=120) as c:
        async def one(p):
            t0 = time.perf_counter()
            r = await c.post("/api/auth/login", json={"email": p["email"], "password": p["password"]})
            stats.add("burst login", time.perf_counter() - t0, r.status_code)
            if r.status_code == 200:
                p["token"] = r.json()["access_token"]
            else:
                p["login_error"] = r.status_code
        await asyncio.gather(*(one(p) for p in people))
        show(f"Burst: {len(people)} logins at the same moment", stats.table())
        lhod = next(p for p in people if p["key"] == "e2e-load-lhod")
        lt = [p for p in people if p["where"] == "load" and p["role"] == "teacher"]
        h = {"Authorization": f"Bearer {lhod['token']}"}
        await c.put("/api/timing/school", headers=h, json={"lunch_start": "13:10", "lunch_end": "13:40"})
        subj = {}
        for code, name, teacher in (("LD-M", "Load Maths", lt[0]), ("LD-P", "Load Physics", lt[1]), ("LD-C", "Load Chemistry", lt[2])):
            r = await c.post("/api/subjects", headers=h, json={"semester_id": str(sems[1]), "name": name, "code": code, "credits": 2, "semester_hours": 30})
            subj[code] = r.json()["id"]
            async with db_once() as conn:
                await conn.execute("insert into public.teacher_subjects (teacher_id, subject_id, school_id) values ($1,$2,$3) on conflict do nothing", uuid.UUID(teacher["id"]), uuid.UUID(subj[code]), load)
            await c.put("/api/assignments", headers=h, json={"subject_id": subj[code], "teacher_id": teacher["id"]})
        draft = (await c.post("/api/drafts", headers=h, json={"semester_id": str(sems[1]), "name": "Load plan"})).json()["id"]
        for day, start, code in ((1, 1, "LD-M"), (1, 2, "LD-P"), (1, 3, "LD-C"), (2, 1, "LD-M"), (2, 2, "LD-P"), (2, 3, "LD-C")):
            await c.post(f"/api/drafts/{draft}/placements", headers=h, json={"day": day, "start_period": start, "subject_id": subj[code]})
        r = await c.post("/api/publish", headers=h, json={"scope": "class", "semester_id": str(sems[1])})
        log(f"  load school published: {r.status_code}")
    STATE.write_text(json.dumps({"people": people, "load_school": str(load), "load_sem": str(sems[1]), "real_school": str(real), "created": time.time()}))
    log(f"setup done: {sum(1 for p in people if p.get('token'))} of {len(people)} logged in")


class db_once:
    """One short-lived connection for a single statement (outside the pool)."""

    async def __aenter__(self):
        self.conn = await db.connect_one()
        return self.conn

    async def __aexit__(self, *a):
        await self.conn.close()


async def cleanup() -> None:
    st = get_settings()
    await db.init_pool()
    auth = SupabaseAuth(st)
    try:
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        if state.get("run_id"):
            await db.execute("delete from public.timetable_drafts where id in (select d.id from public.timetable_drafts d join public.generation_solutions s on s.id = d.solution_id where s.run_id = $1)", uuid.UUID(state["run_id"]))
            await db.execute("delete from public.generation_runs where id = $1", uuid.UUID(state["run_id"]))
        await _wipe(auth)
    finally:
        await auth.aclose()
        await db.close_pool()
    STATE.unlink(missing_ok=True)
    log("cleanup done")


# ====================================================================================================== scenarios
async def s1_browse(state: dict, users: int, seconds: float, stats: Stats, stop: asyncio.Event | None = None) -> None:
    teachers = [p for p in state["people"] if p["where"] == "real" and p["role"] == "teacher" and p.get("token")]
    hod = next(p for p in state["people"] if p["key"] == "e2e-load-hod")
    real = state["real_school"]
    sems = [str(r["id"]) for r in state["real_sems"]]
    tids = state["real_teacher_ids"]
    end = time.monotonic() + seconds
    async with httpx.AsyncClient(base_url=BASE, headers={"Origin": ORIGIN}, timeout=60, limits=httpx.Limits(max_connections=users)) as c:
        async def user(i: int) -> None:
            is_hod = i % 5 == 0
            me = hod if is_hod else teachers[i % len(teachers)]
            await asyncio.sleep(random.uniform(0, 2))
            while time.monotonic() < end and not (stop and stop.is_set()):
                if is_hod:
                    pick = random.choice(["master today", "class week", "teacher week", "free now"])
                    path = {"master today": f"/api/views/master?scope=today&school_id={real}", "class week": f"/api/views/class/{random.choice(sems)}",
                            "teacher week": f"/api/views/teacher/{random.choice(tids)}", "free now": "/api/reports/free-teachers?now=true"}[pick]
                    await timed(c, stats, "HOD " + pick, "GET", path, me["token"])
                else:
                    pick = random.choices(["my today", "my week", "bell count", "home overview"], weights=[4, 2, 3, 1])[0]
                    path = {"my today": "/api/views/my/today", "my week": "/api/views/my/week", "bell count": "/api/notifications/unread-count",
                            "home overview": "/api/teacher/overview"}[pick]
                    await timed(c, stats, "teacher " + pick, "GET", path, me["token"])
                await asyncio.sleep(random.uniform(0.5, 2.0))
        await asyncio.gather(*(user(i) for i in range(users)))


HOME_CALLS = ["/api/auth/me", "/api/terms", "/api/teacher/overview", "/api/views/my/today", "/api/hours/my", "/api/prefs/status",
              "/api/subs/my-offers", "/api/notifications/unread-count"]


async def s2_homes(state: dict, stats: Stats) -> None:
    teachers = [p for p in state["people"] if p["where"] == "real" and p["role"] == "teacher" and p.get("token")]
    teachers = (teachers * 2)[:17]
    async with httpx.AsyncClient(base_url=BASE, headers={"Origin": ORIGIN}, timeout=60, limits=httpx.Limits(max_connections=200)) as c:
        async def home(p):
            t0 = time.perf_counter()
            await asyncio.gather(*(timed(c, stats, "home: " + path.split("/api/")[1], "GET", path, p["token"]) for path in HOME_CALLS))
            stats.add("WHOLE Home screen", time.perf_counter() - t0, 200)
        await asyncio.gather(*(home(p) for p in teachers))


async def s3_offers(state: dict, stats: Stats) -> dict[str, Any]:
    people = state["people"]
    lhod = next(p for p in people if p["key"] == "e2e-load-lhod")
    lt = [p for p in people if p["where"] == "load" and p["role"] == "teacher"]
    h = {"Authorization": f"Bearer {lhod['token']}"}
    today = date.today()
    monday = today + timedelta(days=(1 - today.isoweekday()) % 7 or 7)
    result = {"rounds": 0, "single_winner": 0}
    async with httpx.AsyncClient(base_url=BASE, headers={"Origin": ORIGIN}, timeout=60, limits=httpx.Limits(max_connections=100)) as c:
        for absent, day in ((lt[0], monday), (lt[1], monday + timedelta(days=1))):
            r = await c.post("/api/absence", headers=h, json={"teacher_id": absent["id"], "date": day.isoformat()})
            subs = (await c.get(f"/api/subs?date={day.isoformat()}", headers=h)).json().get("substitutions", [])
            for sub in [s for s in subs if s["status"] == "open"]:
                o = await timed(c, stats, "HOD sends auto-offer (5)", "POST", f"/api/subs/{sub['id']}/offer", lhod["token"], json={"count": 5})
                offered = {x["teacher_id"] for x in (o.json().get("offered") or [])} if o is not None and o.status_code == 200 else set()
                takers = [p for p in lt if p["id"] in offered] or [p for p in lt if p["id"] != absent["id"]][:5]
                codes = await asyncio.gather(*(timed(c, stats, "teacher presses Accept (all at once)", "POST", f"/api/subs/{sub['id']}/accept", p["token"]) for p in takers))
                wins = sum(1 for x in codes if x is not None and x.status_code == 200)
                result["rounds"] += 1
                result["single_winner"] += wins == 1
            if r.status_code == 201:
                await c.delete(f"/api/absence/{r.json()['absence_id']}", headers=h)
    return result


async def s4_generation(state: dict, stats: Stats) -> dict[str, Any]:
    admin = next(p for p in state["people"] if p["key"] == "e2e-load-admin")
    out: dict[str, Any] = {}
    async with httpx.AsyncClient(base_url=BASE, headers={"Origin": ORIGIN}, timeout=60) as c:
        r = await c.post("/api/generation/runs", headers={"Authorization": f"Bearer {admin['token']}"},
                         json={"school_id": state["real_school"], "name": "LOADTEST (deleted again)", "time_limit_s": 40, "mode": "fresh"})
        if r.status_code not in (200, 201, 202):
            out["error"] = f"could not start a run: {r.status_code} {r.text[:120]}"
            return out
        run_id = r.json()["id"]
        state["run_id"] = run_id
        STATE.write_text(json.dumps(state))
        stop = asyncio.Event()

        async def watch():
            t0 = time.monotonic()
            while True:
                await asyncio.sleep(2)
                g = await c.get(f"/api/generation/runs/{run_id}", headers={"Authorization": f"Bearer {admin['token']}"})
                if g.status_code == 200 and g.json().get("status") not in ("running", "queued"):
                    out["run_seconds"] = round(time.monotonic() - t0, 1)
                    out["run_status"] = g.json().get("status")
                    stop.set()
                    return
                if time.monotonic() - t0 > 240:
                    stop.set()
                    return
        await asyncio.gather(watch(), s1_browse(state, 60, 240, stats, stop))
        d = await c.delete(f"/api/generation/runs/{run_id}", headers={"Authorization": f"Bearer {admin['token']}"})
        out["deleted"] = d.status_code
        if d.status_code == 200:
            state.pop("run_id", None)
            STATE.write_text(json.dumps(state))
    return out


async def run(label: str) -> None:
    state = json.loads(STATE.read_text())
    await db.init_pool()
    try:
        state["real_sems"] = [dict(id=str(r["id"])) for r in await db.fetch(
            "select sm.id from public.semesters sm join public.publications p on p.semester_id = sm.id where sm.school_id = $1", uuid.UUID(state["real_school"]))]
        state["real_teacher_ids"] = [str(r["id"]) for r in await db.fetch(
            "select id from public.profiles where school_id = $1 and role in ('teacher', 'hod') and email not like 'e2e-%'", uuid.UUID(state["real_school"]))]
    finally:
        await db.close_pool()
    report: dict[str, Any] = {"label": label, "at": time.strftime("%Y-%m-%d %H:%M")}
    log(f"\n=== LOAD TEST '{label}' against {BASE} ===")
    s = Stats()
    await s2_homes(state, s)
    report["S2"] = s.table()
    show("S2: 17 teachers open Home at the same moment (first time)", report["S2"])
    await asyncio.sleep(2)
    s = Stats()
    await s2_homes(state, s)
    report["S2_again"] = s.table()
    show("S2 again: the same 17 Home screens a moment later", report["S2_again"])
    s = Stats()
    t0 = time.monotonic()
    await s1_browse(state, 150, 60, s)
    report["S1"] = s.table()
    report["S1_requests_per_s"] = round(sum(len(v) for v in s.times.values()) / (time.monotonic() - t0), 1)
    show(f"S1: 150 people browsing for 60 s ({report['S1_requests_per_s']} requests / s)", report["S1"])
    s = Stats()
    report["S3_result"] = await s3_offers(state, s)
    report["S3"] = s.table()
    show(f"S3: offers + simultaneous accepts {report['S3_result']}", report["S3"])
    s = Stats()
    report["S4_result"] = await s4_generation(state, s)
    report["S4"] = s.table()
    show(f"S4: 60 people browsing DURING a generation run {report['S4_result']}", report["S4"])
    out = STATE.with_name(f"timetablepro_loadtest_{label}.json")
    out.write_text(json.dumps(report, indent=1))
    log(f"\nsaved {out}")


if __name__ == "__main__":
    cmd = (sys.argv[1:] or ["run"])[0]
    if cmd == "setup":
        asyncio.run(setup())
    elif cmd == "cleanup":
        asyncio.run(cleanup())
    else:
        asyncio.run(run(sys.argv[2] if len(sys.argv) > 2 else "now"))
