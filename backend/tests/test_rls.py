"""Row Level Security, tested through Supabase's PUBLIC data API (the door an attacker would really use)."""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.config import get_settings

from .conftest import RUN_ID, db_fetch, db_val

S = get_settings()
REST = f"{S.supabase_url}/rest/v1"
DENIED = (401, 403)


def rest(method: str, path: str, token: str | None = None, **kw) -> httpx.Response:
    headers = {"apikey": S.supabase_anon_key}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(kw.pop("headers", {}))
    return httpx.request(method, f"{REST}{path}", headers=headers, timeout=30, **kw)


def ids(resp: httpx.Response) -> set[str]:
    assert resp.status_code == 200, resp.text
    return {row["id"] for row in resp.json()}


def profile_value(user_id: str, column: str):
    return db_val(f"select {column} from public.profiles where id = $1", uuid.UUID(user_id))


# ---------------------------------------------------------------------- the anonymous visitor
@pytest.mark.parametrize("table", ["profiles", "schools", "app_sessions", "schema_migrations"])
def test_anonymous_visitor_gets_nothing(live, table):
    r = rest("GET", f"/{table}?select=*")
    assert r.status_code in DENIED, (table, r.status_code, r.text[:200])


def test_anonymous_visitor_cannot_write(live):
    assert rest("POST", "/schools", json={"name": "ZZ Test School anon"}).status_code in DENIED
    assert rest("PATCH", "/profiles?role=eq.teacher", json={"role": "admin"}).status_code in DENIED
    assert rest("DELETE", "/profiles?role=eq.teacher").status_code in DENIED


# ---------------------------------------------------------------------- who can read what
def test_teacher_sees_only_their_own_profile(live):
    r = rest("GET", "/profiles?select=id,email,role", live["teacher"].token)
    assert ids(r) == {live["teacher"].account.user_id}


def test_hod_sees_only_profiles_of_their_own_school(live):
    r = rest("GET", "/profiles?select=id,role,school_id", live["hod"].token)
    seen = ids(r)
    assert {live["hod"].account.user_id, live["teacher"].account.user_id} <= seen
    assert live["teacher_b"].account.user_id not in seen  # other school
    assert live["admin"].account.user_id not in seen      # college-wide admin is not part of a school
    assert {row["school_id"] for row in r.json()} == {live["hod"].account.school_id}


def test_admin_sees_everyone(live):
    seen = ids(rest("GET", "/profiles?select=id", live["admin"].token))
    assert {live[k].account.user_id for k in ("admin", "hod", "teacher", "teacher_b")} <= seen


def test_each_person_sees_only_their_own_school(live):
    names = lambda who: sorted(row["name"] for row in rest("GET", "/schools?select=name", live[who].token).json())  # noqa: E731
    assert names("teacher") == ["School of IT"]
    assert names("hod") == ["School of IT"]
    assert names("teacher_b") == [f"ZZ Test School {RUN_ID}"]
    admin_names = names("admin")
    assert "School of IT" in admin_names and f"ZZ Test School {RUN_ID}" in admin_names


def test_internal_tables_are_invisible_even_to_the_admin(live):
    for who in ("admin", "hod", "teacher"):
        for table in ("app_sessions", "schema_migrations"):
            r = rest("GET", f"/{table}?select=*", live[who].token)
            assert r.status_code in DENIED, (who, table, r.status_code)


def test_helper_functions_are_not_reachable_over_the_public_api(live):
    assert rest("POST", "/rpc/is_admin", live["teacher"].token, json={}).status_code in (404, 400, 403)
    r = rest("GET", "/profiles?select=id", live["teacher"].token, headers={"Accept-Profile": "private"})
    assert r.status_code in (400, 403, 404, 406)  # schema "private" is not exposed


# ---------------------------------------------------------------------- who can change what
@pytest.mark.parametrize(
    "column, value",
    [("role", "admin"), ("is_active", False), ("must_change_password", False), ("is_visiting", True),
     ("email", "someone-else@selftest.invalid")],
)
def test_teacher_cannot_change_protected_fields_of_their_own_profile(live, column, value):
    me = live["teacher"].account
    before = profile_value(me.user_id, column)
    r = rest("PATCH", f"/profiles?id=eq.{me.user_id}", live["teacher"].token, json={column: value},
             headers={"Prefer": "return=representation"})
    assert r.status_code in (400, 401, 403), (column, r.status_code, r.text[:200])
    assert profile_value(me.user_id, column) == before


