"""Phase 1 API tests, part C: Excel import, audit log, "every route is protected", HOD school-locking everywhere,
direct database access (RLS) for all new tables, and optional fields sent as null by the browser.
"""
from __future__ import annotations

import io
import json
import uuid

import httpx
import pytest
from openpyxl import Workbook

from app.config import get_settings

from .conftest import RUN_ID, bearer, db_exec, db_fetch, db_val, mail_for, new_client

S: dict = {}
ST = get_settings()
REST = f"{ST.supabase_url}/rest/v1"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def fields(resp) -> dict:
    return resp.json().get("fields", {})


def xlsx(rows: list[list], headers: list[str]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


TEACHER_HEADERS = ["Full Name *", "Email *", "Phone", "Specialization", "Visiting Faculty (Yes/No)", "Visiting Days (e.g. Mon, Wed)", "Max Weekly Hours", "Subject Codes They Can Teach (comma separated)"]
SUBJECT_HEADERS = ["Course *", "Semester *", "Subject Name *", "Subject Code *", "Credits *", "Semester Hours *", "Lab (Yes/No)", "Elective (Yes/No)", "Elective Group", "Special (PE / SOL)"]


def upload(p1, who, kind: str, data: bytes, *, dry_run: bool, name: str = "sheet.xlsx", school: str | None = None):
    qs = f"?dry_run={'true' if dry_run else 'false'}" + (f"&school_id={school}" if school else "")
    return p1.api(who, "POST", f"/api/imports/{kind}{qs}", files={"file": (name, data, XLSX)})


@pytest.fixture(scope="module", autouse=True)
def active_odd_term(p1):
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026}).status_code == 200


# ====================================================================================================
# 1) EXCEL IMPORT
# ====================================================================================================
@pytest.mark.parametrize("kind", ["teachers", "subjects"])
def test_sample_templates_download_and_read_back(p1, kind):
    r = p1.api(p1.hod, "GET", f"/api/imports/templates/{kind}")
    assert r.status_code == 200 and r.content[:2] == b"PK" and "attachment" in r.headers["content-disposition"]
    assert XLSX in r.headers["content-type"]
    assert p1.api(p1.teacher, "GET", f"/api/imports/templates/{kind}").status_code == 403
    assert new_client().get(f"/api/imports/templates/{kind}").status_code == 401
    assert p1.api(p1.hod, "GET", "/api/imports/templates/rooms").status_code == 422


def test_subject_import_checks_every_row_then_imports_only_the_good_ones(p1):
    rows = [
        ["ZZ Course", 1, "Imp Math", "IMP-M1", 4, 60, "No", "No", "", ""],
        ["ZZ Course", 3, "Imp Lab", "IMP-L3", 1, 15, "Yes", "No", "", ""],                       # a 1-credit lab: fine
        ["ZZ Course", 5, "Imp AI", "IMP-AI5", 3, 45, "No", "Yes", "Imp Track", ""],
        ["ZZ Course", 5, "Imp Cyber", "IMP-CY5", 4, 60, "No", "Yes", "Imp Track", ""],           # credits differ -> a warning
        ["ZZ Course", 1, "Imp PE", "IMP-PE1", 1, 30, "No", "No", "", "PE"],
        ["ZZ Course", 2, "Wrong semester", "IMP-X1", 3, 45, "No", "No", "", ""],                 # even semester: does not run
        ["ZZ Course", 1, "Twice", "imp-m1", 3, 45, "No", "No", "", ""],                          # same code twice (any case)
        ["Nope", 1, "No course", "IMP-X2", 3, 45, "No", "No", "", ""],
        ["ZZ Course", 1, "Bad numbers", "IMP-X3", 0, 999, "No", "No", "", ""],
        ["ZZ Course", 1, "Bad flags", "IMP-X4", 3, 45, "maybe", "No", "", ""],
        ["ZZ Course", 1, "PE and lab", "IMP-X5", 3, 45, "Yes", "No", "", "PE"],
        ["ZZ Course", 1, "Group but no elective", "IMP-X6", 3, 45, "No", "No", "Some group", ""],
        ["ZZ Course", 9, "Semester 9", "IMP-X7", 3, 45, "No", "No", "", ""],
        ["ZZ Course", 1, "", "IMP-X8", 3, 45, "No", "No", "", ""],
    ]
    data = xlsx(rows, SUBJECT_HEADERS)
    before = db_val("select count(*) from public.subjects where school_id = $1", uuid.UUID(p1.school))

    dry = upload(p1, p1.hod, "subjects", data, dry_run=True)
    assert dry.status_code == 200, dry.text
    body = dry.json()
    assert (body["total"], body["valid"], body["invalid"], body["dry_run"]) == (14, 5, 9, True)
    by_row = {r["row"]: r for r in body["rows"]}
    assert [by_row[n]["status"] for n in range(2, 7)] == ["ok"] * 5
    assert "does not run in the active term" in by_row[7]["errors"][0]
    assert "twice" in by_row[8]["errors"][0]
    assert "not found" in by_row[9]["errors"][0]
    assert any("Credits" in e for e in by_row[10]["errors"]) and any("Semester Hours" in e for e in by_row[10]["errors"])
    assert any("Lab must be Yes or No" in e for e in by_row[11]["errors"])
    assert "cannot also be a lab" in " ".join(by_row[12]["errors"])
    assert "only be used for electives" in " ".join(by_row[13]["errors"])
    assert "Semester must be a number from 1 to 6" in " ".join(by_row[14]["errors"])
    assert any("Subject Name" in e for e in by_row[15]["errors"])
    assert db_val("select count(*) from public.subjects where school_id = $1", uuid.UUID(p1.school)) == before      # a check saves NOTHING

    real = upload(p1, p1.hod, "subjects", data, dry_run=False)
    assert real.status_code == 200
    out = real.json()
    assert (out["imported"], out["invalid"], out["dry_run"]) == (5, 9, False)
    assert any("different credits" in w for w in out["warnings"])
    assert db_val("select count(*) from public.subjects where school_id = $1", uuid.UUID(p1.school)) == before + 5
    got = db_fetch("select code, is_lab, special_kind, credits from public.subjects where code like 'IMP-%' order by code")
    assert [(g["code"], g["is_lab"], g["special_kind"], g["credits"]) for g in got] == [
        ("IMP-AI5", False, None, 3), ("IMP-CY5", False, None, 4), ("IMP-L3", True, None, 1), ("IMP-M1", False, None, 4), ("IMP-PE1", False, "PE", 1)]
    assert db_val("select count(*) from public.elective_groups where name = 'Imp Track' and school_id = $1", uuid.UUID(p1.school)) == 1   # group made once
    assert db_val("select count(*) from public.subjects where elective_group_id is not null and code like 'IMP-%'") == 2
    # importing the same sheet again: every code now exists
    again = upload(p1, p1.hod, "subjects", data, dry_run=False).json()
    assert again["imported"] == 0 and again["valid"] == 0


