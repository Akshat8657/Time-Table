"""Phase 3 API tests: weights, generation runs (background + progress), solutions, semi-automatic pins,
copy-previous-term, the impossible-case messages, roles / school locking / RLS.

Runs in the throw-away school against the REAL API + Supabase (never touches the seeded School of IT).
The tests build on each other (run them in file order). A whole-school run covers EVERY class of the test school.
"""
from __future__ import annotations

import time
import uuid

import httpx
import pytest

from .conftest import db_exec, db_fetch, db_val, mail_for

S: dict = {}
REAL = {"college_start": "10:00", "college_end": "16:50", "lecture_minutes": 60, "break_minutes": 5, "days_per_week": 5}
MON, TUE, WED, THU, FRI = 1, 2, 3, 4, 5


@pytest.fixture(scope="module", autouse=True)
def setup(p1):
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026}).status_code == 200
    assert p1.api(p1.admin, "PUT", "/api/timing/college", json=REAL).status_code == 200
    assert p1.api(p1.hod, "PUT", "/api/timing/school", json={"lunch_start": "13:10", "lunch_end": "13:40"}).status_code == 200
    cid = db_val("insert into public.courses (school_id, name, code, num_semesters) values ($1, 'ZZ Gen Course', 'ZZG', 3) returning id", uuid.UUID(p1.school))
    sems = db_fetch("insert into public.semesters (school_id, course_id, number) select $1, $2, g from generate_series(1, 3) g returning id, number", uuid.UUID(p1.school), cid)
    S["sem"] = {r["number"]: str(r["id"]) for r in sems}
    S["label"] = {n: f"ZZ Gen Course - Sem {n}" for n in S["sem"]}
    yield


def cls(n: int) -> str:
    return S["sem"][n]


def mk_subject(p1, n: int, code: str, name: str, **over) -> str:
    body = {"semester_id": cls(n), "name": name, "code": code, "credits": 2, "semester_hours": 30}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/subjects", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def mk_teacher(p1, key: str, subject_ids: list[str], **over) -> str:
    body = {"full_name": f"Gen {key}", "email": mail_for(f"p3-{key}"), "subject_ids": subject_ids}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/users", json=body)
    assert r.status_code == 201, r.text
    return r.json()["user"]["id"]


def assign(p1, subject_id: str, teacher_id: str) -> None:
    assert p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": subject_id, "teacher_id": teacher_id}).status_code == 200


def wait_run(p1, run_id: str, timeout: float = 120.0) -> dict:
    t0 = time.monotonic()
    while True:
        got = p1.api(p1.hod, "GET", f"/api/generation/runs/{run_id}")
        assert got.status_code == 200, got.text
        data = got.json()
        if data["status"] != "running":
            return data
        assert time.monotonic() - t0 < timeout, f"run stuck: {data['progress']}% {data['message']}"
        time.sleep(0.5)


def run_and_wait(p1, body: dict | None = None, timeout: float = 120.0) -> dict:
    r = p1.api(p1.hod, "POST", "/api/generation/runs", json=body or {})
    assert r.status_code == 201, r.text
    return wait_run(p1, r.json()["id"], timeout)


def sol_class(solution: dict, n: int) -> dict:
    return next(v for v in solution["breakdown"]["classes"].values() if v["label"] == S["label"][n])


# ====================================================================================================
# 1) weights
# ====================================================================================================
def test_weights_have_defaults_and_can_be_saved(p1):
    r = p1.api(p1.hod, "GET", "/api/generation/weights")
    assert r.status_code == 200
    data = r.json()
    assert {x["name"] for x in data["rules"]} >= {"twice_a_day", "class_gaps", "spread", "lab_lunch", "visiting_packed"}
    assert data["weights"]["class_gaps"] == 80                                     # the default
    r = p1.api(p1.hod, "PUT", "/api/generation/weights", json={"weights": {"class_gaps": 95, "lab_lunch": 10}})
    assert r.status_code == 200 and r.json()["weights"]["class_gaps"] == 95
    assert p1.api(p1.hod, "GET", "/api/generation/weights").json()["weights"]["lab_lunch"] == 10
    assert p1.api(p1.hod, "PUT", "/api/generation/weights", json={"weights": {"bogus": 5}}).status_code == 422
    assert p1.api(p1.hod, "PUT", "/api/generation/weights", json={"weights": {"spread": 101}}).status_code == 422
    assert p1.api(p1.teacher, "GET", "/api/generation/weights").status_code == 403
    assert p1.api(p1.hod, "PUT", "/api/generation/weights", json={"school_id": p1.school_b, "weights": {}}).status_code == 403
    assert p1.api(p1.hod, "PUT", "/api/generation/weights", json={"weights": {"class_gaps": 80, "lab_lunch": 30}}).status_code == 200