def test_teacher_cannot_move_themselves_to_another_school(live):
    me = live["teacher"].account
    r = rest("PATCH", f"/profiles?id=eq.{me.user_id}", live["teacher"].token,
             json={"school_id": live["teacher_b"].account.school_id})
    assert r.status_code in (400, 401, 403)
    assert str(profile_value(me.user_id, "school_id")) == me.school_id


def test_teacher_can_edit_the_harmless_fields_of_their_own_profile(live):
    me = live["teacher"].account
    r = rest("PATCH", f"/profiles?id=eq.{me.user_id}", live["teacher"].token,
             json={"phone": "9876543210", "specialization": "AI, ML"}, headers={"Prefer": "return=representation"})
    assert r.status_code == 200 and len(r.json()) == 1, r.text
    assert profile_value(me.user_id, "phone") == "9876543210"
    assert profile_value(me.user_id, "specialization") == "AI, ML"


def test_teacher_cannot_edit_a_colleague(live):
    other = live["teacher_b"].account
    before = profile_value(other.user_id, "phone")
    r = rest("PATCH", f"/profiles?id=eq.{other.user_id}", live["teacher"].token, json={"phone": "1111111111"},
             headers={"Prefer": "return=representation"})
    assert r.status_code == 200 and r.json() == []  # row is invisible -> nothing changes
    assert profile_value(other.user_id, "phone") == before


def test_hod_can_edit_own_school_but_not_other_schools(live):
    mine, theirs = live["teacher"].account, live["teacher_b"].account
    r = rest("PATCH", f"/profiles?id=eq.{mine.user_id}", live["hod"].token, json={"phone": "2222222222"},
             headers={"Prefer": "return=representation"})
    assert r.status_code == 200 and len(r.json()) == 1
    assert profile_value(mine.user_id, "phone") == "2222222222"
    before = profile_value(theirs.user_id, "phone")
    r = rest("PATCH", f"/profiles?id=eq.{theirs.user_id}", live["hod"].token, json={"phone": "3333333333"},
             headers={"Prefer": "return=representation"})
    assert r.status_code == 200 and r.json() == []
    assert profile_value(theirs.user_id, "phone") == before


def test_hod_cannot_promote_a_teacher_to_admin(live):
    t = live["teacher"].account
    r = rest("PATCH", f"/profiles?id=eq.{t.user_id}", live["hod"].token, json={"role": "admin"})
    assert r.status_code in (400, 401, 403)
    assert profile_value(t.user_id, "role") == "teacher"


def test_nobody_can_create_or_delete_accounts_through_the_public_api(live):
    for who in ("teacher", "hod", "admin"):
        token = live[who].token
        r = rest("POST", "/profiles", token, json={"id": str(uuid.uuid4()), "full_name": "X", "email": "x@selftest.invalid", "role": "admin"})
        assert r.status_code in DENIED, who
        r = rest("DELETE", f"/profiles?id=eq.{live['teacher'].account.user_id}", token)
        assert r.status_code in DENIED, who
    assert db_val("select count(*) from public.profiles where id = $1", uuid.UUID(live["teacher"].account.user_id)) == 1


def test_only_the_admin_can_add_schools(live):
    for who in ("teacher", "hod"):
        r = rest("POST", "/schools", live[who].token, json={"name": f"ZZ Test School evil-{who}"})
        assert r.status_code in DENIED, (who, r.status_code)
    r = rest("POST", "/schools", live["admin"].token, json={"name": f"ZZ Test School made-by-admin {RUN_ID}"},
             headers={"Prefer": "return=representation"})
    assert r.status_code == 201, r.text
    assert db_val("select count(*) from public.schools where name like 'ZZ Test School evil%'") == 0
    # a teacher cannot rename a school either
    r = rest("PATCH", "/schools?name=eq.School%20of%20IT", live["teacher"].token, json={"name": "Hacked"},
             headers={"Prefer": "return=representation"})
    assert r.status_code in DENIED or r.json() == []
    assert db_val("select count(*) from public.schools where name = 'School of IT'") == 1
    # nobody (not even admin) can delete a school through the public API
    r = rest("DELETE", "/schools?name=eq.School%20of%20IT", live["admin"].token)
    assert r.status_code in DENIED
    assert db_val("select count(*) from public.schools where name = 'School of IT'") == 1


def test_every_table_in_public_has_rls_switched_on():
    rows = db_fetch(
        "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity"
    )
    assert rows == [], f"tables without RLS: {rows}"