def test_teacher_import_creates_only_valid_rows_and_shows_each_password_once(p1):
    for code, n in (("IMP-M1", 1), ("IMP-AI5", 5), ("IMP-CY5", 5)):      # the subjects the sheet refers to (made here so this test stands alone)
        db_exec("insert into public.subjects (school_id, semester_id, name, code, credits, semester_hours) values ($1,$2,$3,$4,3,45) on conflict do nothing",
                uuid.UUID(p1.school), uuid.UUID(p1.sems[n]), f"Seed {code}", code)
    rows = [
        ["Imp Asha", mail_for("imp-asha"), "98765 43210", "AI, ML", "No", "", "", "IMP-AI5, imp-m1"],
        ["Imp Ravi", mail_for("imp-ravi"), "", "Cyber", "Yes", "Tue, Thu", 6, "IMP-CY5"],
        ["Imp Meera", mail_for("imp-meera"), "", "", "No", "", "", ""],
        ["Bad Email", "not-an-email", "", "", "No", "", "", ""],
        ["Twice", mail_for("imp-asha"), "", "", "No", "", "", ""],
        ["Visiting no days", mail_for("imp-vnd"), "", "", "Yes", "", "", ""],
        ["Visiting bad day", mail_for("imp-vbd"), "", "", "Yes", "Funday", 5, ""],
        ["Visiting Saturday", mail_for("imp-vsat"), "", "", "Yes", "Sat", 5, ""],                # 5-day week: not a working day
        ["Unknown subject", mail_for("imp-us"), "", "", "No", "", "", "NOPE-1"],
        ["Bad phone", mail_for("imp-bp"), "abc", "", "No", "", "", ""],
        ["Bad yes no", mail_for("imp-byn"), "", "", "perhaps", "", "", ""],
        ["", mail_for("imp-noname"), "", "", "No", "", "", ""],
        ["Existing", S.get("existing_email", p1.teacher.account.email), "", "", "No", "", "", ""],
    ]
    data = xlsx(rows, TEACHER_HEADERS)
    before_profiles = db_val("select count(*) from public.profiles where school_id = $1", uuid.UUID(p1.school))
    dry = upload(p1, p1.hod, "teachers", data, dry_run=True).json()
    assert (dry["total"], dry["valid"], dry["invalid"]) == (13, 3, 10)
    errs = {r["row"]: " ".join(r["errors"]) for r in dry["rows"]}
    assert "valid email" in errs[5] and "twice" in errs[6] and "at least one allowed day" in errs[7] and "funday" in errs[8].lower()
    assert "not a working day" in errs[9] and "NOPE-1" in errs[10] and "Phone" in errs[11] and "Yes or No" in errs[12]
    assert "Full Name" in errs[13] and "already exists" in errs[14]
    assert dry["credentials"] == [] and db_val("select count(*) from public.profiles where school_id = $1", uuid.UUID(p1.school)) == before_profiles

    real = upload(p1, p1.hod, "teachers", data, dry_run=False).json()
    assert (real["imported"], real["invalid"]) == (3, 10)
    creds = real["credentials"]
    assert {c["email"] for c in creds} == {mail_for("imp-asha"), mail_for("imp-ravi"), mail_for("imp-meera")}
    assert all(len(c["temp_password"]) == 14 and c["temp_password"].count("-") == 2 for c in creds)
    S["import_creds"] = {c["email"]: c["temp_password"] for c in creds}
    rows_db = {r["email"]: r for r in db_fetch(
        "select email, school_id, role, must_change_password, is_visiting, visiting_days, max_weekly_hours, specialization, "
        "(select count(*) from public.teacher_subjects ts where ts.teacher_id = p.id) as subjects from public.profiles p where email like $1",
        f"t-imp-%-{RUN_ID}@selftest.invalid")}
    assert set(rows_db) == set(S["import_creds"])
    assert all(str(r["school_id"]) == p1.school and r["role"] == "teacher" and r["must_change_password"] for r in rows_db.values())
    ravi = rows_db[mail_for("imp-ravi")]
    assert (ravi["is_visiting"], ravi["visiting_days"], ravi["max_weekly_hours"], ravi["subjects"]) == (True, [2, 4], 6, 1)
    assert rows_db[mail_for("imp-asha")]["subjects"] == 2
    # one of the imported temporary passwords really signs in
    c = new_client()
    r = c.post("/api/auth/login", json={"email": mail_for("imp-meera"), "password": S["import_creds"][mail_for("imp-meera")]})
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is True
    c.close()
    # the passwords are never stored: not in the audit log, not readable again
    assert p1.api(p1.hod, "GET", "/api/users?q=imp-").status_code == 200
    for pw in S["import_creds"].values():
        assert db_val("select count(*) from public.audit_log where summary like $1 or details::text like $1", f"%{pw}%") == 0


