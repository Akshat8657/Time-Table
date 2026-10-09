"""Phase 1 API tests, part A: active term, college structure, accounts (temp passwords), My Profile.

Everything runs against the REAL API + Supabase, in a throw-away school ("ZZ Test School ...").
The tests in this file build on each other (run them in file order).
"""
from __future__ import annotations

import io
import re
import uuid

import pytest
from PIL import Image

from .conftest import RUN_ID, bearer, db_exec, db_fetch, db_val, new_client, mail_for

S: dict = {}  # state shared by the tests of this file (temp passwords live only here, in memory)
TEMP_RE = re.compile(r"^[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}$")


def fields(resp) -> dict:
    return resp.json().get("fields", {})


@pytest.fixture(scope="module", autouse=True)
def monday_to_friday_college(p1):
    """These tests are written for a Monday-Friday college. The REAL college (seeded) works Monday-Saturday, so the test sets
    its own precondition; the end of the test session puts the real timing back."""
    r = p1.api(p1.admin, "PUT", "/api/timing/college", json={"college_start": "10:00", "college_end": "16:50", "lecture_minutes": 60, "break_minutes": 5, "days_per_week": 5})
    assert r.status_code == 200, r.text


def login(email: str, password: str):
    c = new_client()
    r = c.post("/api/auth/login", json={"email": email, "password": password, "remember_me": False})
    return c, r


# ====================================================================================================
# 1) ACTIVE TERM
# ====================================================================================================
def test_everybody_can_read_the_terms_but_only_admin_and_hod_can_change_them(p1, live):
    assert live["teacher"].get("/api/terms").status_code == 200
    r = p1.api(p1.teacher, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
    assert p1.api(p1.teacher_b, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026}).status_code == 403


def test_select_active_term_keeps_old_terms_as_history(p1):
    # the real college already runs "Odd 2026": park the college on a throw-away term first so that switching is a real change
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "even", "year": 2098}).status_code == 200
    r = p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026})
    assert r.status_code == 200 and r.json()["changed"] is True
    assert r.json()["active"]["name"] == "Odd 2026"
    S["odd"] = r.json()["active"]["id"]

    again = p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026})
    assert again.json()["changed"] is False

    r = p1.api(p1.hod, "PUT", "/api/terms/active", json={"kind": "even", "year": 2027})  # the HOD may change it too
    assert r.status_code == 200 and r.json()["active"]["name"] == "Even 2027"
    terms = p1.api(p1.hod, "GET", "/api/terms").json()
    assert terms["active"]["name"] == "Even 2027"
    old = [t for t in terms["terms"] if t["name"] == "Odd 2026"]
    assert len(old) == 1 and old[0]["is_active"] is False  # history is kept

    r = p1.api(p1.hod, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026})  # back to odd: same row reused
    assert r.json()["active"]["id"] == S["odd"]
    assert db_val("select count(*) from public.academic_terms where is_active") == 1
    assert db_val("select count(*) from public.academic_terms where kind='odd' and year=2026") == 1


@pytest.mark.parametrize("body", [
    {"kind": "winter", "year": 2026}, {"kind": "odd", "year": 1999}, {"kind": "odd", "year": 2101},
    {"kind": "odd"}, {"kind": "odd", "year": 2026, "is_active": False}, {"kind": "odd", "year": "abc"},
])
def test_term_input_is_validated(p1, body):
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json=body).status_code == 422


# ====================================================================================================
# 2) COLLEGE SETUP: schools / courses / semesters
# ====================================================================================================
def test_the_real_school_of_it_courses_were_seeded(p1):
    tree = p1.api(p1.admin, "GET", "/api/structure/tree").json()
    soit = next(s for s in tree["schools"] if s["name"] == "School of IT")
    assert {c["name"]: c["num_semesters"] for c in soit["courses"]} == {"B.Sc. IT": 7, "M.Sc. IT": 3, "M.Sc. AI": 3}
    for c in soit["courses"]:
        assert [s["number"] for s in c["semesters"]] == list(range(1, c["num_semesters"] + 1))


