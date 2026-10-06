"""Self-test helpers.

The integration tests talk to the REAL Supabase project from .env, using throw-away accounts
(@selftest.invalid) that are created at the start and deleted at the end. The real admin is never touched.

NOTE: Supabase allows about 30 password sign-ins per 5 minutes per IP, so do not re-run the whole
suite many times in a row.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import ENV_FILE, get_settings  # noqa: E402
from app.db import connect_one  # noqa: E402
from app.supabase_auth import SupabaseAuth  # noqa: E402

TEST_PORT = 8001
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"
ORIGIN = "http://localhost:5173"
RUN_ID = uuid.uuid4().hex[:6]
DOMAIN = "selftest.invalid"
EMAIL_PREFIX = "t-"  # every throw-away account looks like  t-<name>-<run>@selftest.invalid
LOCKOUT_AFTER = 3     # the test server locks an email after 3 failed logins


# ---------------------------------------------------------------------- tiny sync helpers around asyncpg
def run(coro):
    return asyncio.run(coro)


def db_exec(sql: str, *args: Any) -> str:
    async def go():
        conn = await connect_one()
        try:
            return await conn.execute(sql, *args)
        finally:
            await conn.close()
    return run(go())


def db_fetch(sql: str, *args: Any) -> list[dict[str, Any]]:
    async def go():
        conn = await connect_one()
        try:
            return [dict(r) for r in await conn.fetch(sql, *args)]
        finally:
            await conn.close()
    return run(go())


def db_val(sql: str, *args: Any) -> Any:
    async def go():
        conn = await connect_one()
        try:
            return await conn.fetchval(sql, *args)
        finally:
            await conn.close()
    return run(go())


def jwt_payload(token: str) -> dict[str, Any]:
    """Reads (does NOT verify) the middle part of a JWT - only for finding the session id in tests."""
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------- accounts
@dataclass
class Account:
    key: str
    email: str
    password: str
    user_id: str
    role: str
    school_id: str | None


class Factory:
    """Creates throw-away accounts; everything is removed again at the end of the test run."""

    def __init__(self, school_a: str, school_b: str) -> None:
        self.school_a = school_a
        self.school_b = school_b
        self.created: list[Account] = []
        self._n = 0

    def create(self, role: str = "teacher", *, school: str = "a", must_change: bool = False, active: bool = True,
               key: str | None = None, school_id: str | None = None) -> Account:
        self._n += 1
        key = key or f"{role}{self._n}"
        if role == "admin":
            school_id = None
        elif school_id is None:
            school_id = self.school_a if school == "a" else self.school_b
        email = f"{EMAIL_PREFIX}{key}-{RUN_ID}@{DOMAIN}"
        password = "Tt-" + secrets.token_urlsafe(9) + "7x"

        async def go() -> Account:
            auth = SupabaseAuth(get_settings())
            conn = await connect_one()
            try:
                user = await auth.admin_create_user(email=email, password=password)
                await conn.execute(
                    "insert into public.profiles (id, full_name, email, role, school_id, must_change_password, is_active) "
                    "values ($1,$2,$3,$4,$5,$6,$7)",
                    uuid.UUID(user["id"]), f"Test {key}", email, role,
                    uuid.UUID(school_id) if school_id else None, must_change, active,
                )
                return Account(key, email, password, user["id"], role, school_id)
            finally:
                await auth.aclose()
                await conn.close()

        acct = run(go())
        self.created.append(acct)
        return acct


async def _purge_old_test_users() -> None:
    """Removes leftovers of an earlier crashed run."""
    auth = SupabaseAuth(get_settings())
    conn = await connect_one()
    try:
        rows = await conn.fetch("select id from auth.users where email like $1", f"{EMAIL_PREFIX}%@{DOMAIN}")
        for r in rows:
            await auth.admin_delete_user(str(r["id"]))
        await _delete_test_schools(conn)
    finally:
        await auth.aclose()
        await conn.close()


async def _delete_test_schools(conn) -> None:
    """Courses first: a school that still owns courses cannot be removed."""
    await conn.execute("delete from public.courses where school_id in (select id from public.schools where name like 'ZZ Test School%')")
    await conn.execute("delete from public.schools where name like 'ZZ Test School%'")


async def _teardown(factory: Factory) -> None:
    auth = SupabaseAuth(get_settings())
    conn = await connect_one()
    try:
        for acct in factory.created:
            try:
                await auth.admin_delete_user(acct.user_id)
            except Exception as exc:  # noqa: BLE001
                print("cleanup problem:", acct.email, exc)
        # school_b and anything else a test created by hand
        await _delete_test_schools(conn)
    finally:
        await auth.aclose()
        await conn.close()


# ---------------------------------------------------------------------- fixtures
@pytest.fixture(scope="session", autouse=True)
def _need_env():
    if not ENV_FILE.exists():
        pytest.skip(".env not found - integration tests need the real Supabase keys", allow_module_level=True)


@pytest.fixture(scope="session")
def server():
    """Starts the real API on port 8001 (stricter lock-out so the test is quick)."""
    env = os.environ.copy()
    env["LOGIN_MAX_FAILED_ATTEMPTS"] = str(LOCKOUT_AFTER)
    env["SESSION_CACHE_SECONDS"] = "0"  # these tests edit sessions straight in the database -> check it on every request
    # (the in-memory session cache has its own tests in test_p1_session_cache.py, on a second server)
    log_path = BACKEND_DIR / "tests" / "_server.log"
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(TEST_PORT)],
        cwd=BACKEND_DIR, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(80):
            try:
                if httpx.get(f"{BASE_URL}/api/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise RuntimeError("test server did not start - see backend/tests/_server.log")
        yield BASE_URL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        try:
            log_path.unlink()
        except OSError:
            pass


@pytest.fixture(scope="session")
def factory(server):
    run(_purge_old_test_users())
    school_a = db_val("select id from public.schools where lower(name) = 'school of it'")
    school_b = db_val("insert into public.schools (name) values ($1) returning id", f"ZZ Test School {RUN_ID}")
    f = Factory(str(school_a), str(school_b))
    yield f
    run(_teardown(f))


def new_client() -> httpx.Client:
    """A 'browser': keeps cookies, always says it comes from our own site."""
    return httpx.Client(base_url=BASE_URL, headers={"Origin": ORIGIN}, timeout=40)


@pytest.fixture
def client(server):
    with new_client() as c:
        yield c


def do_login(client: httpx.Client, acct: Account, *, remember: bool = False, password: str | None = None) -> httpx.Response:
    return client.post(
        "/api/auth/login",
        json={"email": acct.email, "password": password if password is not None else acct.password, "remember_me": remember},
    )


@dataclass
class Live:
    """A logged-in account: its own 'browser' + current access token."""
    account: Account
    client: httpx.Client
    token: str

    def get(self, path: str, **kw):
        return self.client.get(path, headers=bearer(self.token), **kw)


@pytest.fixture(scope="session")
def live(factory):
    """One logged-in session per role, shared by the read-only tests (saves sign-ins)."""
    out: dict[str, Live] = {}
    clients: list[httpx.Client] = []
    plan = {
        "admin": factory.create("admin", key="admin"),
        "hod": factory.create("hod", key="hod"),
        "teacher": factory.create("teacher", key="teacher"),
        "teacher_b": factory.create("teacher", school="b", key="teacherb"),
    }
    for name, acct in plan.items():
        c = new_client()
        clients.append(c)
        r = do_login(c, acct)
        assert r.status_code == 200, r.text
        out[name] = Live(acct, c, r.json()["access_token"])
    yield out
    for c in clients:
        c.close()


# =====================================================================================================
#  PHASE 1 support: a dedicated test school (the real "School of IT" is never touched), state snapshots
# =====================================================================================================
def mail_for(name: str) -> str:
    return f"{EMAIL_PREFIX}{name}-{RUN_ID}@{DOMAIN}"


@dataclass
class P1:
    school: str            # test school "P": the HOD + teacher of the Phase 1 tests live here
    school_b: str          # another school (the "foreign" one)
    admin: Live
    hod: Live
    teacher: Live
    teacher_b: Live
    course: str            # "ZZ Course" in school P (6 semesters)
    sems: dict[int, str]   # semester number -> id (school P)
    b_course: str
    b_sems: dict[int, str]
    b_subject: str         # a subject of school B (for "foreign id" tests)

    def api(self, who: Live, method: str, path: str, **kw) -> httpx.Response:
        return who.client.request(method, path, headers=bearer(who.token), **kw)


async def _snapshot() -> dict[str, Any]:
    conn = await connect_one()
    try:
        return {
            "college": dict(await conn.fetchrow("select * from public.college_settings")),
            "terms": [r["id"] for r in await conn.fetch("select id from public.academic_terms")],
            "active": await conn.fetchval("select id from public.academic_terms where is_active"),
            "preloaded": [dict(r) for r in await conn.fetch("select * from public.calendar_entries where is_preloaded and school_id is null")],
        }
    finally:
        await conn.close()


async def _restore(snap: dict[str, Any]) -> None:
    conn = await connect_one()
    auth = SupabaseAuth(get_settings())
    try:
        rows = await conn.fetch("select id from auth.users where email like $1", f"{EMAIL_PREFIX}%-{RUN_ID}@{DOMAIN}")
        ids = [r["id"] for r in rows]
        await conn.execute("delete from public.calendar_entries where created_by = any($1::uuid[]) or title like 'ZZ %'", ids)
        await conn.execute("delete from public.audit_log where actor_email like $1", f"{EMAIL_PREFIX}%@{DOMAIN}")
        # courses first: cascades semesters / subjects / assignments / locked slots (assignments protect teacher_subjects)
        await conn.execute("delete from public.courses where school_id in (select id from public.schools where name like 'ZZ Test School%')")
        for uid in ids:
            await auth.admin_delete_user(str(uid))
        # global settings back to what they were
        c = snap["college"]
        await conn.execute(
            "update public.college_settings set college_start=$1, college_end=$2, lecture_minutes=$3, break_minutes=$4, days_per_week=$5",
            c["college_start"], c["college_end"], c["lecture_minutes"], c["break_minutes"], c["days_per_week"],
        )
        await conn.execute("update public.academic_terms set is_active = false")
        await conn.execute("delete from public.academic_terms where not (id = any($1::uuid[]))", snap["terms"])
        if snap["active"]:
            await conn.execute("update public.academic_terms set is_active = true where id = $1", snap["active"])
        await conn.execute("delete from public.calendar_entries where is_preloaded and school_id is null")
        for e in snap["preloaded"]:
            cols = list(e)
            marks = ", ".join("$" + str(i + 1) for i in range(len(cols)))
            await conn.execute(
                "insert into public.calendar_entries (" + ", ".join(cols) + ") values (" + marks + ")",
                *[e[k] for k in cols],
            )
    finally:
        await auth.aclose()
        await conn.close()


@pytest.fixture(scope="session")
def p1(factory, live):
    snap = run(_snapshot())
    school = str(db_val("insert into public.schools (name) values ($1) returning id", f"ZZ Test School P{RUN_ID}"))

    def make_course(school_id: str, name: str, code: str, n: int):
        cid = db_val("insert into public.courses (school_id, name, code, num_semesters) values ($1,$2,$3,$4) returning id",
                     uuid.UUID(school_id), name, code, n)
        rows = db_fetch("insert into public.semesters (school_id, course_id, number) select $1, $2, g from generate_series(1,$3) g returning id, number",
                        uuid.UUID(school_id), cid, n)
        return str(cid), {r["number"]: str(r["id"]) for r in rows}

    course, sems = make_course(school, "ZZ Course", "ZZC", 6)
    b_course, b_sems = make_course(factory.school_b, "ZZ B Course", "ZZB", 4)
    b_subject = str(db_val(
        "insert into public.subjects (school_id, semester_id, name, code, credits, semester_hours) values ($1,$2,'ZZ B Subject','ZZB-1',3,45) returning id",
        uuid.UUID(factory.school_b), uuid.UUID(b_sems[1]),
    ))
    hod_acct = factory.create("hod", school_id=school, key="p1hod")
    teacher_acct = factory.create("teacher", school_id=school, key="p1teacher")
    clients = []
    logged = {}
    for key, acct in (("hod", hod_acct), ("teacher", teacher_acct)):
        c = new_client()
        clients.append(c)
        r = do_login(c, acct)
        assert r.status_code == 200, r.text
        logged[key] = Live(acct, c, r.json()["access_token"])
    ctx = P1(school, factory.school_b, live["admin"], logged["hod"], logged["teacher"], live["teacher_b"],
             course, sems, b_course, b_sems, b_subject)
    yield ctx
    for c in clients:
        c.close()
    run(_restore(snap))