def test_import_input_is_checked_before_anything_is_read(p1):
    ok = xlsx([["Imp File Test", mail_for("imp-ft"), "", "", "No", "", "", ""]], TEACHER_HEADERS)
    assert upload(p1, p1.hod, "teachers", ok, dry_run=True, name="list.csv").status_code == 422           # only .xlsx
    assert upload(p1, p1.hod, "teachers", b"this is plain text", dry_run=True).status_code == 422
    assert upload(p1, p1.hod, "teachers", b"", dry_run=True).status_code == 422
    assert upload(p1, p1.hod, "teachers", b"PK\x03\x04" + b"0" * 5000, dry_run=True).status_code == 422     # looks like a zip, is not an Excel file
    assert upload(p1, p1.hod, "teachers", b"0" * (2 * 1024 * 1024 + 10), dry_run=True).status_code == 422   # too big
    missing = xlsx([["x"]], ["Something else"])
    r = upload(p1, p1.hod, "teachers", missing, dry_run=True)
    assert r.status_code == 422 and r.json()["code"] == "BAD_FILE" and "missing" in r.json()["message"]
    empty = xlsx([], TEACHER_HEADERS)
    assert upload(p1, p1.hod, "teachers", empty, dry_run=True).status_code == 422
    many = xlsx([[f"Person {i}", f"t-many{i}-{RUN_ID}@selftest.invalid", "", "", "No", "", "", ""] for i in range(101)], TEACHER_HEADERS)
    r = upload(p1, p1.hod, "teachers", many, dry_run=True)
    assert r.status_code == 422 and "at most 100" in r.json()["message"]
    # a cell that looks like a formula is just text
    sneaky = xlsx([["=HYPERLINK(\"http://evil\")", mail_for("imp-formula"), "", "", "No", "", "", ""]], TEACHER_HEADERS)
    body = upload(p1, p1.hod, "teachers", sneaky, dry_run=True).json()
    assert body["valid"] in (0, 1)           # accepted as a (strange) name or refused - never executed
    assert db_val("select count(*) from public.profiles where email = $1", mail_for("imp-ft")) == 0


def test_imports_are_locked_to_roles_and_schools(p1):
    ok = xlsx([["Imp Lock", mail_for("imp-lock"), "", "", "No", "", "", ""]], TEACHER_HEADERS)
    assert upload(p1, p1.teacher, "teachers", ok, dry_run=True).status_code == 403
    assert upload(p1, p1.teacher, "subjects", ok, dry_run=True).status_code == 403
    assert upload(p1, p1.hod, "teachers", ok, dry_run=True, school=p1.school_b).status_code == 403          # HOD cannot import into another school
    assert upload(p1, p1.hod, "subjects", ok, dry_run=False, school=p1.school_b).status_code == 403
    assert upload(p1, p1.admin, "teachers", ok, dry_run=True).status_code == 422                            # admin must name the school
    r = upload(p1, p1.admin, "teachers", ok, dry_run=True, school=p1.school)
    assert r.status_code == 200 and r.json()["valid"] == 1
    assert upload(p1, p1.admin, "teachers", ok, dry_run=True, school=str(uuid.uuid4())).status_code == 404
    assert db_val("select count(*) from public.profiles where email = $1", mail_for("imp-lock")) == 0


# ====================================================================================================
# 2) AUDIT LOG - every important change is recorded (who, what, when, via UI)
# ====================================================================================================
def audit_rows(actor_ids: list[str]):
    return db_fetch("select id, actor_id, actor_email, actor_role, school_id, action, entity, entity_id, summary, details, via, at "
                    "from public.audit_log where actor_id = any($1::uuid[]) order by id", [uuid.UUID(a) for a in actor_ids])