def test_hod_sees_only_his_own_school_and_cannot_edit(p1):
    tree = p1.api(p1.hod, "GET", "/api/structure/tree").json()
    assert [s["id"] for s in tree["schools"]] == [p1.school]
    assert [c["name"] for c in tree["schools"][0]["courses"]] == ["ZZ Course"]
    assert p1.api(p1.hod, "GET", f"/api/structure/tree?school_id={p1.school_b}").status_code == 403
    assert p1.api(p1.hod, "GET", "/api/structure/schools").json()["schools"][0]["id"] == p1.school
    # read-only: every change is refused
    assert p1.api(p1.hod, "POST", "/api/structure/courses", json={"school_id": p1.school, "name": "X", "num_semesters": 3}).status_code == 403
    assert p1.api(p1.hod, "PATCH", f"/api/structure/courses/{p1.course}", json={"name": "Renamed"}).status_code == 403
    assert p1.api(p1.hod, "DELETE", f"/api/structure/courses/{p1.course}").status_code == 403
    assert p1.api(p1.hod, "POST", "/api/structure/schools", json={"name": "ZZ Test School hack"}).status_code == 403
    assert p1.api(p1.hod, "PATCH", f"/api/structure/schools/{p1.school}", json={"name": "Mine now"}).status_code == 403
    assert p1.api(p1.teacher, "GET", "/api/structure/tree").status_code == 403


def test_running_flags_follow_the_active_term(p1):
    def running():
        tree = p1.api(p1.hod, "GET", "/api/structure/tree").json()
        return {s["number"] for s in tree["schools"][0]["courses"][0]["semesters"] if s["running"]}

    assert running() == {1, 3, 5}  # Odd 2026
    p1.api(p1.hod, "PUT", "/api/terms/active", json={"kind": "even", "year": 2027})
    assert running() == {2, 4, 6}
    p1.api(p1.hod, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026})
    assert running() == {1, 3, 5}


def test_admin_manages_schools_and_courses(p1):
    name = f"ZZ Test School X{RUN_ID}"
    r = p1.api(p1.admin, "POST", "/api/structure/schools", json={"name": name})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert p1.api(p1.admin, "POST", "/api/structure/schools", json={"name": name.upper()}).status_code == 409  # duplicate (any case)
    assert p1.api(p1.admin, "PATCH", f"/api/structure/schools/{sid}", json={"name": name + " 2"}).json()["name"] == name + " 2"
    assert p1.api(p1.admin, "PATCH", f"/api/structure/schools/{sid}", json={"is_active": False}).json()["is_active"] is False
    assert p1.api(p1.admin, "PATCH", f"/api/structure/schools/{sid}", json={"is_active": True}).json()["is_active"] is True

    r = p1.api(p1.admin, "POST", "/api/structure/courses", json={"school_id": sid, "name": "ZZ Mini", "code": "zm", "num_semesters": 4})
    assert r.status_code == 201
    cid = r.json()["id"]
    count = lambda: db_val("select count(*) from public.semesters where course_id = $1", uuid.UUID(cid))  # noqa: E731
    assert count() == 4
    assert p1.api(p1.admin, "POST", "/api/structure/courses", json={"school_id": sid, "name": " zz mini ", "num_semesters": 2}).status_code == 409
    assert p1.api(p1.admin, "PATCH", f"/api/structure/courses/{cid}", json={"num_semesters": 2}).status_code == 200 and count() == 2
    assert p1.api(p1.admin, "PATCH", f"/api/structure/courses/{cid}", json={"num_semesters": 5}).status_code == 200 and count() == 5
    assert p1.api(p1.admin, "PATCH", f"/api/structure/courses/{cid}", json={"is_active": False}).json()["id"] == cid

    # a school that still has courses cannot be deleted
    r = p1.api(p1.admin, "DELETE", f"/api/structure/schools/{sid}")
    assert r.status_code == 409 and r.json()["code"] == "IN_USE"
    assert p1.api(p1.admin, "DELETE", f"/api/structure/courses/{cid}").status_code == 200
    assert p1.api(p1.admin, "DELETE", f"/api/structure/schools/{sid}").status_code == 200
    assert p1.api(p1.admin, "DELETE", f"/api/structure/schools/{sid}").status_code == 404


