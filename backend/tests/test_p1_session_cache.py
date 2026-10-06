"""The short in-memory memory of checked login sessions (speed layer).

A second copy of the API is started on port 8002 with SESSION_CACHE_SECONDS=6 (the main test server checks the database on
every request, because other tests edit sessions directly in the database).  What must hold:
  * a session that was just checked is remembered for a few seconds (that is the whole point: no database trip per click) ...
  * ... but every way of ending / changing a session inside the program is seen AT ONCE: logout, deactivate, password reset,
    password change.  A person who is signed out must never get one more answer.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import httpx
import pytest

from .conftest import BACKEND_DIR, ORIGIN, bearer, db_exec, db_val, jwt_payload

PORT = 8002
BASE = f"http://127.0.0.1:{PORT}"
TTL = 6


@pytest.fixture(scope="module")
def cached_server(factory):
    env = os.environ.copy()
    env["SESSION_CACHE_SECONDS"] = str(TTL)
    env["DB_POOL_MIN"], env["DB_POOL_MAX"] = "1", "3"
    log_path = BACKEND_DIR / "tests" / "_server_cached.log"
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=BACKEND_DIR, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(80):
            try:
                if httpx.get(f"{BASE}/api/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise RuntimeError("cached test server did not start - see backend/tests/_server_cached.log")
        yield BASE
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


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE, headers={"Origin": ORIGIN}, timeout=40)


def sign_in(c: httpx.Client, acct) -> str:
    r = c.post("/api/auth/login", json={"email": acct.email, "password": acct.password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def me(c: httpx.Client, token: str) -> httpx.Response:
    return c.get("/api/auth/me", headers=bearer(token))


def test_a_checked_session_is_remembered_for_a_few_seconds_then_checked_again(cached_server, factory, p1):
    acct = factory.create("teacher", school_id=p1.school, key="cache1")
    with client() as c:
        token = sign_in(c, acct)
        assert me(c, token).status_code == 200                    # checked against the database, now remembered
        sid = jwt_payload(token)["session_id"]
        db_exec("update public.app_sessions set revoked_at = now(), revoked_reason = 'logout' where supabase_session_id = $1", __import__("uuid").UUID(sid))
        assert me(c, token).status_code == 200                    # still remembered (an edit made behind the program's back is not seen yet)
        time.sleep(TTL + 1.5)
        r = me(c, token)                                          # the memory expired -> the database is asked again
        assert r.status_code == 401, r.text


def test_deactivating_someone_ends_their_session_at_once(cached_server, factory, p1):
    acct = factory.create("teacher", school_id=p1.school, key="cache2")
    with client() as c, client() as boss:
        token = sign_in(c, acct)
        assert me(c, token).status_code == 200 and me(c, token).status_code == 200      # remembered
        r = boss.post(f"/api/users/{acct.user_id}/deactivate", headers=bearer(p1.hod.token))
        assert r.status_code == 200, r.text
        after = me(c, token)                                      # no waiting: the very next answer is already "no"
        assert after.status_code in (401, 403), after.text
        assert boss.post(f"/api/users/{acct.user_id}/reactivate", headers=bearer(p1.hod.token)).status_code == 200


def test_a_password_reset_ends_every_session_at_once(cached_server, factory, p1):
    acct = factory.create("teacher", school_id=p1.school, key="cache3")
    with client() as c, client() as boss:
        token = sign_in(c, acct)
        assert me(c, token).status_code == 200 and me(c, token).status_code == 200
        r = boss.post(f"/api/users/{acct.user_id}/reset-password", headers=bearer(p1.hod.token))
        assert r.status_code == 200, r.text
        assert me(c, token).status_code in (401, 403)


def test_signing_out_is_seen_at_once(cached_server, factory, p1):
    acct = factory.create("teacher", school_id=p1.school, key="cache4")
    with client() as c:
        token = sign_in(c, acct)
        assert me(c, token).status_code == 200 and me(c, token).status_code == 200
        assert c.post("/api/auth/logout", headers=bearer(token)).status_code == 200
        assert me(c, token).status_code in (401, 403)
        assert c.get("/api/profile/me", headers=bearer(token)).status_code in (401, 403)


def test_changing_the_first_password_is_seen_at_once(cached_server, factory, p1):
    acct = factory.create("teacher", school_id=p1.school, key="cache5", must_change=True)
    with client() as c:
        token = sign_in(c, acct)
        blocked = c.get("/api/profile/me", headers=bearer(token))                       # remembered as "must change the password first"
        assert blocked.status_code == 403 and blocked.json()["code"] == "PASSWORD_CHANGE_REQUIRED"
        assert c.get("/api/profile/me", headers=bearer(token)).status_code == 403
        r = c.post("/api/auth/change-password", headers=bearer(token), json={"current_password": acct.password, "new_password": "Brand-New-Pass-7x9"})
        assert r.status_code == 200, r.text
        assert c.get("/api/profile/me", headers=bearer(token)).status_code == 200      # no waiting for the memory to expire
        assert db_val("select must_change_password from public.profiles where id = $1", __import__("uuid").UUID(acct.user_id)) is False