def test_every_kind_of_change_is_audited(p1):
    people = [p1.admin.account.user_id, p1.hod.account.user_id, p1.teacher.account.user_id]
    start = db_val("select coalesce(max(id), 0) from public.audit_log")

    def new_rows():
        return [r for r in audit_rows(people) if r["id"] > start]

    def expect(entity: str, action: str, contains: str | None = None):
        hit = [r for r in new_rows() if r["entity"] == entity and r["action"] == action and (contains is None or contains.lower() in r["summary"].lower())]
        assert hit, (entity, action, contains, [(r["entity"], r["action"], r["summary"]) for r in new_rows()])
        return hit[-1]

    A, H, T = p1.admin, p1.hod, p1.teacher
    sem1, sem3 = p1.sems[1], p1.sems[3]
    # --- term
    p1.api(A, "PUT", "/api/terms/active", json={"kind": "even", "year": 2027})
    expect("term", "activate", "Even 2027")
    p1.api(H, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026})
    expect("term", "activate", "Odd 2026")
    term = p1.api(H, "GET", "/api/terms").json()["active"]["id"]
    p1.api(H, "PUT", f"/api/terms/{term}/dates", json={"items": [{"semester_id": sem1, "start_date": "2026-07-01", "end_date": "2026-11-30"}]})
    expect("semester_dates", "update")
    # --- schools / courses
    school = p1.api(A, "POST", "/api/structure/schools", json={"name": f"ZZ Test School Audit{RUN_ID}"}).json()["id"]
    expect("school", "create", "Audit")
    p1.api(A, "PATCH", f"/api/structure/schools/{school}", json={"is_active": False})
    expect("school", "update", "deactivated")
    course = p1.api(A, "POST", "/api/structure/courses", json={"school_id": school, "name": "Audit Course", "num_semesters": 2}).json()["id"]
    expect("course", "create", "Audit Course")
    p1.api(A, "PATCH", f"/api/structure/courses/{course}", json={"name": "Audit Course 2"})
    expect("course", "update", "Audit Course 2")
    p1.api(A, "DELETE", f"/api/structure/courses/{course}")
    expect("course", "delete", "Audit Course 2")
    p1.api(A, "DELETE", f"/api/structure/schools/{school}")
    expect("school", "delete")
    # --- accounts
    made = p1.api(H, "POST", "/api/users", json={"full_name": "Audit Person", "email": mail_for("audit-p")}).json()
    row = expect("teacher", "create", "Audit Person")
    assert row["actor_email"] == H.account.email and row["actor_role"] == "hod" and str(row["school_id"]) == p1.school and row["via"] == "ui"
    assert row["entity_id"] == made["user"]["id"] and row["at"] is not None
    uid = made["user"]["id"]
    p1.api(H, "PATCH", f"/api/users/{uid}", json={"phone": "98765 43210"})
    expect("teacher", "update", "phone")
    p1.api(H, "POST", f"/api/users/{uid}/deactivate")
    expect("teacher", "deactivate")
    p1.api(H, "POST", f"/api/users/{uid}/reactivate")
    expect("teacher", "reactivate")
    reset = p1.api(H, "POST", f"/api/users/{uid}/reset-password").json()
    expect("teacher", "reset_password")
    # --- profile
    p1.api(T, "PATCH", "/api/profile/me", json={"specialization": "Audit trail"})
    prof = expect("profile", "update", "own profile")
    assert prof["actor_role"] == "teacher" and prof["actor_email"] == T.account.email
    # --- subjects / groups
    sub = p1.api(H, "POST", "/api/subjects", json={"semester_id": sem1, "name": "Audit Subject", "code": "AUD-1", "credits": 3, "semester_hours": 45}).json()["id"]
    expect("subject", "create", "AUD-1")
    p1.api(H, "PATCH", f"/api/subjects/{sub}", json={"credits": 4})
    expect("subject", "update", "AUD-1")
    grp = p1.api(H, "POST", "/api/subjects/groups", json={"semester_id": sem3, "name": "Audit Group"}).json()["id"]
    expect("elective_group", "create")
    p1.api(H, "PATCH", f"/api/subjects/groups/{grp}", json={"name": "Audit Group 2"})
    expect("elective_group", "update")
    p1.api(H, "DELETE", f"/api/subjects/groups/{grp}")
    expect("elective_group", "delete")
    # --- assignment (the new teacher must be allowed to teach it first)
    p1.api(H, "PATCH", f"/api/users/{uid}", json={"subject_ids": [sub]})
    asg = p1.api(H, "PUT", "/api/assignments", json={"subject_id": sub, "teacher_id": uid}).json()["id"]
    expect("assignment", "create", "Audit Person")
    p1.api(H, "PUT", "/api/assignments", json={"subject_id": sub, "teacher_id": uid, "co_teacher_id": None})
    expect("assignment", "update")
    p1.api(H, "DELETE", f"/api/assignments/{asg}")
    expect("assignment", "delete")
    # --- timing
    p1.api(A, "PUT", "/api/timing/college", json={"college_start": "10:00", "college_end": "16:50", "lecture_minutes": 60, "break_minutes": 5, "days_per_week": 5})
    expect("college_timing", "update")
    p1.api(H, "PUT", "/api/timing/school", json={"lunch_start": "13:10", "lunch_end": "13:40"})
    expect("school_timing", "update", "13:10")
    p1.api(H, "PUT", f"/api/timing/semesters/{sem1}/days", json={"days": [{"day": 5, "is_off": True}]})
    expect("semester_days", "update", "Fri OFF")
    p1.api(H, "PUT", f"/api/timing/semesters/{sem1}/days", json={"days": [{"day": 5}]})
    # --- fixed slots
    slot = p1.api(H, "POST", "/api/fixed-slots", json={"semester_id": sem3, "day": 2, "start_period": 3, "kind": "SOL"}).json()["id"]
    expect("fixed_slot", "create")
    p1.api(H, "PATCH", f"/api/fixed-slots/{slot}", json={"day": 3})
    expect("fixed_slot", "update")
    p1.api(H, "DELETE", f"/api/fixed-slots/{slot}")
    expect("fixed_slot", "delete")
    # --- holidays / events
    hol = p1.api(A, "POST", "/api/calendar", json={"scope": "college", "kind": "holiday", "title": "ZZ Audit Day", "date_from": "2026-12-12"}).json()["id"]
    expect("holiday", "create", "ZZ Audit Day")
    p1.api(A, "PATCH", f"/api/calendar/{hol}", json={"title": "ZZ Audit Day 2"})
    expect("holiday", "update", "ZZ Audit Day 2")
    p1.api(A, "DELETE", f"/api/calendar/{hol}")
    expect("holiday", "delete")
    ev = p1.api(H, "POST", "/api/calendar", json={"kind": "event", "title": "ZZ Audit Event", "date_from": "2026-12-13"}).json()["id"]
    expect("event", "create")
    p1.api(H, "DELETE", f"/api/calendar/{ev}")
    expect("event", "delete")
    p1.api(A, "POST", "/api/calendar/restore-india-list")
    expect("holiday", "create", "Restored the standard India holiday list")
    # --- photo
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), (200, 10, 10)).save(buf, "PNG")
    p1.api(T, "PUT", "/api/profile/me/photo", files={"file": ("a.png", buf.getvalue(), "image/png")})
    expect("profile", "update", "photo")
    p1.api(T, "DELETE", "/api/profile/me/photo")

    # --- the audit rows never contain a password, a token or an e-mail's secret
    blob = json.dumps([[r["summary"], r["details"]] for r in new_rows()], default=str)
    assert reset["temp_password"] not in blob and "access_token" not in blob and "Bearer" not in blob
    # --- nobody but the admin can read the audit log (even through the public data API)
    S["audit_count"] = len(new_rows())
    assert S["audit_count"] >= 35