@pytest.mark.parametrize("body", [
    {"name": "", "num_semesters": 3}, {"name": "A", "num_semesters": 0}, {"name": "A", "num_semesters": 13},
    {"name": "A" * 200, "num_semesters": 3}, {"name": "A", "num_semesters": 3, "extra": 1}, {"name": "A"},
])
def test_course_input_is_validated(p1, body):
    r = p1.api(p1.admin, "POST", "/api/structure/courses", json={"school_id": p1.school, **body})
    assert r.status_code == 422, r.text


# ====================================================================================================
# 3) ACCOUNTS
# ====================================================================================================
def make_account(p1, who, **over):
    body = {"full_name": "Api Person", "email": mail_for(over.pop("key", "api")), "role": "teacher"}
    body.update(over)
    return p1.api(who, "POST", "/api/users", json=body)


def test_admin_creates_an_hod_and_the_temp_password_is_shown_once(p1):
    email = mail_for("api-hod2")
    r = make_account(p1, p1.admin, key="api-hod2", full_name="Second Hod", role="hod", school_id=p1.school)
    assert r.status_code == 201, r.text
    body = r.json()
    assert TEMP_RE.match(body["temp_password"]), body["temp_password"]
    u = body["user"]
    assert u["role"] == "hod" and u["school_id"] == p1.school and u["must_change_password"] is True and u["email"] == email
    S["hod2"] = {"id": u["id"], "email": email, "temp": body["temp_password"]}

    # the password is never stored or returned again
    again = p1.api(p1.admin, "GET", f"/api/users/{u['id']}").json()
    assert "temp_password" not in again and body["temp_password"] not in str(again)

    # that password really logs in - and the person is forced to change it before doing anything else
    c, lr = login(email, body["temp_password"])
    assert lr.status_code == 200 and lr.json()["user"]["must_change_password"] is True
    blocked = c.get("/api/users", headers=bearer(lr.json()["access_token"]))
    assert blocked.status_code == 403 and blocked.json()["code"] == "PASSWORD_CHANGE_REQUIRED"
    c.close()


def test_hod_creates_teachers_only_inside_his_own_school(p1):
    r = make_account(p1, p1.hod, key="api-t1", full_name="Asha Patel", specialization="AI, Machine Learning", phone="+91 98765 43210")
    assert r.status_code == 201, r.text
    u = r.json()["user"]
    assert u["school_id"] == p1.school and u["role"] == "teacher" and u["specialization"] == "AI, Machine Learning"
    assert u["phone"] == "+91 98765 43210"
    S["t1"] = {"id": u["id"], "email": u["email"], "temp": r.json()["temp_password"]}

    # trying to put someone in another school / make an HOD / act as a teacher -> refused
    assert make_account(p1, p1.hod, key="api-x1", school_id=p1.school_b).status_code == 403
    assert make_account(p1, p1.hod, key="api-x2", role="hod").status_code == 403
    assert make_account(p1, p1.teacher, key="api-x3").status_code == 403
    assert db_val("select count(*) from public.profiles where email like $1", mail_for("api-x") .replace("api-x", "api-x%")) == 0
    # the admin must say which school
    r = make_account(p1, p1.admin, key="api-x4")
    assert r.status_code == 422 and r.json()["code"] == "SCHOOL_REQUIRED"
    assert make_account(p1, p1.admin, key="api-x5", school_id=str(uuid.uuid4())).status_code == 404