# ====================================================================================================
# 2) a full run on a feasible little school
# ====================================================================================================
def test_setup_a_feasible_timetable_problem(p1):
    maths = mk_subject(p1, 1, "G1-MAT", "Gen Maths", credits=3)
    phys = mk_subject(p1, 1, "G1-PHY", "Gen Physics", credits=2)
    lab = mk_subject(p1, 1, "G1-LAB", "Gen Physics LAB", credits=1, is_lab=True)
    g = p1.api(p1.hod, "POST", "/api/subjects/groups", json={"semester_id": cls(1), "name": "Gen Tracks"}).json()["id"]
    ai = mk_subject(p1, 1, "G1-AI", "Gen AI Track", credits=2, is_elective=True, elective_group_id=g)
    cy = mk_subject(p1, 1, "G1-CY", "Gen Cyber Track", credits=2, is_elective=True, elective_group_id=g)
    m3 = mk_subject(p1, 3, "G3-MAT", "Gen Maths III", credits=3)
    v3 = mk_subject(p1, 3, "G3-VIS", "Gen Visiting Subject", credits=2)
    t_m = mk_teacher(p1, "maths", [maths, m3])                                     # shared across the two classes
    t_p = mk_teacher(p1, "phys", [phys, lab])
    t_a = mk_teacher(p1, "ai", [ai])
    t_c = mk_teacher(p1, "cy", [cy])
    t_v = mk_teacher(p1, "visit", [v3], is_visiting=True, visiting_days=[1, 2, 3], max_weekly_hours=6)
    for sid, tid in ((maths, t_m), (phys, t_p), (lab, t_p), (ai, t_a), (cy, t_c), (m3, t_m), (v3, t_v)):
        assign(p1, sid, tid)
    r = p1.api(p1.hod, "POST", "/api/fixed-slots", json={"semester_id": cls(1), "day": MON, "start_period": 5, "kind": "PE"})
    assert r.status_code == 201
    S.update(maths=maths, phys=phys, lab=lab, group=g, ai=ai, cy=cy, m3=m3, v3=v3, t_m=t_m, t_v=t_v)


def test_a_run_finishes_with_scored_distinct_solutions(p1):
    data = run_and_wait(p1, {"name": "First try", "time_limit_s": 60})
    assert data["status"] == "done", data
    assert data["progress"] == 100 and "score" in data["message"]
    sols = data["solutions"]
    assert 2 <= len(sols) <= 3 and [s["label"] for s in sols] == list("ABC")[: len(sols)]
    best = sols[0]
    assert 0 <= best["score"] <= 100 and best["complete"] is True
    assert {r["rule"] for r in best["breakdown"]["rules"]} >= {"twice_a_day", "class_gaps", "spread"}
    assert all(r["points_lost"] >= 0 for r in best["breakdown"]["rules"])          # the per-rule breakdown
    assert sol_class(best, 1)["percent"] == 100 and sol_class(best, 3)["percent"] == 100
    assert best["breakdown"]["unplaced"] == []
    S["run1"], S["solA"], S["solB"] = data["id"], sols[0], sols[1]