def test_refused_requests_are_not_audited(p1):
    people = [p1.admin.account.user_id, p1.hod.account.user_id, p1.teacher.account.user_id]
    before = len(audit_rows(people))
    assert p1.api(p1.teacher, "POST", "/api/users", json={"full_name": "No Way", "email": mail_for("noway")}).status_code == 403
    assert p1.api(p1.hod, "POST", "/api/structure/courses", json={"school_id": p1.school, "name": "Nope", "num_semesters": 2}).status_code == 403
    assert p1.api(p1.hod, "POST", "/api/subjects", json={"semester_id": p1.sems[1], "name": "", "code": "", "credits": 0, "semester_hours": 0}).status_code == 422
    assert p1.api(p1.hod, "POST", "/api/fixed-slots", json={"semester_id": p1.sems[1], "day": 6, "start_period": 1, "kind": "PE"}).status_code == 422
    assert p1.api(p1.hod, "PUT", "/api/terms/active", json={"kind": "bad", "year": 2026}).status_code == 422
    assert len(audit_rows(people)) == before


# ====================================================================================================
# 3) EVERY ROUTE IS PROTECTED (found automatically - also covers routes added later)
# ====================================================================================================
PUBLIC = {("POST", "/api/auth/login"), ("POST", "/api/auth/refresh"), ("POST", "/api/auth/logout"), ("GET", "/api/health")}


def _walk(routes):
    """This FastAPI version keeps each included router as a lazy object - look inside it."""
    from fastapi.routing import APIRoute

    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _walk(inner.routes)
        elif isinstance(r, APIRoute):
            yield r


def all_routes():
    from app.main import app

    out = []
    for r in _walk(app.routes):
        if not r.path.startswith("/api"):
            continue
        roles = None
        for dep in r.dependant.dependencies:
            roles = getattr(dep.call, "allowed_roles", None) or roles
        for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
            out.append((method, r.path, roles))
    return out


def concrete(path: str) -> str:
    import re

    return re.sub(r"\{[^}]+\}", str(uuid.UUID(int=0)), path).replace("{kind}", "teachers")


def test_every_route_exists_in_the_list_and_is_not_accidentally_public():
    routes = all_routes()
    assert len(routes) >= 60
    open_routes = [(m, p) for m, p, roles in routes if roles is None and (m, p) not in PUBLIC and not p.startswith("/api/auth/")]
    assert open_routes == [], f"routes without any role guard: {open_routes}"


def test_without_a_login_every_route_says_401(live):
    problems = []
    with new_client() as c:
        for method, path, roles in all_routes():
            if (method, path) in PUBLIC:
                continue
            url = concrete(path) + ("?dry_run=true" if "imports" in path and method == "POST" else "")
            r = c.request(method, url, json={} if method in ("POST", "PUT", "PATCH") else None)
            if r.status_code != 401:
                problems.append((method, path, r.status_code))
    assert problems == [], problems


def test_every_role_is_refused_on_the_routes_not_meant_for_it(live, p1):
    people = {"admin": p1.admin, "hod": p1.hod, "teacher": p1.teacher}
    problems = []
    checked = 0
    for method, path, roles in all_routes():
        if roles is None:
            continue
        for role, who in people.items():
            if role in roles:
                continue
            url = concrete(path)
            r = who.client.request(method, url, headers=bearer(who.token), json={} if method in ("POST", "PUT", "PATCH") else None)
            checked += 1
            if r.status_code != 403:
                problems.append((role, method, path, r.status_code))
    assert checked >= 40
    assert problems == [], problems