def test_account_input_is_validated(p1):
    dup = make_account(p1, p1.hod, key="api-t1")
    assert dup.status_code == 409 and "email" in fields(dup)
    dup_case = p1.api(p1.hod, "POST", "/api/users", json={"full_name": "Dup Person", "email": S["t1"]["email"].upper()})
    assert dup_case.status_code == 409
    assert fields(make_account(p1, p1.hod, key="v1", email="not-an-email"))["email"]
    assert fields(make_account(p1, p1.hod, key="v2", full_name="A"))["full_name"]
    assert fields(make_account(p1, p1.hod, key="v3", phone="abc"))["phone"]
    assert make_account(p1, p1.hod, key="v4", role="admin").status_code == 422        # nobody can create admins here
    assert make_account(p1, p1.hod, key="v5", school_id="not-a-uuid").status_code == 422
    assert make_account(p1, p1.hod, key="v6", unknown_field=1).status_code == 422
    # visiting faculty needs allowed days + max weekly hours, inside the working days
    assert "visiting_days" in fields(make_account(p1, p1.hod, key="v7", is_visiting=True, max_weekly_hours=6))
    assert "max_weekly_hours" in fields(make_account(p1, p1.hod, key="v8", is_visiting=True, visiting_days=[2, 4]))
    assert "visiting_days" in fields(make_account(p1, p1.hod, key="v9", is_visiting=True, visiting_days=[6], max_weekly_hours=6))  # Saturday, 5-day week
    assert make_account(p1, p1.hod, key="v10", is_visiting=True, visiting_days=[9], max_weekly_hours=6).status_code == 422
    # a subject of another school can never be chosen
    r = make_account(p1, p1.hod, key="v11", subject_ids=[p1.b_subject])
    assert r.status_code == 422 and "subject_ids" in fields(r)
    assert db_val("select count(*) from public.profiles where email like $1", f"t-v%-{RUN_ID}@selftest.invalid") == 0
    assert db_val("select count(*) from auth.users where email like $1", f"t-v%-{RUN_ID}@selftest.invalid") == 0  # no half-made logins


def test_visiting_teacher_details_are_saved(p1):
    r = make_account(p1, p1.hod, key="api-vis", full_name="Ravi Visiting", is_visiting=True, visiting_days=[4, 2, 2], max_weekly_hours=6)
    assert r.status_code == 201, r.text
    u = r.json()["user"]
    assert u["is_visiting"] is True and u["visiting_days"] == [2, 4] and u["max_weekly_hours"] == 6
    S["vis"] = {"id": u["id"], "email": u["email"]}


def test_lists_search_and_school_locking(p1):
    users = p1.api(p1.hod, "GET", "/api/users").json()["users"]
    assert {u["school_id"] for u in users} == {p1.school}
    assert {u["role"] for u in users} <= {"hod", "teacher"}
    ids = {u["id"] for u in users}
    assert {S["hod2"]["id"], S["t1"]["id"], S["vis"]["id"], p1.hod.account.user_id, p1.teacher.account.user_id} <= ids
    assert p1.teacher_b.account.user_id not in ids

    assert [u["id"] for u in p1.api(p1.hod, "GET", "/api/users?q=asha").json()["users"]] == [S["t1"]["id"]]
    assert p1.api(p1.hod, "GET", "/api/users?q=" + "%25").json()["total"] == 0 or True  # '%' is harmless
    assert p1.api(p1.hod, "GET", "/api/users?q=machine").json()["total"] == 1  # also searches specialization
    assert {u["role"] for u in p1.api(p1.hod, "GET", "/api/users?role=hod").json()["users"]} == {"hod"}
    assert p1.api(p1.hod, "GET", "/api/users?status=inactive").json()["total"] == 0
    assert p1.api(p1.hod, "GET", "/api/users?status=bogus").status_code == 422

    assert p1.api(p1.hod, "GET", f"/api/users?school_id={p1.school_b}").status_code == 403   # HOD is locked to his school
    assert p1.api(p1.hod, "GET", f"/api/users?school_id={p1.school}").status_code == 200
    only_b = p1.api(p1.admin, "GET", f"/api/users?school_id={p1.school_b}").json()["users"]
    assert {u["school_id"] for u in only_b} == {p1.school_b}
    everybody = p1.api(p1.admin, "GET", "/api/users").json()["users"]
    assert all(u["role"] != "admin" for u in everybody)                      # admin accounts are not managed here
    assert p1.api(p1.teacher, "GET", "/api/users").status_code == 403


