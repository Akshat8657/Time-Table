"""Phase 2 API tests: timetable DRAFTS, placements, real-time clash checks, the conflict report, roles and school locking.

Runs in a throw-away school against the REAL API + Supabase (never touches the seeded School of IT). Tests build on each other.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from .conftest import connect_one, db_exec, db_fetch, db_val, mail_for, run

S: dict = {}
REAL = {"college_start": "10:00", "college_end": "16:50", "lecture_minutes": 60, "break_minutes": 5, "days_per_week": 5}
MON, TUE, WED, THU, FRI = 1, 2, 3, 4, 5


@pytest.fixture(scope="module", autouse=True)
def setup(p1):
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026}).status_code == 200
    assert p1.api(p1.admin, "PUT", "/api/timing/college", json=REAL).status_code == 200
    assert p1.api(p1.hod, "PUT", "/api/timing/school", json={"lunch_start": "13:10", "lunch_end": "13:40"}).status_code == 200
    cid = db_val("insert into public.courses (school_id, name, code, num_semesters) values ($1, 'ZZ Draft Course', 'ZZD', 5) returning id", uuid.UUID(p1.school))
    sems = db_fetch("insert into public.semesters (school_id, course_id, number) select $1, $2, g from generate_series(1, 5) g returning id, number", uuid.UUID(p1.school), cid)
    S["sem"] = {r["number"]: str(r["id"]) for r in sems}
    S["label"] = {n: f"ZZ Draft Course - Sem {n}" for n in S["sem"]}
    yield


def cls(n: int) -> str:
    return S["sem"][n]


def mk_subject(p1, n: int, code: str, name: str, **over) -> str:
    body = {"semester_id": cls(n), "name": name, "code": code, "credits": 2, "semester_hours": 30}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/subjects", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def mk_teacher(p1, key: str, subject_ids: list[str] | None = None, **over) -> str:
    body = {"full_name": f"Draft {key}", "email": mail_for(f"p2-{key}"), "subject_ids": subject_ids or []}
    body.update(over)
    r = p1.api(p1.hod, "POST", "/api/users", json=body)
    assert r.status_code == 201, r.text
    return r.json()["user"]["id"]


def assign(p1, subject_id: str, teacher_id: str) -> None:
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": subject_id, "teacher_id": teacher_id})
    assert r.status_code == 200, r.text


def new_draft(p1, n: int, name: str, who=None, **kw):
    return (who or p1.hod).client.post("/api/drafts", json={"semester_id": cls(n), "name": name, **kw}, headers={"Authorization": f"Bearer {(who or p1.hod).token}"})


def mk(p1, n: int, name: str, **kw) -> str:
    r = new_draft(p1, n, name, **kw)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def detail(p1, draft: str) -> dict:
    r = p1.api(p1.hod, "GET", f"/api/drafts/{draft}")
    assert r.status_code == 200, r.text
    return r.json()


def place(p1, draft: str, day: int, start: int, who=None, **body):
    return p1.api(who or p1.hod, "POST", f"/api/drafts/{draft}/placements", json={"day": day, "start_period": start, **body})


def sub_of(det: dict, name: str) -> dict:
    return next(s for s in det["subjects"] if s["name"] == name)


# ====================================================================================================
# 1) drafts: create, list, primary, rename, copy, delete
# ====================================================================================================
def test_the_first_draft_of_a_class_is_primary_and_the_names_are_unique(p1):
    r = new_draft(p1, 1, "Plan A", note="my first try")
    assert r.status_code == 201 and r.json()["is_primary"] is True
    S["a"] = r.json()["id"]
    r = new_draft(p1, 1, "Plan B")
    assert r.status_code == 201 and r.json()["is_primary"] is False
    S["b"] = r.json()["id"]
    assert new_draft(p1, 1, "  plan a ").status_code == 409                       # the same name (case / spaces ignored) in one class
    assert new_draft(p1, 3, "Plan A").status_code == 201                          # ... but another class may use it
    S["c3"] = db_val("select id from public.timetable_drafts where semester_id = $1", uuid.UUID(cls(3)))
    S["c3"] = str(S["c3"])
    listing = p1.api(p1.hod, "GET", "/api/drafts").json()
    row = next(c for c in listing["classes"] if c["semester_id"] == cls(1))
    assert [d["name"] for d in row["drafts"]] == ["Plan A", "Plan B"] and row["drafts"][0]["is_primary"] and not row["drafts"][1]["is_primary"]
    assert row["drafts"][0]["note"] == "my first try" and row["drafts"][0]["percent"] == 0
    assert listing["active_term"]["name"] == "Odd 2026"
    assert {c["semester_id"] for c in listing["classes"]} >= {cls(1), cls(3), cls(5)}      # only the classes that run in the odd term
    assert cls(2) not in {c["semester_id"] for c in listing["classes"]}


def test_draft_input_is_checked(p1):
    assert new_draft(p1, 1, "").status_code == 422
    assert new_draft(p1, 1, "x" * 61).status_code == 422
    assert p1.api(p1.hod, "POST", "/api/drafts", json={"semester_id": cls(1)}).status_code == 422
    assert p1.api(p1.hod, "POST", "/api/drafts", json={"semester_id": cls(1), "name": "Z", "extra": 1}).status_code == 422
    assert new_draft(p1, 2, "Even class").status_code == 409                       # Sem 2 does not run in an odd term
    assert p1.api(p1.hod, "POST", "/api/drafts", json={"semester_id": str(uuid.uuid4()), "name": "Z"}).status_code == 404
    assert new_draft(p1, 1, "Copy", copy_from=str(uuid.uuid4())).status_code == 404
    assert new_draft(p1, 1, "Copy", copy_from=S["c3"]).status_code == 404            # a draft of ANOTHER class cannot be copied


def test_rename_note_and_make_primary(p1):
    assert p1.api(p1.hod, "PATCH", f"/api/drafts/{S['b']}", json={"name": "Plan A"}).status_code == 409
    r = p1.api(p1.hod, "PATCH", f"/api/drafts/{S['b']}", json={"name": "Plan B (labs first)", "note": "labs in the morning"})
    assert r.status_code == 200 and r.json()["name"] == "Plan B (labs first)"
    assert p1.api(p1.hod, "PATCH", f"/api/drafts/{S['a']}", json={"is_primary": False}).status_code == 409      # a class keeps one primary draft
    assert p1.api(p1.hod, "PATCH", f"/api/drafts/{S['b']}", json={"is_primary": True}).status_code == 200
    rows = db_val("select count(*) from public.timetable_drafts where semester_id = $1 and is_primary", uuid.UUID(cls(1)))
    assert rows == 1 and detail(p1, S["b"])["draft"]["is_primary"] and not detail(p1, S["a"])["draft"]["is_primary"]
    assert p1.api(p1.hod, "PATCH", f"/api/drafts/{S['a']}", json={"is_primary": True}).status_code == 200      # and back
    assert p1.api(p1.hod, "PATCH", f"/api/drafts/{S['a']}", json={"bogus": 1}).status_code == 422


def test_the_number_of_drafts_per_class_is_limited(p1):
    made = [mk(p1, 5, f"D{i}") for i in range(12)]
    r = new_draft(p1, 5, "one too many")
    assert r.status_code == 409 and r.json()["code"] == "TOO_MANY_DRAFTS"
    for d in made:
        assert p1.api(p1.hod, "DELETE", f"/api/drafts/{d}").status_code == 200


# ====================================================================================================
# 2) the grid: details, placing, clashes
# ====================================================================================================
def test_setup_subjects_and_teachers(p1):
    maths = mk_subject(p1, 1, "D1-MAT", "Maths", credits=3)
    phys = mk_subject(p1, 1, "D1-PHY", "Physics", credits=3)
    lab = mk_subject(p1, 1, "D1-LAB", "Physics LAB", credits=1, semester_hours=30, is_lab=True)
    sol = mk_subject(p1, 1, "D1-SOL", "Science of Living", credits=2, special_kind="SOL")
    mat3 = mk_subject(p1, 3, "D3-MAT", "Maths III", credits=3)
    t_maths = mk_teacher(p1, "maths", [maths, mat3])
    t_phys = mk_teacher(p1, "phys", [phys, lab])
    t_sol = mk_teacher(p1, "sol", [sol])
    assign(p1, maths, t_maths)
    assign(p1, mat3, t_maths)                                  # ONE teacher teaches in class 1 AND class 3
    assign(p1, phys, t_phys)
    assign(p1, lab, t_phys)
    assign(p1, sol, t_sol)
    S.update(maths=maths, phys=phys, lab=lab, sol=sol, mat3=mat3, t_maths=t_maths, t_phys=t_phys, t_sol=t_sol)


def test_the_detail_is_everything_the_editor_needs(p1):
    d = detail(p1, S["a"])
    assert d["draft"]["name"] == "Plan A" and d["draft"]["class_label"] == S["label"][1] and d["draft"]["is_primary"]
    assert [x["name"] for x in d["siblings"]] == ["Plan A", "Plan B (labs first)"]
    assert d["active_term"]["name"] == "Odd 2026" and len(d["grid"]["periods"]) == 6 and d["grid"]["lunch"]["start"] == "13:10"
    assert [x["short"] for x in d["days"]][:5] == ["Mon", "Tue", "Wed", "Thu", "Fri"] and all(x["working"] for x in d["days"][:5]) and not d["days"][5]["working"]
    names = {s["name"]: s for s in d["subjects"]}
    assert names["Maths"]["required"] == 3 and names["Maths"]["placed"] == 0 and names["Maths"]["teacher_name"] == "Draft maths"
    assert names["Physics LAB"]["required"] == 1 and names["Physics LAB"]["is_lab"]
    assert names["Science of Living"]["special_kind"] == "SOL" and names["Science of Living"]["status"] == "missing"      # no fixed slot yet
    assert d["completeness"]["percent"] == 0 and d["placements"] == [] and d["locked"] == []


def test_placing_a_lecture_and_a_lab_block(p1):
    r = place(p1, S["a"], MON, 1, subject_id=S["maths"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["warnings"] == [] and body["key"]
    d = body["detail"]
    pl = d["placements"][0]
    assert (pl["day"], pl["start_period"], pl["length"], pl["time"]) == (MON, 1, 1, "10:00-11:00") and pl["entries"][0]["teacher_name"] == "Draft maths"
    assert sub_of(d, "Maths")["placed"] == 1 and sub_of(d, "Maths")["status"] == "missing"
    r = place(p1, S["a"], TUE, 3, subject_id=S["lab"])                              # P3 + P4 = around lunch: allowed with a warning
    assert r.status_code == 201 and any("lunch" in w for w in r.json()["warnings"])
    lab = next(p for p in r.json()["detail"]["placements"] if p["is_lab"])
    assert (lab["length"], lab["time"]) == (2, "12:10-14:40") and sub_of(r.json()["detail"], "Physics LAB")["status"] == "complete"
    assert db_val("select count(*) from public.draft_entries where draft_id = $1", uuid.UUID(S["a"])) == 2


def test_a_class_cannot_have_two_lectures_at_once(p1):
    r = place(p1, S["a"], MON, 1, subject_id=S["phys"])
    assert r.status_code == 422 and r.json()["code"] == "PLACEMENT_BLOCKED" and "already has 'Maths'" in r.json()["message"]
    assert r.json()["problems"]
    assert place(p1, S["a"], TUE, 4, subject_id=S["phys"]).status_code == 422          # the 2nd period of the lab block
    assert place(p1, S["a"], TUE, 5, subject_id=S["phys"]).status_code == 201


def test_placement_input_is_checked(p1):
    assert place(p1, S["a"], 0, 1, subject_id=S["maths"]).status_code == 422
    assert place(p1, S["a"], 7, 1, subject_id=S["maths"]).status_code == 422
    assert place(p1, S["a"], MON, 1).status_code == 422                                 # neither subject nor group
    assert place(p1, S["a"], MON, 1, subject_id=S["maths"], group_id=str(uuid.uuid4())).status_code == 422
    assert place(p1, S["a"], WED, 1, subject_id=str(uuid.uuid4())).status_code == 404
    assert place(p1, S["a"], WED, 1, subject_id=S["mat3"]).status_code == 404         # a subject of ANOTHER class
    assert place(p1, S["a"], WED, 7, subject_id=S["maths"]).status_code == 422          # period 7 does not exist
    assert place(p1, S["a"], WED, 1, subject_id=S["maths"], bogus=1).status_code == 422
    assert place(p1, S["a"], WED, 1, subject_id=S["sol"]).status_code == 422            # SOL comes from Fixed Slots
    assert p1.api(p1.hod, "POST", f"/api/drafts/{S['a']}/placements", json={"subject_id": S["maths"], "day": WED}).status_code == 422


def test_a_teacher_cannot_be_in_two_classes_at_once_but_only_primary_drafts_count(p1):
    # class 3 (primary draft 'Plan A') wants Maths-III at Monday P1: the same teacher teaches Maths in class 1 at that time
    r = place(p1, S["c3"], MON, 1, subject_id=S["mat3"])
    assert r.status_code == 422 and "Draft maths" in r.json()["message"] and S["label"][1] in r.json()["message"]
    # the options map says the same before the drop
    opts = p1.api(p1.hod, "POST", f"/api/drafts/{S['c3']}/options", json={"subject_id": S["mat3"]}).json()
    assert opts["cells"]["1:1"]["state"] == "blocked" and "Draft maths" in opts["cells"]["1:1"]["hard"][0]
    assert opts["cells"]["1:2"]["state"] in ("ok", "warn") and len(opts["cells"]) == 5 * 6 and opts["unit"]["length"] == 1
    # an ALTERNATIVE draft of class 1 may use Monday P1 freely: only the primary draft counts for other classes
    assert place(p1, S["b"], MON, 1, subject_id=S["maths"]).status_code == 201
    assert place(p1, S["c3"], MON, 2, subject_id=S["mat3"]).status_code == 201
    # ... but class 1's alternative draft is checked against class 3's primary draft
    assert place(p1, S["b"], MON, 2, subject_id=S["phys"]).status_code == 201           # (Physics has another teacher)
    r = p1.api(p1.hod, "POST", f"/api/drafts/{S['b']}/options", json={"subject_id": S["maths"]}).json()
    assert r["cells"]["1:2"]["state"] == "blocked"


def test_locked_slots_including_combined_ones_are_locked_in_every_covered_grid(p1):
    r = p1.api(p1.hod, "POST", "/api/fixed-slots", json={"semester_ids": [cls(1), cls(3)], "day": WED, "start_period": 5, "kind": "SOL"})
    assert r.status_code == 201 and r.json()["combined"] is True, r.text
    for n in (1, 3):
        draft = S["a"] if n == 1 else S["c3"]
        d = detail(p1, draft)
        assert len(d["locked"]) == 1 and d["locked"][0]["combined"] and len(d["locked"][0]["classes"]) == 2 and d["locked"][0]["label"].startswith("Science")
        assert (d["locked"][0]["day"], d["locked"][0]["start_period"], d["locked"][0]["length"]) == (WED, 5, 2)
        r = place(p1, draft, WED, 6, subject_id=S["maths"] if n == 1 else S["mat3"])
        assert r.status_code == 422 and "locked slot" in r.json()["message"] and "combined" in r.json()["message"]
    d = detail(p1, S["a"])
    assert sub_of(d, "Science of Living")["status"] == "complete"                  # SOL now counts as placed (its fixed slot)


def test_the_teacher_of_a_locked_slot_is_busy_in_other_classes(p1):
    # the SOL teacher also teaches Maths-III (class 3 is in the combined session, so use class 5)
    mat5 = mk_subject(p1, 5, "D5-MAT", "Maths V", credits=2)
    p1.api(p1.hod, "PATCH", f"/api/users/{S['t_sol']}", json={"subject_ids": [S["sol"], mat5]})
    assign(p1, mat5, S["t_sol"])
    c5 = mk(p1, 5, "Main 5")
    r = place(p1, c5, WED, 5, subject_id=mat5)
    assert r.status_code == 422 and "Draft sol" in r.json()["message"] and "locked slot" in r.json()["message"]
    assert place(p1, c5, WED, 4, subject_id=mat5).status_code == 201
    S["mat5"], S["c5"] = mat5, c5


def test_visiting_faculty_are_only_placed_on_their_days(p1):
    sv = mk_subject(p1, 3, "D3-VIS", "Visiting Subject", credits=2)
    tv = mk_teacher(p1, "visit", [sv], is_visiting=True, visiting_days=[1, 2, 3], max_weekly_hours=2)
    assign(p1, sv, tv)
    r = place(p1, S["c3"], THU, 1, subject_id=sv)
    assert r.status_code == 422 and "visiting faculty" in r.json()["message"] and "Monday, Tuesday, Wednesday" in r.json()["message"]
    opts = p1.api(p1.hod, "POST", f"/api/drafts/{S['c3']}/options", json={"subject_id": sv}).json()
    assert opts["cells"]["4:1"]["state"] == "blocked" and opts["cells"]["5:3"]["state"] == "blocked"
    assert place(p1, S["c3"], TUE, 1, subject_id=sv).status_code == 201
    assert place(p1, S["c3"], TUE, 2, subject_id=sv).status_code == 201                  # 2 hours = the weekly limit
    r = place(p1, S["c3"], WED, 1, subject_id=sv)
    assert r.status_code == 422 and "limit of 2 hours" in r.json()["message"]
    S["sv"] = sv


def test_off_days_and_early_end_times_cannot_be_used(p1):
    assert p1.api(p1.hod, "PUT", f"/api/timing/semesters/{cls(1)}/days", json={"days": [{"day": THU, "is_off": True}, {"day": FRI, "end_time": "14:40"}]}).status_code == 200
    r = place(p1, S["a"], THU, 1, subject_id=S["maths"])
    assert r.status_code == 422 and "OFF" in r.json()["message"]
    r = place(p1, S["a"], FRI, 5, subject_id=S["maths"])
    assert r.status_code == 422 and "14:40" in r.json()["message"]
    assert place(p1, S["a"], FRI, 4, subject_id=S["maths"]).status_code == 201
    opts = p1.api(p1.hod, "POST", f"/api/drafts/{S['a']}/options", json={"subject_id": S["phys"]}).json()
    assert all(opts["cells"][f"4:{p}"]["state"] == "blocked" for p in range(1, 7))
    assert p1.api(p1.hod, "PUT", f"/api/timing/semesters/{cls(1)}/days", json={"days": [{"day": THU}, {"day": FRI}]}).status_code == 200
    d = detail(p1, S["a"])
    assert d["days"][3]["is_off"] is False


def test_soft_rules_only_warn(p1):
    # Maths is already on Monday P1 and Friday P4: another one on Monday = the same subject twice in a day (3 of 3 lectures now)
    r = place(p1, S["a"], MON, 3, subject_id=S["maths"])
    assert r.status_code == 201 and any("already taught" in w for w in r.json()["warnings"])
    assert sub_of(r.json()["detail"], "Maths")["status"] == "complete"
    # a 4th lecture is more than the credits ask for: a warning too, still allowed
    r = place(p1, S["a"], WED, 1, subject_id=S["maths"])
    assert r.status_code == 201 and any("all its 3" in w for w in r.json()["warnings"])
    d = r.json()["detail"]
    assert sub_of(d, "Maths")["status"] == "over" and d["problems"]["clashes"] == 0 and d["problems"]["warnings"] >= 1
    assert p1.api(p1.hod, "DELETE", f"/api/drafts/{S['a']}/placements/{r.json()['key']}").status_code == 200


# ====================================================================================================
# 3) parallel (elective group) placements
# ====================================================================================================
def test_elective_group_options_are_one_parallel_placement(p1):
    g = p1.api(p1.hod, "POST", "/api/subjects/groups", json={"semester_id": cls(3), "name": "Major 3 - Tracks"}).json()["id"]
    ai = mk_subject(p1, 3, "D3-AI", "AI Major", credits=3, is_elective=True, elective_group_id=g)
    cy = mk_subject(p1, 3, "D3-CY", "Cyber Major", credits=3, is_elective=True, elective_group_id=g)
    t_ai = mk_teacher(p1, "ai", [ai])
    t_cy = mk_teacher(p1, "cy", [cy])
    assign(p1, ai, t_ai)
    assign(p1, cy, t_cy)
    S.update(g=g, ai=ai, cy=cy, t_ai=t_ai, t_cy=t_cy)
    d = detail(p1, S["c3"])
    assert [x["name"] for x in d["groups"]] == ["Major 3 - Tracks"] and d["groups"][0]["required"] == 3
    r = place(p1, S["c3"], MON, 4, subject_id=ai)                                     # one option alone: refused
    assert r.status_code == 422 and r.json()["code"] == "GROUP_ONLY"
    r = place(p1, S["c3"], MON, 4, group_id=g)
    assert r.status_code == 201, r.text
    d = r.json()["detail"]
    pl = next(p for p in d["placements"] if p["parallel"])
    assert {e["subject_name"]: e["teacher_name"] for e in pl["entries"]} == {"AI Major": "Draft ai", "Cyber Major": "Draft cy"}
    assert pl["problems"] == [] and d["problems"]["clashes"] == 0                      # NO false clash between the options
    assert d["groups"][0]["placed"] == 1 and sub_of(d, "AI Major")["placed"] == 1 and sub_of(d, "Cyber Major")["placed"] == 1
    assert db_val("select count(distinct parallel_key) from public.draft_entries where draft_id = $1 and subject_id in ($2, $3)", uuid.UUID(S["c3"]), uuid.UUID(ai), uuid.UUID(cy)) == 1
    # the class is busy for any other lecture at that time
    r = place(p1, S["c3"], MON, 4, subject_id=S["mat3"])
    assert r.status_code == 422 and "already has" in r.json()["message"]
    S["par_key"] = pl["key"]


def test_each_option_teacher_is_checked_against_the_other_classes(p1):
    # class 5's draft puts the Cyber teacher somewhere ... then the group cannot go to that time
    s5 = mk_subject(p1, 5, "D5-CY", "Cyber Elective V", credits=2)
    p1.api(p1.hod, "PATCH", f"/api/users/{S['t_cy']}", json={"subject_ids": [S["cy"], s5]})
    assign(p1, s5, S["t_cy"])
    assert place(p1, S["c5"], TUE, 1, subject_id=s5).status_code == 201
    r = place(p1, S["c3"], TUE, 1, group_id=S["g"])
    assert r.status_code == 422 and "Draft cy" in r.json()["message"]


def test_a_parallel_placement_moves_and_disappears_as_one(p1):
    r = p1.api(p1.hod, "PATCH", f"/api/drafts/{S['c3']}/placements/{S['par_key']}", json={"day": THU, "start_period": 1})
    assert r.status_code == 200, r.text
    pl = next(p for p in r.json()["detail"]["placements"] if p["parallel"])
    assert (pl["day"], pl["start_period"]) == (THU, 1) and len(pl["entries"]) == 2
    assert db_val("select count(*) from public.draft_entries where draft_id = $1 and day = $2 and start_period = 1", uuid.UUID(S["c3"]), THU) == 2
    r = p1.api(p1.hod, "DELETE", f"/api/drafts/{S['c3']}/placements/{S['par_key']}")
    assert r.status_code == 200 and not any(p["parallel"] for p in r.json()["detail"]["placements"])
    assert db_val("select count(*) from public.draft_entries where draft_id = $1 and subject_id in ($2, $3)", uuid.UUID(S["c3"]), uuid.UUID(S["ai"]), uuid.UUID(S["cy"])) == 0


# ====================================================================================================
# 4) move / swap / remove / clear
# ====================================================================================================
def test_move_swap_remove_and_clear(p1):
    d = mk(p1, 1, "Scratch")
    a = place(p1, d, TUE, 1, subject_id=S["maths"]).json()["key"]
    b = place(p1, d, TUE, 3, subject_id=S["phys"]).json()["key"]
    r = p1.api(p1.hod, "PATCH", f"/api/drafts/{d}/placements/{a}", json={"day": WED, "start_period": 2})
    assert r.status_code == 200
    assert p1.api(p1.hod, "PATCH", f"/api/drafts/{d}/placements/{a}", json={"day": WED, "start_period": 5}).status_code == 422       # locked SOL
    assert p1.api(p1.hod, "PATCH", f"/api/drafts/{d}/placements/{a}", json={"day": TUE, "start_period": 3}).status_code == 422       # class busy (Physics)
    assert p1.api(p1.hod, "PATCH", f"/api/drafts/{d}/placements/{uuid.uuid4()}", json={"day": 1, "start_period": 1}).status_code == 404
    r = p1.api(p1.hod, "POST", f"/api/drafts/{d}/swap", json={"placement_a": a, "placement_b": b})
    assert r.status_code == 200
    pos = {p["key"]: (p["day"], p["start_period"]) for p in r.json()["detail"]["placements"]}
    assert pos[a] == (TUE, 3) and pos[b] == (WED, 2)
    assert p1.api(p1.hod, "POST", f"/api/drafts/{d}/swap", json={"placement_a": a, "placement_b": a}).status_code == 422
    lab = place(p1, d, MON, 1, subject_id=S["lab"]).json()["key"]
    r = p1.api(p1.hod, "POST", f"/api/drafts/{d}/swap", json={"placement_a": a, "placement_b": lab})
    assert r.status_code == 422 and "2-period" in r.json()["message"]
    # the options of a placement that is picked up: moving cells + swap answers for the other placements
    opts = p1.api(p1.hod, "POST", f"/api/drafts/{d}/options", json={"placement_key": a}).json()
    assert opts["cells"]["3:3"]["state"] in ("ok", "warn") and opts["swaps"][b]["ok"] is True and opts["swaps"][lab]["ok"] is False
    assert p1.api(p1.hod, "DELETE", f"/api/drafts/{d}/placements/{a}").status_code == 200
    assert p1.api(p1.hod, "DELETE", f"/api/drafts/{d}/placements/{a}").status_code == 404
    r = p1.api(p1.hod, "POST", f"/api/drafts/{d}/clear")
    assert r.status_code == 200 and r.json()["cleared"] == 2 and r.json()["detail"]["placements"] == []
    assert p1.api(p1.hod, "DELETE", f"/api/drafts/{d}").status_code == 200


def test_the_database_itself_refuses_two_lectures_at_one_time_in_a_draft(p1):
    import asyncpg

    async def attempt() -> str:
        conn = await connect_one()
        try:
            try:
                await conn.execute(
                    "insert into public.draft_entries (draft_id, school_id, semester_id, subject_id, day, start_period, length) values ($1,$2,$3,$4,$5,1,1)",
                    uuid.UUID(S["a"]), uuid.UUID(p1.school), uuid.UUID(cls(1)), uuid.UUID(S["phys"]), MON)
                return "accepted"
            except asyncpg.ExclusionViolationError:
                return "refused"
        finally:
            await conn.close()

    assert run(attempt()) == "refused"            # Monday P1 holds Maths already


def test_copy_from_another_draft_and_deleting_the_primary(p1):
    src = detail(p1, S["a"])
    r = new_draft(p1, 1, "Copy of A", copy_from=S["a"])
    assert r.status_code == 201 and r.json()["copied"] == len(src["placements"]) and r.json()["is_primary"] is False
    cp = detail(p1, r.json()["id"])
    assert [(p["day"], p["start_period"], p["entries"][0]["subject_name"]) for p in cp["placements"]] == [(p["day"], p["start_period"], p["entries"][0]["subject_name"]) for p in src["placements"]]
    assert cp["completeness"] == src["completeness"]
    # the copy is independent
    key = cp["placements"][0]["key"]
    assert p1.api(p1.hod, "DELETE", f"/api/drafts/{r.json()['id']}/placements/{key}").status_code == 200
    assert len(detail(p1, S["a"])["placements"]) == len(src["placements"])
    S["copy"] = r.json()["id"]
    # deleting the primary draft hands the role to another draft of the class
    assert p1.api(p1.hod, "DELETE", f"/api/drafts/{S['a']}").status_code == 200
    assert p1.api(p1.hod, "GET", f"/api/drafts/{S['a']}").status_code == 404
    assert db_val("select count(*) from public.timetable_drafts where semester_id = $1 and is_primary", uuid.UUID(cls(1))) == 1


# ====================================================================================================
# 5) the conflict report
# ====================================================================================================
def test_the_conflict_report(p1):
    # make a REAL clash: put class 3's Maths-III where class 1's Maths is, using the alternative-draft rules, then switch primary
    prim = db_val("select id from public.timetable_drafts where semester_id = $1 and is_primary", uuid.UUID(cls(1)))
    S["prim1"] = str(prim)
    rep = p1.api(p1.hod, "GET", "/api/conflicts").json()
    assert rep["active_term"]["name"] == "Odd 2026" and rep["scope"] == "primary" and {"clashes", "missing", "warnings"} <= set(rep["counts"])
    labels = {i["class_label"] for i in rep["items"]}
    assert S["label"][1] in labels and S["label"][3] in labels
    missing = [i for i in rep["items"] if i["kind"] == "missing" and i["class_label"] == S["label"][3]]
    assert missing and all(i["message"] for i in missing)
    # class 5 has a draft; classes with no draft say so
    no_draft = [i for i in rep["items"] if i["code"] == "NO_DRAFT"]
    assert all(i["draft_id"] is None for i in no_draft)
    # scope + filter validation
    assert p1.api(p1.hod, "GET", "/api/conflicts?scope=weird").status_code == 422
    only3 = p1.api(p1.hod, "GET", f"/api/conflicts?semester_id={cls(3)}").json()
    assert {i["class_label"] for i in only3["items"]} == {S["label"][3]}
    assert p1.api(p1.hod, "GET", f"/api/conflicts?semester_id={cls(2)}").status_code == 404           # a class of another term
    assert p1.api(p1.hod, "GET", f"/api/conflicts?semester_id={uuid.uuid4()}").status_code == 404
    allr = p1.api(p1.hod, "GET", "/api/conflicts?scope=all").json()
    assert len(allr["items"]) >= len(rep["items"])


def test_a_clash_that_appears_later_is_reported_in_both_classes(p1):
    # class 1's primary draft has Maths at Monday P1 (the 'copy'); class 3 now moves Maths-III onto the same time via the DATABASE
    # (as if the assignment had changed afterwards): teacher double-booked
    c3 = detail(p1, S["c3"])
    mine = next(p for p in c3["placements"] if p["entries"][0]["subject_name"] == "Maths III")
    p1_dets = detail(p1, S["prim1"])
    maths1 = next(p for p in p1_dets["placements"] if p["entries"][0]["subject_name"] == "Maths")
    db_exec("update public.draft_entries set day = $2, start_period = $3 where id = $1", uuid.UUID(mine["entries"][0]["id"]), maths1["day"], maths1["start_period"])
    rep = p1.api(p1.hod, "GET", "/api/conflicts").json()
    clashes = [i for i in rep["items"] if i["kind"] == "clash" and i["code"] == "TEACHER_BUSY"]
    assert {c["class_label"] for c in clashes} == {S["label"][1], S["label"][3]} and rep["counts"]["clashes"] == len(
        [i for i in rep["items"] if i["kind"] == "clash"])
    item = next(c for c in clashes if c["class_label"] == S["label"][3])
    assert (item["day"], item["start_period"], item["day_name"]) == (maths1["day"], maths1["start_period"], "Monday") and item["placement_key"] and item["draft_id"] == S["c3"]
    d = detail(p1, S["c3"])
    bad = next(p for p in d["placements"] if p["problems"])
    assert "Draft maths" in bad["problems"][0] and d["problems"]["clashes"] >= 1
    # the list screen counts it too
    row = next(c for c in p1.api(p1.hod, "GET", "/api/drafts").json()["classes"] if c["semester_id"] == cls(3))
    assert row["drafts"][0]["clashes"] >= 1
    # put it back
    db_exec("update public.draft_entries set day = $2, start_period = $3 where id = $1", uuid.UUID(mine["entries"][0]["id"]), mine["day"], mine["start_period"])
    assert p1.api(p1.hod, "GET", "/api/conflicts").json()["counts"]["clashes"] == 0


def test_a_subject_that_is_in_a_draft_cannot_be_deleted(p1):
    r = p1.api(p1.hod, "DELETE", f"/api/subjects/{S['mat5']}")
    assert r.status_code == 409 and r.json()["code"] == "IN_USE"


def test_drafts_of_an_old_term_are_history(p1):
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "even", "year": 2097}).status_code == 200
    r = p1.api(p1.hod, "GET", f"/api/drafts/{S['c3']}")
    assert r.status_code == 409 and r.json()["code"] == "TERM_NOT_ACTIVE"
    assert place(p1, S["c3"], MON, 1, subject_id=S["mat3"]).status_code == 409
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026}).status_code == 200
    assert p1.api(p1.hod, "GET", f"/api/drafts/{S['c3']}").status_code == 200


# ====================================================================================================
# 6) the audit log, roles, schools, direct database access
# ====================================================================================================
def test_every_change_is_audited(p1):
    me = uuid.UUID(p1.hod.account.user_id)
    for entity, action in (("timetable_draft", "create"), ("timetable_draft", "update"), ("timetable_draft", "delete"),
                           ("draft_placement", "create"), ("draft_placement", "update"), ("draft_placement", "delete")):
        n = db_val("select count(*) from public.audit_log where entity = $1 and action = $2 and actor_id = $3", entity, action, me)
        assert n > 0, (entity, action)
    row = db_fetch("select summary from public.audit_log where entity = 'draft_placement' and action = 'create' and actor_id = $1 order by id limit 1", me)[0]
    assert "placed" in row["summary"] and "draft" in row["summary"]


def test_teachers_never_see_drafts(p1):
    for method, path, body in (
        ("GET", "/api/drafts", None), ("POST", "/api/drafts", {"semester_id": cls(1), "name": "T"}), ("GET", f"/api/drafts/{S['c3']}", None),
        ("PATCH", f"/api/drafts/{S['c3']}", {"name": "T"}), ("DELETE", f"/api/drafts/{S['c3']}", None),
        ("POST", f"/api/drafts/{S['c3']}/options", {"subject_id": S["mat3"]}), ("POST", f"/api/drafts/{S['c3']}/placements", {"subject_id": S["mat3"], "day": 1, "start_period": 1}),
        ("POST", f"/api/drafts/{S['c3']}/clear", None), ("GET", "/api/conflicts", None),
    ):
        r = p1.api(p1.teacher, method, path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, path, r.status_code)


def test_an_hod_cannot_reach_the_drafts_of_another_school(p1):
    # a draft of school B (made by the database) does not exist for the HOD of school P
    sem_b = p1.b_sems[1]
    gid = uuid.uuid4()
    db_exec(
        "insert into public.timetable_drafts (id, school_id, term_id, semester_id, name, is_primary) "
        "select $1, $2, t.id, $3, 'ZZ Foreign draft', true from public.academic_terms t where t.is_active",
        gid, uuid.UUID(p1.school_b), uuid.UUID(sem_b))
    S["foreign"] = str(gid)
    for method, path, body in (
        ("GET", f"/api/drafts/{gid}", None), ("PATCH", f"/api/drafts/{gid}", {"name": "x"}), ("DELETE", f"/api/drafts/{gid}", None),
        ("POST", f"/api/drafts/{gid}/placements", {"subject_id": p1.b_subject, "day": 1, "start_period": 1}),
        ("POST", f"/api/drafts/{gid}/options", {"subject_id": p1.b_subject}), ("POST", f"/api/drafts/{gid}/clear", None),
    ):
        r = p1.api(p1.hod, method, path, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, path, r.status_code)
    assert p1.api(p1.hod, "GET", f"/api/drafts?school_id={p1.school_b}").status_code == 403
    assert p1.api(p1.hod, "GET", f"/api/conflicts?school_id={p1.school_b}").status_code == 403
    r = p1.api(p1.hod, "POST", "/api/drafts", json={"semester_id": p1.b_sems[3], "name": "Sneaky"})
    assert r.status_code == 404
    names = {d["name"] for c in p1.api(p1.hod, "GET", "/api/drafts").json()["classes"] for d in c["drafts"]}
    assert "ZZ Foreign draft" not in names
    # the admin must name the school, and may work in any school
    assert p1.api(p1.admin, "GET", "/api/drafts").status_code == 422
    assert p1.api(p1.admin, "GET", f"/api/drafts?school_id={p1.school}").status_code == 200
    assert p1.api(p1.admin, "GET", f"/api/drafts/{S['c3']}").status_code == 200
    assert p1.api(p1.admin, "GET", f"/api/drafts/{gid}").status_code == 200
    assert p1.api(p1.admin, "DELETE", f"/api/drafts/{gid}").status_code == 200


REST = f"{__import__('app.config', fromlist=['get_settings']).get_settings().supabase_url}/rest/v1"


def rest(method: str, path: str, token: str | None = None, **kw) -> httpx.Response:
    from app.config import get_settings
    headers = {"apikey": get_settings().supabase_anon_key}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.request(method, f"{REST}{path}", headers=headers, timeout=40, **kw)


@pytest.mark.parametrize("table", ["timetable_drafts", "draft_entries"])
def test_drafts_cannot_be_read_by_teachers_or_visitors_and_nobody_writes_through_the_data_api(p1, table):
    assert rest("GET", f"/{table}?select=id").status_code in (401, 403)                                       # not logged in
    def visible(who) -> list:
        r = rest("GET", f"/{table}?select=school_id", who.token)
        assert r.status_code == 200, r.text[:150]
        return r.json()

    assert visible(p1.teacher) == [] and visible(p1.teacher_b) == []                                           # teachers: nothing, even in their own school
    assert {row["school_id"] for row in visible(p1.hod)} == {p1.school}                                        # the HOD: own school only
    # the admin sees them too (asked for this school only: the data API returns at most 1000 rows, and the real school now holds more drafts than that)
    admin_rows = rest("GET", f"/{table}?select=school_id&school_id=eq.{p1.school}", p1.admin.token)
    assert admin_rows.status_code == 200 and p1.school in {row["school_id"] for row in admin_rows.json()}
    before = db_val(f"select count(*) from public.{table}")
    for who in (p1.admin, p1.hod, p1.teacher):
        assert rest("POST", f"/{table}", who.token, json={"school_id": p1.school}).status_code in (401, 403)
        assert rest("PATCH", f"/{table}?school_id=eq.{p1.school}", who.token, json={"school_id": p1.school}).status_code in (401, 403)
        assert rest("DELETE", f"/{table}?school_id=eq.{p1.school}", who.token).status_code in (401, 403)
    assert db_val(f"select count(*) from public.{table}") == before