def test_each_solution_is_a_set_of_ordinary_editable_drafts(p1):
    sol = S["solA"]
    drafts = {d["semester_id"]: d for d in sol["drafts"]}
    assert cls(1) in drafts and cls(3) in drafts
    assert drafts[cls(1)]["name"].startswith("Auto A · First try")
    d = p1.api(p1.hod, "GET", f"/api/drafts/{drafts[cls(1)]['draft_id']}").json()
    assert d["draft"]["is_primary"] is False                                        # a solution never jumps the queue
    assert d["completeness"]["percent"] == 100 and d["problems"]["clashes"] == 0
    assert any(p["parallel"] for p in d["placements"])                              # the track group went in as ONE parallel slot
    labs = [p for p in d["placements"] if p["is_lab"]]
    assert labs and all(p["length"] == 2 for p in labs)
    # the shared teacher is never double-booked between the two generated drafts
    d3 = p1.api(p1.hod, "GET", f"/api/drafts/{drafts[cls(3)]['draft_id']}").json()
    busy1 = {(p["day"], q) for p in d["placements"] for q in range(p["start_period"], p["start_period"] + p["length"])
             if any(e["teacher_id"] == S["t_m"] for e in p["entries"])}
    busy3 = {(p["day"], q) for p in d3["placements"] for q in range(p["start_period"], p["start_period"] + p["length"])
             if any(e["teacher_id"] == S["t_m"] for e in p["entries"])}
    assert not (busy1 & busy3)
    # visiting faculty only Mon-Wed
    vis = [p for p in d3["placements"] if any(e["teacher_id"] == S["t_v"] for e in p["entries"])]
    assert vis and all(p["day"] in (1, 2, 3) for p in vis)


def test_using_a_solution_makes_its_drafts_primary_and_the_report_stays_clean(p1):
    r = p1.api(p1.hod, "POST", f"/api/generation/solutions/{S['solA']['id']}/use")
    assert r.status_code == 200 and r.json()["used"] is True
    for d in S["solA"]["drafts"]:
        assert db_val("select is_primary from public.timetable_drafts where id = $1", uuid.UUID(d["draft_id"])) is True
        others = db_val("select count(*) from public.timetable_drafts where semester_id = $1 and is_primary", uuid.UUID(d["semester_id"]))
        assert others == 1
    rep = p1.api(p1.hod, "GET", "/api/conflicts").json()
    assert rep["counts"]["clashes"] == 0
    listing = p1.api(p1.hod, "GET", "/api/generation/runs").json()
    run = next(x for x in listing["runs"] if x["id"] == S["run1"])
    assert next(s for s in run["solutions"] if s["id"] == S["solA"]["id"])["in_use"] is True
    assert next(s for s in run["solutions"] if s["id"] == S["solB"]["id"])["in_use"] is False


def test_a_used_run_cannot_be_discarded_but_an_unused_one_can(p1):
    r = p1.api(p1.hod, "DELETE", f"/api/generation/runs/{S['run1']}")
    assert r.status_code == 409 and r.json()["code"] == "IN_USE"
    data = run_and_wait(p1, {"name": "Throwaway", "time_limit_s": 30})
    assert data["status"] == "done"
    draft_ids = [d["draft_id"] for s in data["solutions"] for d in s["drafts"]]
    r = p1.api(p1.hod, "DELETE", f"/api/generation/runs/{data['id']}")
    assert r.status_code == 200 and r.json()["drafts_removed"] == len(draft_ids)
    for did in draft_ids:
        assert db_val("select count(*) from public.timetable_drafts where id = $1", uuid.UUID(did)) == 0
    assert p1.api(p1.hod, "GET", f"/api/generation/runs/{data['id']}").status_code == 404