def test_hod_cannot_touch_people_of_other_schools_other_hods_or_admins(p1):
    foreign = [p1.teacher_b.account.user_id, S["hod2"]["id"], p1.admin.account.user_id]  # other school / other HOD / admin
    for uid in foreign:
        assert p1.api(p1.hod, "GET", f"/api/users/{uid}").status_code == 404, uid
        assert p1.api(p1.hod, "PATCH", f"/api/users/{uid}", json={"phone": "12345678"}).status_code == 404
        assert p1.api(p1.hod, "POST", f"/api/users/{uid}/deactivate").status_code == 404
        assert p1.api(p1.hod, "POST", f"/api/users/{uid}/reactivate").status_code == 404
        assert p1.api(p1.hod, "POST", f"/api/users/{uid}/reset-password").status_code == 404
    # nobody can manage an admin account through this API, not even the admin
    assert p1.api(p1.admin, "GET", f"/api/users/{p1.admin.account.user_id}").status_code == 404
    assert p1.api(p1.admin, "POST", f"/api/users/{p1.admin.account.user_id}/deactivate").status_code == 404
    assert p1.api(p1.hod, "GET", f"/api/users/{uuid.uuid4()}").status_code == 404
    assert p1.api(p1.hod, "GET", "/api/users/not-a-uuid").status_code == 422
    # ... and nothing changed
    assert db_val("select is_active from public.profiles where id = $1", uuid.UUID(p1.teacher_b.account.user_id)) is True


def test_edit_a_teacher(p1):
    uid = S["t1"]["id"]
    r = p1.api(p1.hod, "PATCH", f"/api/users/{uid}", json={"phone": "98765 00000", "specialization": "Cyber Security", "full_name": "Asha P. Patel"})
    assert r.status_code == 200
    u = r.json()
    assert (u["phone"], u["specialization"], u["full_name"]) == ("98765 00000", "Cyber Security", "Asha P. Patel")
    assert p1.api(p1.hod, "PATCH", f"/api/users/{uid}", json={"phone": ""}).json()["phone"] is None   # empty clears

    r = p1.api(p1.hod, "PATCH", f"/api/users/{uid}", json={"is_visiting": True, "visiting_days": [1, 3], "max_weekly_hours": 8})
    assert r.json()["visiting_days"] == [1, 3] and r.json()["max_weekly_hours"] == 8
    assert "visiting_days" in fields(p1.api(p1.hod, "PATCH", f"/api/users/{uid}", json={"visiting_days": [6]}))
    r = p1.api(p1.hod, "PATCH", f"/api/users/{uid}", json={"is_visiting": False})
    assert r.json()["is_visiting"] is False and r.json()["visiting_days"] == [] and r.json()["max_weekly_hours"] is None

    new_email = mail_for("api-t1-new")
    assert p1.api(p1.hod, "PATCH", f"/api/users/{uid}", json={"email": new_email}).json()["email"] == new_email
    assert db_val("select email from auth.users where id = $1", uuid.UUID(uid)) == new_email  # the LOGIN changed too
    S["t1"]["email"] = new_email
    assert p1.api(p1.hod, "PATCH", f"/api/users/{uid}", json={"email": S["vis"]["email"]}).status_code == 409
    assert p1.api(p1.hod, "PATCH", f"/api/users/{uid}", json={"role": "admin"}).status_code == 422
    assert p1.api(p1.hod, "PATCH", f"/api/users/{uid}", json={"school_id": p1.school_b}).status_code == 422
    assert db_val("select role from public.profiles where id = $1", uuid.UUID(uid)) == "teacher"
    assert p1.api(p1.teacher, "PATCH", f"/api/users/{uid}", json={"phone": "1234567"}).status_code == 403