# ====================================================================================================
# 4) HOD IS LOCKED TO HIS OWN SCHOOL - one table over every endpoint family
# ====================================================================================================
def test_hod_school_locking_matrix(p1):
    term = p1.api(p1.hod, "GET", "/api/terms").json()["active"]["id"]
    B = p1.school_b
    gets = ["/api/structure/tree", "/api/users", "/api/subjects", "/api/subjects/groups", "/api/assignments", "/api/timing", "/api/fixed-slots",
            "/api/calendar", f"/api/terms/{term}/dates"]
    for path in gets:
        r = p1.api(p1.hod, "GET", f"{path}?school_id={B}")
        assert r.status_code == 403, (path, r.status_code)
        mine = p1.api(p1.hod, "GET", f"{path}?school_id={p1.school}")
        assert mine.status_code == 200, (path, mine.status_code)
    writes = [
        ("PUT", "/api/timing/school", {"school_id": B, "lunch_start": "13:00", "lunch_end": "13:30"}),
        ("POST", "/api/users", {"full_name": "Lock Test", "email": mail_for("lock1"), "school_id": B}),
        ("POST", "/api/calendar", {"title": "ZZ Lock", "date_from": "2026-12-01", "school_id": B}),
        ("PUT", f"/api/terms/{term}/dates", {"school_id": B, "items": [{"semester_id": p1.b_sems[1], "start_date": "2026-07-01", "end_date": "2026-11-30"}]}),
    ]
    for method, path, body in writes:
        assert p1.api(p1.hod, method, path, json=body).status_code == 403, (method, path)
    # foreign ids look like they do not exist (404) - for every kind of thing
    foreign = [
        ("PATCH", f"/api/users/{p1.teacher_b.account.user_id}", {"phone": "12345678"}),
        ("POST", f"/api/users/{p1.teacher_b.account.user_id}/deactivate", None),
        ("POST", f"/api/users/{p1.teacher_b.account.user_id}/reset-password", None),
        ("PATCH", f"/api/subjects/{p1.b_subject}", {"credits": 9}),
        ("DELETE", f"/api/subjects/{p1.b_subject}", None),
        ("POST", "/api/subjects", {"semester_id": p1.b_sems[1], "name": "x", "code": "LOCK-1", "credits": 3, "semester_hours": 45}),
        ("POST", "/api/subjects/groups", {"semester_id": p1.b_sems[1], "name": "Lock"}),
        ("PUT", "/api/assignments", {"subject_id": p1.b_subject, "teacher_id": p1.teacher.account.user_id}),
        ("PUT", f"/api/timing/semesters/{p1.b_sems[1]}/days", {"days": [{"day": 1, "is_off": True}]}),
        ("POST", "/api/fixed-slots", {"semester_id": p1.b_sems[1], "day": 1, "start_period": 1, "kind": "PE"}),
        ("POST", "/api/fixed-slots/check", {"semester_id": p1.b_sems[1], "day": 1, "start_period": 1, "kind": "PE"}),
        ("POST", "/api/calendar", {"title": "ZZ Lock", "date_from": "2026-12-01", "semester_ids": [p1.b_sems[1]]}),
    ]
    for method, path, body in foreign:
        r = p1.api(p1.hod, method, path, json=body) if body is not None else p1.api(p1.hod, method, path)
        assert r.status_code in (404, 422), (method, path, r.status_code, r.text[:120])
    assert db_val("select count(*) from public.subjects where code = 'LOCK-1'") == 0
    assert db_val("select is_active from public.profiles where id = $1", uuid.UUID(p1.teacher_b.account.user_id)) is True
    assert db_val("select count(*) from public.school_timing where school_id = $1", uuid.UUID(B)) == 0
    # the HOD of the test school does not even see school B in the school list
    assert [s["id"] for s in p1.api(p1.hod, "GET", "/api/structure/schools").json()["schools"]] == [p1.school]


def test_hod_of_the_real_school_cannot_touch_the_test_school(p1, live):
    other = live["hod"]  # HOD of the REAL School of IT
    mine = [("GET", f"/api/users/{p1.teacher.account.user_id}"), ("GET", f"/api/subjects/{uuid.uuid4()}"),
            ("DELETE", f"/api/subjects/{p1.b_subject}"), ("PATCH", f"/api/users/{p1.hod.account.user_id}")]
    for method, path in mine:
        r = other.client.request(method, path, headers=bearer(other.token), json={"phone": "12345678"} if method == "PATCH" else None)
        assert r.status_code in (404, 405), (method, path, r.status_code)
    assert other.client.get(f"/api/users?school_id={p1.school}", headers=bearer(other.token)).status_code == 403