# ====================================================================================================
# 3) semi-automatic mode: pinned placements survive a regeneration
# ====================================================================================================
def test_pin_a_placement_and_regenerate_around_it(p1):
    # pin Gen Maths on Friday P6 in the class's primary draft (an "Auto A" draft from the used solution)
    primary = db_val("select id from public.timetable_drafts where semester_id = $1 and is_primary", uuid.UUID(cls(1)))
    d = p1.api(p1.hod, "GET", f"/api/drafts/{primary}").json()
    maths_pl = next(p for p in d["placements"] if any(e["subject_id"] == S["maths"] for e in p["entries"]))
    spot = (maths_pl["day"], maths_pl["start_period"])                              # pin it exactly where it sits now
    r = p1.api(p1.hod, "POST", f"/api/drafts/{primary}/placements/{maths_pl['key']}/lock", json={"locked": True})
    assert r.status_code == 200 and r.json()["locked"] is True
    pinned = next(p for p in r.json()["detail"]["placements"] if p["key"] == maths_pl["key"])
    assert pinned["is_locked"] is True
    # a pinned placement cannot be moved, removed or swapped
    assert p1.api(p1.hod, "PATCH", f"/api/drafts/{primary}/placements/{maths_pl['key']}", json={"day": MON, "start_period": 1}).status_code == 409, "a pinned placement must refuse to move"
    assert p1.api(p1.hod, "DELETE", f"/api/drafts/{primary}/placements/{maths_pl['key']}").status_code == 409
    clear = p1.api(p1.hod, "POST", f"/api/drafts/{primary}/clear")
    assert clear.status_code == 200 and clear.json()["kept"] == 1                   # clear keeps the pin
    data = run_and_wait(p1, {"name": "Around the pin", "mode": "keep_locked", "time_limit_s": 60})
    assert data["status"] == "done"
    for sol in data["solutions"]:
        assert sol["complete"] is True                                              # the engine verified the whole set internally
        draft = next(x for x in sol["drafts"] if x["semester_id"] == cls(1))
        nd = p1.api(p1.hod, "GET", f"/api/drafts/{draft['draft_id']}").json()
        kept = next(p for p in nd["placements"] if any(e["subject_id"] == S["maths"] for e in p["entries"]) and p["is_locked"])
        assert (kept["day"], kept["start_period"]) == spot                          # exactly where it was pinned
        assert nd["completeness"]["percent"] == 100
    # picking one makes the whole set primary - and the school is clash-free again
    assert p1.api(p1.hod, "POST", f"/api/generation/solutions/{data['solutions'][0]['id']}/use").status_code == 200
    assert p1.api(p1.hod, "GET", "/api/conflicts").json()["counts"]["clashes"] == 0
    prim = db_val("select id from public.timetable_drafts where semester_id = $1 and is_primary", uuid.UUID(cls(1)))
    pd = p1.api(p1.hod, "GET", f"/api/drafts/{prim}").json()
    again = next(p for p in pd["placements"] if p["is_locked"])
    assert (again["day"], again["start_period"]) == spot
    S["run_pin"] = data["id"]


# ====================================================================================================
# 4) impossible = a clear message
# ====================================================================================================
def test_an_impossible_class_is_explained_with_numbers(p1):
    fat = mk_subject(p1, 3, "G3-FAT", "Gen Impossible", credits=28)
    t = mk_teacher(p1, "fat", [fat])
    assign(p1, fat, t)
    r = p1.api(p1.hod, "POST", "/api/generation/runs", json={"name": "Impossible", "time_limit_s": 30})
    assert r.status_code == 201
    pre = r.json()["precheck"]
    msg = next(f["message"] for f in pre if f["kind"] == "class_capacity" and S["label"][3] in f["message"])
    assert "33 lecture periods" in msg and "only 30 free" in msg                     # 28 + 3 + 2 demanded, 30 cells exist
    data = wait_run(p1, r.json()["id"], timeout=90)
    assert data["status"] == "done"
    best = data["solutions"][0]
    assert best["complete"] is False
    assert sol_class(best, 3)["percent"] < 100
    unplaced = [u for u in best["breakdown"]["unplaced"] if S["label"][3] in u["message"]]
    assert unplaced and all("could not be placed" in u["message"] for u in unplaced)
    assert sum(u["missing"] for u in unplaced) == 3                                  # exactly the 33 - 30 shortfall
    assert p1.api(p1.hod, "DELETE", f"/api/generation/runs/{data['id']}").status_code == 200
    # clean the fat subject away again (its drafts are gone with the run)
    assert p1.api(p1.hod, "DELETE", f"/api/assignments/{db_val('select id from public.assignments where subject_id = $1', uuid.UUID(fat))}").status_code == 200
    db_exec("delete from public.teacher_subjects where subject_id = $1", uuid.UUID(fat))
    assert p1.api(p1.hod, "DELETE", f"/api/subjects/{fat}").status_code == 200