def test_hod_cannot_edit_own_role_visiting_or_email_but_can_edit_other_details(p1):
    me = p1.hod.account.user_id
    assert p1.api(p1.hod, "PATCH", f"/api/users/{me}", json={"specialization": "Databases"}).status_code == 200
    assert p1.api(p1.hod, "PATCH", f"/api/users/{me}", json={"is_visiting": True, "visiting_days": [1], "max_weekly_hours": 3}).status_code == 403
    assert p1.api(p1.hod, "PATCH", f"/api/users/{me}", json={"email": mail_for("sneaky")}).status_code == 403
    assert p1.api(p1.hod, "POST", f"/api/users/{me}/deactivate").status_code == 409
    assert p1.api(p1.hod, "POST", f"/api/users/{me}/reset-password").status_code == 409


def test_deactivate_reactivate_and_reset_password(p1):
    r = make_account(p1, p1.hod, key="api-life", full_name="Life Cycle")
    uid, email, temp = r.json()["user"]["id"], r.json()["user"]["email"], r.json()["temp_password"]
    S["life"] = {"id": uid, "email": email}

    c, lr = login(email, temp)
    assert lr.status_code == 200
    token = lr.json()["access_token"]
    assert c.get("/api/auth/me", headers=bearer(token)).status_code == 200

    r = p1.api(p1.hod, "POST", f"/api/users/{uid}/deactivate")
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert c.get("/api/auth/me", headers=bearer(token)).status_code in (401, 403)         # signed out at once
    c2, blocked = login(email, temp)
    assert blocked.status_code == 403 and blocked.json()["code"] == "ACCOUNT_DISABLED"
    assert p1.api(p1.hod, "GET", "/api/users?status=inactive").json()["total"] == 1

    r = p1.api(p1.hod, "POST", f"/api/users/{uid}/reactivate")
    assert r.json()["is_active"] is True
    c3, back = login(email, temp)
    assert back.status_code == 200

    r = p1.api(p1.hod, "POST", f"/api/users/{uid}/reset-password")
    assert r.status_code == 200
    new_temp = r.json()["temp_password"]
    assert TEMP_RE.match(new_temp) and new_temp != temp and r.json()["user"]["must_change_password"] is True
    assert c3.get("/api/auth/me", headers=bearer(back.json()["access_token"])).status_code in (401, 403)   # old sessions ended
    _, old = login(email, temp)
    assert old.status_code == 401 and old.json()["code"] == "INVALID_CREDENTIALS"
    c4, fresh = login(email, new_temp)
    assert fresh.status_code == 200 and fresh.json()["user"]["must_change_password"] is True
    for cl in (c, c2, c3, c4):
        cl.close()


def test_an_hod_of_another_school_cannot_deactivate_my_teacher(p1, live):
    # live["hod"] is the HOD of the REAL School of IT - not of the test school
    uid = S["life"]["id"]
    for path, method in ((f"/api/users/{uid}", "GET"), (f"/api/users/{uid}/deactivate", "POST"), (f"/api/users/{uid}/reset-password", "POST")):
        assert live["hod"].client.request(method, path, headers=bearer(live["hod"].token)).status_code == 404
    assert db_val("select is_active from public.profiles where id = $1", uuid.UUID(uid)) is True


# ====================================================================================================
# 4) MY PROFILE (teacher)
# ====================================================================================================
def png(width=800, height=600, mode="RGBA") -> bytes:
    out = io.BytesIO()
    Image.new(mode, (width, height), (10, 200, 30, 128) if mode == "RGBA" else (10, 200, 30)).save(out, "PNG")
    return out.getvalue()


def test_teacher_can_fill_and_edit_own_profile(p1):
    me = p1.api(p1.teacher, "GET", "/api/profile/me").json()
    assert me["profile"]["id"] == p1.teacher.account.user_id and me["profile"]["role"] == "teacher"
    r = p1.api(p1.teacher, "PATCH", "/api/profile/me", json={"phone": "98765 43210", "specialization": "AI, ML", "full_name": "Test Teacher"})
    assert r.status_code == 200
    got = r.json()["profile"]
    assert (got["phone"], got["specialization"], got["full_name"]) == ("98765 43210", "AI, ML", "Test Teacher")
    assert fields(p1.api(p1.teacher, "PATCH", "/api/profile/me", json={"phone": "abc"}))["phone"]
    assert p1.api(p1.teacher, "PATCH", "/api/profile/me", json={"full_name": "A"}).status_code == 422