# ====================================================================================================
# 5) THE BROWSER SENDS null FOR EVERY EMPTY OPTIONAL FIELD - none of them may crash
# ====================================================================================================
def test_null_optional_fields_are_accepted_everywhere(p1):
    r = p1.api(p1.hod, "POST", "/api/users", json={"full_name": "Null Fields", "email": mail_for("nulls"), "phone": None, "specialization": None,
                                                    "max_weekly_hours": None, "visiting_days": [], "subject_ids": []})
    assert r.status_code == 201, r.text
    uid = r.json()["user"]["id"]
    assert p1.api(p1.hod, "PATCH", f"/api/users/{uid}", json={"phone": None, "specialization": None}).status_code == 200
    assert p1.api(p1.teacher, "PATCH", "/api/profile/me", json={"phone": None, "specialization": None}).status_code == 200
    r = p1.api(p1.hod, "POST", "/api/fixed-slots", json={"semester_id": p1.sems[3], "day": 4, "start_period": 1, "kind": "PE", "label": None,
                                                         "note": None, "teacher_id": None, "co_teacher_id": None, "subject_id": None, "length": None})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert p1.api(p1.hod, "PATCH", f"/api/fixed-slots/{sid}", json={"note": None, "teacher_id": None, "subject_id": None}).status_code == 200
    assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{sid}").status_code == 200
    r = p1.api(p1.hod, "POST", "/api/calendar", json={"kind": "holiday", "title": "ZZ Null Day", "date_from": "2026-12-14", "date_to": None,
                                                      "note": None, "start_time": None, "end_time": None, "semester_ids": []})
    assert r.status_code == 201, r.text
    assert p1.api(p1.admin, "POST", "/api/structure/courses", json={"school_id": p1.school, "name": "Null Course", "code": None, "num_semesters": 2}).status_code == 201
    r = p1.api(p1.hod, "PUT", "/api/assignments", json={"subject_id": str(uuid.uuid4()), "teacher_id": uid, "co_teacher_id": None})
    assert r.status_code == 404                                                       # (the subject does not exist) - but null co-teacher is fine
    assert p1.api(p1.hod, "PUT", "/api/timing/school", json={"lunch_start": None, "lunch_end": None}).status_code == 200      # no lunch
    assert p1.api(p1.hod, "PUT", "/api/timing/school", json={"lunch_start": "13:10", "lunch_end": "13:40"}).status_code == 200


# ====================================================================================================
# 6) DIRECT DATABASE ACCESS (the public data API): read-only, own school only, audit log admin-only
# ====================================================================================================
NEW_TABLES = ["courses", "semesters", "term_semesters", "school_timing", "subjects", "elective_groups", "teacher_subjects",
              "assignments", "fixed_slots", "calendar_entries", "calendar_entry_classes", "semester_days", "academic_terms", "college_settings",
              "audit_log", "profile_photos"]
SCHOOL_TABLES = ["courses", "semesters", "subjects", "elective_groups", "teacher_subjects", "assignments", "fixed_slots", "semester_days", "school_timing", "term_semesters"]


def rest(method: str, path: str, token: str | None = None, **kw) -> httpx.Response:
    headers = {"apikey": ST.supabase_anon_key}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(kw.pop("headers", {}))
    return httpx.request(method, f"{REST}{path}", headers=headers, timeout=40, **kw)


@pytest.fixture(scope="module")
def some_rows(p1):
    """A little data in BOTH schools so the isolation checks have something to find."""
    p1.api(p1.hod, "POST", "/api/subjects", json={"semester_id": p1.sems[5], "name": "RLS Subject", "code": "RLS-P1", "credits": 3, "semester_hours": 45})
    p1.api(p1.hod, "POST", "/api/fixed-slots", json={"semester_id": p1.sems[5], "day": 2, "start_period": 1, "kind": "PE"})
    p1.api(p1.hod, "PUT", f"/api/timing/semesters/{p1.sems[5]}/days", json={"days": [{"day": 1, "end_time": "14:40"}]})
    p1.api(p1.hod, "POST", "/api/calendar", json={"kind": "event", "title": "ZZ RLS Event", "date_from": "2026-12-15"})
    db_exec("insert into public.semester_days (semester_id, school_id, day, end_time) values ($1,$2,2,'14:40') on conflict do nothing", uuid.UUID(p1.b_sems[1]), uuid.UUID(p1.school_b))
    db_exec("insert into public.school_timing (school_id, lunch_start, lunch_end) values ($1,'13:00','13:30') on conflict do nothing", uuid.UUID(p1.school_b))
    db_exec("insert into public.calendar_entries (school_id, kind, title, date_from, date_to) values ($1,'event','ZZ RLS B Event','2026-12-16','2026-12-16')", uuid.UUID(p1.school_b))
    db_exec("insert into public.term_semesters (term_id, semester_id, school_id, start_date, end_date) select t.id, $1, $2, '2026-07-01', '2026-11-30' from public.academic_terms t where t.is_active on conflict do nothing",
            uuid.UUID(p1.b_sems[1]), uuid.UUID(p1.school_b))
    db_exec("insert into public.profile_photos (user_id, content_type, data) values ($1, 'image/jpeg', '\\x00') on conflict do nothing", uuid.UUID(p1.teacher.account.user_id))


@pytest.mark.parametrize("table", NEW_TABLES)
def test_anonymous_visitor_gets_nothing_from_the_new_tables(live, table):
    assert rest("GET", f"/{table}?select=*").status_code in (401, 403)


@pytest.mark.parametrize("table", SCHOOL_TABLES)
def test_each_person_reads_only_their_own_schools_rows(p1, some_rows, table):
    def schools(who) -> set[str]:
        r = rest("GET", f"/{table}?select=school_id", who.token)
        assert r.status_code == 200, (table, r.status_code, r.text[:150])
        return {row["school_id"] for row in r.json()}

    assert schools(p1.hod) <= {p1.school}
    assert schools(p1.teacher) <= {p1.school}
    assert schools(p1.teacher_b) <= {p1.school_b}
    assert p1.school_b not in schools(p1.hod) and p1.school not in schools(p1.teacher_b)
    admin_sees = schools(p1.admin)
    if table in ("courses", "semesters", "subjects"):
        assert {p1.school, p1.school_b} <= admin_sees       # the admin sees every school
    elif table in ("semester_days", "school_timing", "term_semesters"):
        assert {p1.school_b} <= admin_sees