def test_cancel_is_accepted_and_the_run_ends(p1):
    r = p1.api(p1.hod, "POST", "/api/generation/runs", json={"name": "Cancel me", "time_limit_s": 600})
    assert r.status_code == 201
    run_id = r.json()["id"]
    c = p1.api(p1.hod, "POST", f"/api/generation/runs/{run_id}/cancel")
    assert c.status_code == 200 and c.json()["status"] in ("cancelling", "cancelled", "done")
    t0 = time.monotonic()
    while True:
        st = p1.api(p1.hod, "GET", f"/api/generation/runs/{run_id}").json()["status"]
        if st != "running":
            break
        assert time.monotonic() - t0 < 60
        time.sleep(0.5)
    assert st in ("cancelled", "done")                                              # tiny problems may finish first - both honest
    if st == "done":
        p1.api(p1.hod, "DELETE", f"/api/generation/runs/{run_id}")
    # a stale 'running' row whose process died: cancel closes it directly
    stale = uuid.uuid4()
    db_exec("insert into public.generation_runs (id, school_id, term_id, name, status) select $1, $2, t.id, 'ZZ Stale', 'running' from public.academic_terms t where t.is_active",
            stale, uuid.UUID(p1.school))
    assert p1.api(p1.hod, "POST", f"/api/generation/runs/{stale}/cancel").json()["status"] == "cancelled"
    assert db_val("select status from public.generation_runs where id = $1", stale) == "cancelled"
    db_exec("delete from public.generation_runs where id = $1", stale)


def test_only_one_run_at_a_time_per_school(p1):
    r = p1.api(p1.hod, "POST", "/api/generation/runs", json={"time_limit_s": 600})
    assert r.status_code == 201
    run_id = r.json()["id"]
    second = p1.api(p1.hod, "POST", "/api/generation/runs", json={})
    if second.status_code == 409:
        assert second.json()["code"] == "RUN_ACTIVE"
    else:  # the first one was simply faster than this request
        assert second.status_code == 201
        p1.api(p1.hod, "POST", f"/api/generation/runs/{second.json()['id']}/cancel")
    p1.api(p1.hod, "POST", f"/api/generation/runs/{run_id}/cancel")
    t0 = time.monotonic()
    while p1.api(p1.hod, "GET", f"/api/generation/runs/{run_id}").json()["status"] == "running":
        assert time.monotonic() - t0 < 60
        time.sleep(0.5)


# ====================================================================================================
# 5) copy a previous term as a base
# ====================================================================================================
def test_copy_previous_term_drafts(p1):
    # build a tiny past term with a primary draft
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2024}).status_code == 200
    r = p1.api(p1.hod, "POST", "/api/drafts", json={"semester_id": cls(1), "name": "Old base"})
    assert r.status_code == 201, r.text
    old_draft = r.json()["id"]
    assert p1.api(p1.hod, "POST", f"/api/drafts/{old_draft}/placements", json={"subject_id": S["maths"], "day": TUE, "start_period": 2}).status_code == 201
    assert p1.api(p1.hod, "POST", f"/api/drafts/{old_draft}/placements", json={"group_id": S["group"], "day": WED, "start_period": 1}).status_code == 201
    old_term = db_val("select term_id from public.timetable_drafts where id = $1", uuid.UUID(old_draft))
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "even", "year": 2025}).status_code == 200
    r = p1.api(p1.hod, "POST", "/api/drafts/copy-term", json={"term_id": str(old_term)})
    assert r.status_code == 409 and r.json()["code"] == "KIND_MISMATCH"              # odd -> even: nothing matches
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026}).status_code == 200
    terms = p1.api(p1.hod, "GET", "/api/drafts/copyable-terms").json()["terms"]
    old = next(t for t in terms if t["name"] == "Odd 2024")
    assert old["primaries"] == 1 and old["same_kind"] is True
    r = p1.api(p1.hod, "POST", "/api/drafts/copy-term", json={"term_id": str(old_term)})
    assert r.status_code == 201, r.text
    made = r.json()["created"]
    assert len(made) == 1 and made[0]["name"] == "From Odd 2024"
    nd = p1.api(p1.hod, "GET", f"/api/drafts/{made[0]['draft_id']}").json()
    spots = {(p["day"], p["start_period"], p["parallel"]) for p in nd["placements"]}
    assert spots == {(TUE, 2, False), (WED, 1, True)}                                # the base came over, parallel kept
    assert nd["draft"]["is_primary"] is False                                        # the class already had drafts
    assert p1.api(p1.hod, "POST", "/api/drafts/copy-term", json={"term_id": str(uuid.uuid4())}).status_code == 404
    assert p1.api(p1.teacher, "POST", "/api/drafts/copy-term", json={"term_id": str(old_term)}).status_code == 403