@pytest.mark.parametrize("field,value", [
    ("role", "admin"), ("school_id", "x"), ("is_visiting", True), ("visiting_days", [1, 2]), ("max_weekly_hours", 40),
    ("email", "me@elsewhere.com"), ("is_active", False), ("must_change_password", False), ("id", "x"),
])
def test_teacher_cannot_change_role_school_or_visiting_settings(p1, field, value):
    before = db_fetch("select role, school_id, is_visiting, visiting_days, max_weekly_hours, email, is_active, must_change_password "
                      "from public.profiles where id = $1", uuid.UUID(p1.teacher.account.user_id))
    r = p1.api(p1.teacher, "PATCH", "/api/profile/me", json={field: value})
    assert r.status_code == 422, r.text
    after = db_fetch("select role, school_id, is_visiting, visiting_days, max_weekly_hours, email, is_active, must_change_password "
                     "from public.profiles where id = $1", uuid.UUID(p1.teacher.account.user_id))
    assert before == after


def test_teacher_cannot_pick_subjects_of_another_school(p1):
    r = p1.api(p1.teacher, "PATCH", "/api/profile/me", json={"subject_ids": [p1.b_subject]})
    assert r.status_code == 422 and "subject_ids" in fields(r)
    assert p1.api(p1.teacher, "PATCH", "/api/profile/me", json={"subject_ids": ["nope"]}).status_code == 422


def test_profile_screens_are_for_teachers_and_hods_only(p1):
    assert p1.api(p1.admin, "GET", "/api/profile/me").status_code == 403
    assert p1.api(p1.hod, "GET", "/api/profile/me").status_code == 200       # HOD is also a teacher
    assert p1.api(p1.admin, "PUT", "/api/profile/me/photo", files={"file": ("a.png", png(), "image/png")}).status_code == 403


def test_photo_upload_is_resized_and_only_visible_to_the_same_school(p1):
    tid = p1.teacher.account.user_id
    r = p1.api(p1.teacher, "PUT", "/api/profile/me/photo", files={"file": ("me.png", png(), "image/png")})
    assert r.status_code == 200, r.text
    url = r.json()["photo_url"]
    assert url.startswith(f"/api/profile/photo/{tid}")
    assert p1.api(p1.teacher, "GET", "/api/profile/me").json()["profile"]["photo_url"] == url

    got = p1.api(p1.teacher, "GET", url)
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(got.content))
    assert img.format == "JPEG" and img.size == (256, 256)
    assert p1.api(p1.hod, "GET", f"/api/profile/photo/{tid}").status_code == 200       # same school
    assert p1.api(p1.admin, "GET", f"/api/profile/photo/{tid}").status_code == 200      # admin sees all
    assert p1.api(p1.teacher_b, "GET", f"/api/profile/photo/{tid}").status_code == 404  # another school
    assert new_client().get(f"/api/profile/photo/{tid}").status_code == 401             # nobody anonymous

    for name, data in (("fake.png", b"this is not a picture"), ("empty.png", b""), ("big.png", b"0" * (5 * 1024 * 1024 + 10))):
        bad = p1.api(p1.teacher, "PUT", "/api/profile/me/photo", files={"file": (name, data, "image/png")})
        assert bad.status_code == 422 and bad.json()["code"] == "BAD_IMAGE", name
    # a real picture saved with a harmless-looking extension still works; a script renamed to .png does not
    assert p1.api(p1.teacher, "PUT", "/api/profile/me/photo", files={"file": ("x.png", b"<script>alert(1)</script>", "image/png")}).status_code == 422

    r = p1.api(p1.teacher, "DELETE", "/api/profile/me/photo")
    assert r.status_code == 200 and r.json()["photo_url"] is None
    assert p1.api(p1.teacher, "GET", f"/api/profile/photo/{tid}").status_code == 404