def test_calendar_rows_college_wide_for_everybody_school_ones_only_for_that_school(p1, some_rows):
    def titles(who) -> set[str]:
        r = rest("GET", "/calendar_entries?select=title,school_id&title=like.ZZ%25", who.token)
        assert r.status_code == 200
        return {row["title"] for row in r.json()}

    assert "ZZ RLS Event" in titles(p1.hod) and "ZZ RLS B Event" not in titles(p1.hod)
    assert "ZZ RLS B Event" in titles(p1.teacher_b) and "ZZ RLS Event" not in titles(p1.teacher_b)
    assert {"ZZ RLS Event", "ZZ RLS B Event"} <= titles(p1.admin)
    holidays = rest("GET", "/calendar_entries?select=id&school_id=is.null&is_preloaded=eq.true", p1.teacher_b.token).json()
    assert len(holidays) >= 30                                   # the standard India list is visible to everybody


def test_terms_and_college_timing_are_readable_by_everybody_but_not_writable(p1):
    for who in (p1.hod, p1.teacher, p1.teacher_b, p1.admin):
        assert rest("GET", "/academic_terms?select=kind,year", who.token).status_code == 200
        assert rest("GET", "/college_settings?select=college_start", who.token).json()
    for who in (p1.hod, p1.admin):
        assert rest("PATCH", "/college_settings?id=eq.true", who.token, json={"college_end": "23:00"}).status_code in (401, 403)
        assert rest("POST", "/academic_terms", who.token, json={"kind": "odd", "year": 2040}).status_code in (401, 403)
    assert db_val("select college_end::text from public.college_settings") == "16:50:00"
    assert db_val("select count(*) from public.academic_terms where year = 2040") == 0


def test_the_audit_log_can_only_be_read_by_the_admin(p1):
    assert len(rest("GET", "/audit_log?select=id&limit=5", p1.admin.token).json()) > 0
    for who in (p1.hod, p1.teacher, p1.teacher_b):
        r = rest("GET", "/audit_log?select=id", who.token)
        assert r.status_code == 200 and r.json() == []           # RLS: no row is visible
    for who in (p1.admin, p1.hod, p1.teacher):
        for method, kw in (("POST", {"json": {"action": "x", "entity": "x", "summary": "forged"}}), ("PATCH", {"json": {"summary": "edited"}}), ("DELETE", {})):
            path = "/audit_log" + ("?id=gt.0" if method != "POST" else "")
            assert rest(method, path, who.token, **kw).status_code in (401, 403), (who.account.role, method)
    assert db_val("select count(*) from public.audit_log where summary in ('forged', 'edited')") == 0


def test_profile_photos_cannot_be_read_even_by_the_admin_through_the_data_api(p1, some_rows):
    for who in (p1.admin, p1.hod, p1.teacher, p1.teacher_b):
        assert rest("GET", "/profile_photos?select=user_id", who.token).status_code in (401, 403)
    assert db_val("select count(*) from public.profile_photos") >= 1       # the row exists - it is just unreachable


@pytest.mark.parametrize("table", [t for t in NEW_TABLES if t != "audit_log"])
def test_nobody_can_write_to_any_new_table_through_the_data_api(p1, table):
    payloads = {
        "courses": {"school_id": p1.school, "name": "Hack", "num_semesters": 1}, "subjects": {"school_id": p1.school, "name": "Hack"},
        "calendar_entries": {"kind": "holiday", "title": "Hack", "date_from": "2026-01-01", "date_to": "2026-01-01"},
        "academic_terms": {"kind": "odd", "year": 2041}, "college_settings": {"id": True},
        "profile_photos": {"user_id": p1.teacher.account.user_id, "content_type": "image/jpeg", "data": "\\x00"},
    }
    flt = {"academic_terms": "kind=eq.odd", "college_settings": "id=eq.true", "profile_photos": f"user_id=eq.{p1.teacher.account.user_id}"}.get(table, f"school_id=eq.{p1.school}")
    col = flt.split("=")[0]
    before = db_val(f"select count(*) from public.{table}")
    for who in (p1.admin, p1.hod, p1.teacher):
        assert rest("POST", f"/{table}", who.token, json=payloads.get(table, {"school_id": p1.school})).status_code in (401, 403)
        assert rest("PATCH", f"/{table}?{flt}", who.token, json={col: flt.split("=eq.")[1]}).status_code in (401, 403)
        assert rest("DELETE", f"/{table}?{flt}", who.token).status_code in (401, 403)
    assert db_val(f"select count(*) from public.{table}") == before


def test_every_table_of_phase_1_has_row_level_security_and_no_anonymous_grant():
    rows = db_fetch("""
        select c.relname, c.relrowsecurity,
               has_table_privilege('anon', c.oid, 'select,insert,update,delete') as anon_any,
               has_table_privilege('authenticated', c.oid, 'insert,update,delete') as auth_write
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind = 'r' and c.relname = any($1::text[])""", NEW_TABLES)
    assert {r["relname"] for r in rows} == set(NEW_TABLES)
    for r in rows:
        assert r["relrowsecurity"] is True, r["relname"]
        assert r["anon_any"] is False and r["auth_write"] is False, r["relname"]