# ====================================================================================================
# 6) roles, schools, RLS
# ====================================================================================================
def test_generation_is_locked_to_roles_and_schools(p1):
    assert p1.api(p1.teacher, "GET", "/api/generation/runs").status_code == 403
    assert p1.api(p1.teacher, "POST", "/api/generation/runs", json={}).status_code == 403
    assert p1.api(p1.hod, "GET", f"/api/generation/runs?school_id={p1.school_b}").status_code == 403
    assert p1.api(p1.hod, "POST", "/api/generation/runs", json={"school_id": p1.school_b}).status_code == 403
    assert p1.api(p1.admin, "GET", "/api/generation/runs").status_code == 422        # the admin must name the school
    assert p1.api(p1.admin, "GET", f"/api/generation/runs?school_id={p1.school}").status_code == 200
    # a run of school B does not exist for the HOD of school P
    foreign = uuid.uuid4()
    db_exec("insert into public.generation_runs (id, school_id, term_id, name, status) select $1, $2, t.id, 'ZZ Foreign run', 'done' from public.academic_terms t where t.is_active",
            foreign, uuid.UUID(p1.school_b))
    assert p1.api(p1.hod, "GET", f"/api/generation/runs/{foreign}").status_code == 404
    assert p1.api(p1.hod, "DELETE", f"/api/generation/runs/{foreign}").status_code == 404
    assert p1.api(p1.admin, "GET", f"/api/generation/runs/{foreign}").status_code == 200
    db_exec("delete from public.generation_runs where id = $1", foreign)
    assert p1.api(p1.hod, "POST", f"/api/generation/solutions/{uuid.uuid4()}/use").status_code == 404


REST = f"{__import__('app.config', fromlist=['get_settings']).get_settings().supabase_url}/rest/v1"


def rest(method: str, path: str, token: str | None = None, **kw) -> httpx.Response:
    from app.config import get_settings

    headers = {"apikey": get_settings().supabase_anon_key}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.request(method, f"{REST}{path}", headers=headers, timeout=40, **kw)


@pytest.mark.parametrize("table", ["generation_settings", "generation_runs", "generation_solutions"])
def test_generation_tables_follow_the_same_rls_as_drafts(p1, table):
    assert rest("GET", f"/{table}?select=school_id").status_code in (401, 403)       # not signed in

    def visible(who) -> list:
        r = rest("GET", f"/{table}?select=school_id", who.token)
        assert r.status_code == 200, r.text[:150]
        return r.json()

    assert visible(p1.teacher) == []                                                 # teachers see nothing at all
    assert {row["school_id"] for row in visible(p1.hod)} <= {p1.school}
    for who in (p1.admin, p1.hod, p1.teacher):
        assert rest("POST", f"/{table}", who.token, json={"school_id": p1.school}).status_code in (401, 403)
        assert rest("DELETE", f"/{table}?school_id=eq.{p1.school}", who.token).status_code in (401, 403)


def test_every_generation_change_is_audited(p1):
    me = uuid.UUID(p1.hod.account.user_id)
    for entity, action in (("generation_run", "create"), ("generation_run", "update"), ("generation_run", "delete"),
                           ("generation_solution", "update"), ("generation_weights", "update")):
        n = db_val("select count(*) from public.audit_log where entity = $1 and action = $2 and actor_id = $3", entity, action, me)
        assert n > 0, (entity, action)
