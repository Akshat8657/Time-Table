"""Integration tests of the real API + real Supabase (throw-away accounts only)."""
from __future__ import annotations

import base64
import json
import uuid

import httpx
import pytest

from app.config import get_settings

from .conftest import (
    DOMAIN, LOCKOUT_AFTER, ORIGIN, RUN_ID, bearer, db_exec, db_fetch, db_val, do_login, jwt_payload, new_client,
)

ROUTES = ["/api/admin/overview", "/api/hod/overview", "/api/teacher/overview"]
INTERVAL_SQL = "update public.app_sessions set last_activity_at = now() - ($2 * interval '1 second') where supabase_session_id = $1"


def session_cookie(resp: httpx.Response) -> dict:
    raw = [h for h in resp.headers.get_list("set-cookie") if h.startswith("tt_session=")]
    assert len(raw) == 1, resp.headers.get_list("set-cookie")
    parts = [p.strip() for p in raw[0].split(";")]
    name, _, value = parts[0].partition("=")
    attrs: dict[str, str] = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        attrs[k.lower()] = v
    return {"value": value, "attrs": attrs, "raw": raw[0]}


def set_idle(session_id: str, seconds_ago: int) -> None:
    db_exec(INTERVAL_SQL, uuid.UUID(session_id), seconds_ago)


# =====================================================================
#  1. No token / bad token / wrong role
# =====================================================================
def test_missing_token_is_rejected_on_every_protected_route(client):
    for path in ROUTES + ["/api/auth/me"]:
        r = client.get(path)
        assert r.status_code == 401, path
        assert r.json()["code"] == "NOT_AUTHENTICATED"
    r = client.post("/api/auth/change-password", json={"current_password": "a", "new_password": "b"})
    assert r.status_code == 401


def test_invalid_tokens_are_rejected_on_every_protected_route(client, live):
    good = live["teacher"].token
    header, payload, signature = good.split(".")
    flipped = signature[:-3] + ("AAA" if not signature.endswith("AAA") else "BBB")
    claims = jwt_payload(good)
    claims["sub"] = jwt_payload(live["admin"].token)["sub"]  # a teacher pretending to be the admin
    forged_payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    none_header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    cases = {
        "garbage": "not-a-token",
        "three dots of nothing": "a.b.c",
        "tampered signature": f"{header}.{payload}.{flipped}",
        "claims edited (teacher -> admin)": f"{header}.{forged_payload}.{signature}",
        "alg none": f"{none_header}.{forged_payload}.",
    }
    for label, token in cases.items():
        for path in ROUTES + ["/api/auth/me"]:
            r = client.get(path, headers=bearer(token))
            assert r.status_code == 401, (label, path, r.text)
            assert r.json()["code"] == "INVALID_TOKEN", (label, path)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/api/admin/overview", {"admin": 200, "hod": 403, "teacher": 403}),
        ("/api/hod/overview", {"admin": 200, "hod": 200, "teacher": 403}),
        ("/api/teacher/overview", {"admin": 403, "hod": 200, "teacher": 200}),
    ],
)
def test_every_route_enforces_the_role(live, path, expected):
    for role, status in expected.items():
        r = live[role].get(path)
        assert r.status_code == status, (path, role, r.text)
        if status == 403:
            assert r.json()["code"] == "FORBIDDEN"


def test_dashboards_return_real_data_for_the_right_person(live):
    admin = live["admin"].get("/api/admin/overview").json()
    assert any(s["name"] == "School of IT" for s in admin["schools"])
    hod = live["hod"].get("/api/hod/overview").json()
    assert hod["scope"] == "school" and hod["school"]["name"] == "School of IT"
    teacher = live["teacher"].get("/api/teacher/overview").json()
    assert teacher["school"]["name"] == "School of IT" and teacher["mode"] == "teacher"
    assert live["hod"].get("/api/teacher/overview").json()["mode"] == "hod-teacher-mode"  # HOD is also a teacher


def test_a_genuine_supabase_token_not_issued_by_our_login_is_refused(factory, client):
    """Even a perfectly valid Supabase JWT is useless unless it came through OUR login (session record)."""
    s = get_settings()
    acct = factory.create("teacher")
    r = httpx.post(
        f"{s.supabase_url}/auth/v1/token?grant_type=password",
        headers={"apikey": s.supabase_anon_key},
        json={"email": acct.email, "password": acct.password}, timeout=30,
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    r = client.get("/api/teacher/overview", headers=bearer(token))
    assert r.status_code == 401 and r.json()["code"] == "INVALID_SESSION"


# =====================================================================
#  2. Login errors, friendly messages, rate limit
# =====================================================================
def test_wrong_password_and_unknown_user_look_identical(factory, client):
    acct = factory.create("teacher")
    r1 = do_login(client, acct, password="definitely-wrong-1")
    r2 = client.post("/api/auth/login", json={"email": f"nobody-{RUN_ID}@{DOMAIN}", "password": "whatever-123"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["code"] == r2.json()["code"] == "INVALID_CREDENTIALS"
    assert r1.json()["message"] == r2.json()["message"]  # nobody can find out which emails exist
    assert "set-cookie" not in r1.headers and "access_token" not in r1.text


def test_login_is_locked_after_repeated_failures(client):
    email = f"lockout-{RUN_ID}@{DOMAIN}"
    for i in range(LOCKOUT_AFTER):
        r = client.post("/api/auth/login", json={"email": email, "password": f"wrong-{i}-pass"})
        assert r.status_code == 401, r.text
    r = client.post("/api/auth/login", json={"email": email, "password": "wrong-again-1"})
    assert r.status_code == 429
    body = r.json()
    assert body["code"] == "TOO_MANY_ATTEMPTS" and body["retry_after_seconds"] > 0
    assert int(r.headers["Retry-After"]) > 0
    assert "minute" in body["message"]


def test_validation_errors_never_echo_the_password(client):
    secret = "SuperSecret-Value-123"
    r = client.post("/api/auth/login", json={"email": "not-an-email", "password": secret})
    assert r.status_code == 422 and secret not in r.text
    assert "email" in r.json()["fields"]
    r = client.post("/api/auth/login", content=f'{{"email": "a@b.co", "password": "{secret}", "bogus": 1}}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and secret not in r.text
    r = client.post("/api/auth/login", json={"email": "a@b.co"})
    assert r.status_code == 422 and r.json()["fields"]["password"]


# =====================================================================
#  3. A normal session from login to logout (non-remembered)
# =====================================================================
def test_session_lifecycle_login_refresh_activity_logout(factory):
    acct = factory.create("teacher")
    with new_client() as c:
        # ---- login
        r = do_login(c, acct, remember=False)
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        body = r.json()
        token, sid = body["access_token"], jwt_payload(body["access_token"])["session_id"]
        assert body["user"]["email"] == acct.email and body["user"]["role"] == "teacher"
        assert acct.password not in r.text  # the response never contains the person's password
        assert body["session"] == {
            "remember_me": False, "idle_timeout_seconds": 8 * 3600,
            "idle_expires_in_seconds": pytest.approx(8 * 3600, abs=30),
            "absolute_expires_in_seconds": pytest.approx(24 * 3600, abs=30),
        }

        # ---- cookie: httpOnly, SameSite=Lax, only for /api/auth, dies with the browser, holds no personal data
        ck = session_cookie(r)
        assert "httponly" in ck["attrs"]
        assert ck["attrs"]["samesite"].lower() == "lax"
        assert ck["attrs"]["path"] == "/api/auth"
        assert "max-age" not in ck["attrs"] and "expires" not in ck["attrs"]
        assert "@" not in ck["value"] and ck["value"].count(".") < 2 and acct.email not in ck["raw"]
        other = c.get("/api/teacher/overview", headers=bearer(token))
        assert other.status_code == 200
        assert "cookie" not in other.request.headers  # never sent to ordinary API routes

        # ---- /me works; it counts as activity and restarts the idle clock
        set_idle(sid, 7 * 3600)
        me = c.get("/api/auth/me", headers=bearer(token))
        assert me.status_code == 200 and me.json()["user"]["email"] == acct.email
        assert me.json()["session"]["idle_expires_in_seconds"] > 8 * 3600 - 120
        assert db_val("select extract(epoch from now() - last_activity_at) from public.app_sessions where supabase_session_id = $1",
                      uuid.UUID(sid)) < 120

        # ---- refresh rotates the cookie, gives a new working token, and is NOT user activity
        set_idle(sid, 5 * 3600)
        rf = c.post("/api/auth/refresh")
        assert rf.status_code == 200, rf.text
        assert session_cookie(rf)["value"] != ck["value"]
        new_token = rf.json()["access_token"]
        assert c.get("/api/teacher/overview", headers=bearer(new_token)).status_code == 200  # touches again (>30s old)
        set_idle(sid, 5 * 3600)
        c.post("/api/auth/refresh")
        assert db_val("select extract(epoch from now() - last_activity_at) from public.app_sessions where supabase_session_id = $1",
                      uuid.UUID(sid)) > 4.9 * 3600

        # ---- logout clears everything
        old_cookie = session_cookie(rf)["value"]
        out = c.post("/api/auth/logout", headers=bearer(new_token))
        assert out.status_code == 200
        cleared = session_cookie(out)
        assert cleared["attrs"].get("max-age") == "0" or "1970" in cleared["attrs"].get("expires", "")
        r = c.get("/api/teacher/overview", headers=bearer(new_token))
        assert r.status_code == 401 and r.json()["code"] in ("SESSION_REVOKED", "INVALID_SESSION")  # token is still "valid" cryptographically
        assert c.get("/api/auth/me", headers=bearer(token)).status_code == 401
        with new_client() as thief:  # somebody replays the old cookie
            thief.cookies.set("tt_session", old_cookie, domain="127.0.0.1", path="/api/auth")
            assert thief.post("/api/auth/refresh").status_code == 401
        assert db_val("select count(*) from auth.sessions where user_id = $1", uuid.UUID(acct.user_id)) == 0
        assert db_val("select revoked_reason from public.app_sessions where supabase_session_id = $1", uuid.UUID(sid)) == "logout"


def test_logout_works_with_only_the_cookie(factory):
    acct = factory.create("teacher")
    with new_client() as c:
        r = do_login(c, acct)
        token = r.json()["access_token"]
        out = c.post("/api/auth/logout")  # no Authorization header at all
        assert out.status_code == 200
        assert c.get("/api/auth/me", headers=bearer(token)).status_code == 401
        assert db_val("select count(*) from auth.sessions where user_id = $1", uuid.UUID(acct.user_id)) == 0
        assert c.post("/api/auth/refresh").status_code == 401  # the cookie is gone too


# =====================================================================
#  4. Auto-expiry after inactivity, absolute limit, remember me
# =====================================================================
def test_inactive_session_expires_after_the_idle_time(factory):
    acct = factory.create("teacher")
    with new_client() as c:
        r = do_login(c, acct)
        token, sid = r.json()["access_token"], jwt_payload(r.json()["access_token"])["session_id"]
        assert c.get("/api/teacher/overview", headers=bearer(token)).status_code == 200

        set_idle(sid, 7 * 3600 + 59 * 60)  # just under 8 hours: still fine
        assert c.get("/api/teacher/overview", headers=bearer(token)).status_code == 200

        set_idle(sid, 8 * 3600 + 60)  # just over 8 hours, and the page was reopened (refresh) -> expired
        rf = c.post("/api/auth/refresh")
        assert rf.status_code == 401 and rf.json()["code"] == "SESSION_EXPIRED" and rf.json()["reason"] == "idle"
        assert "inactive" in rf.json()["message"]
        assert session_cookie(rf)["attrs"].get("max-age") == "0"  # cookie wiped
        again = c.get("/api/teacher/overview", headers=bearer(token))  # the old token is dead as well
        assert again.status_code == 401 and again.json()["code"] == "SESSION_EXPIRED"
        assert db_val("select count(*) from auth.sessions where user_id = $1", uuid.UUID(acct.user_id)) == 0


def test_expiry_is_also_enforced_when_an_api_call_arrives_after_a_long_pause(factory):
    acct = factory.create("teacher")
    with new_client() as c:
        r = do_login(c, acct)
        token, sid = r.json()["access_token"], jwt_payload(r.json()["access_token"])["session_id"]
        set_idle(sid, 9 * 3600)
        r = c.get("/api/teacher/overview", headers=bearer(token))
        assert r.status_code == 401 and r.json()["code"] == "SESSION_EXPIRED" and r.json()["reason"] == "idle"


def test_session_has_a_hard_maximum_lifetime(factory):
    acct = factory.create("teacher")
    with new_client() as c:
        r = do_login(c, acct)
        token, sid = r.json()["access_token"], jwt_payload(r.json()["access_token"])["session_id"]
        db_exec("update public.app_sessions set absolute_expires_at = now() - interval '1 minute' where supabase_session_id = $1",
                uuid.UUID(sid))
        r = c.get("/api/teacher/overview", headers=bearer(token))
        assert r.status_code == 401 and r.json()["code"] == "SESSION_EXPIRED" and r.json()["reason"] == "absolute"


def test_remember_me_keeps_the_session_much_longer(factory):
    acct = factory.create("teacher")
    with new_client() as c:
        r = do_login(c, acct, remember=True)
        assert r.status_code == 200
        token, sid = r.json()["access_token"], jwt_payload(r.json()["access_token"])["session_id"]
        s = get_settings()
        ck = session_cookie(r)
        assert ck["attrs"]["max-age"] == str(int(s.remember_me_days * 86400))  # persistent cookie
        assert "httponly" in ck["attrs"] and ck["attrs"]["samesite"].lower() == "lax"
        assert r.json()["session"]["remember_me"] is True
        assert r.json()["session"]["idle_timeout_seconds"] == int(s.remember_me_days * 86400)

        set_idle(sid, 9 * 3600)  # would have killed a normal session
        assert c.get("/api/teacher/overview", headers=bearer(token)).status_code == 200
        set_idle(sid, 20 * 86400)
        assert c.get("/api/teacher/overview", headers=bearer(token)).status_code == 200
        rf = c.post("/api/auth/refresh")  # page reopened 20 days later: still signed in
        assert rf.status_code == 200 and session_cookie(rf)["attrs"]["max-age"] == ck["attrs"]["max-age"]

        set_idle(sid, 31 * 86400)  # but not forever
        r = c.get("/api/teacher/overview", headers=bearer(rf.json()["access_token"]))
        assert r.status_code == 401 and r.json()["code"] == "SESSION_EXPIRED"


def test_housekeeping_ends_sessions_nobody_came_back_to(factory):
    import asyncio
    from app.db import connect_one
    from app.security import sessions
    acct = factory.create("teacher")
    with new_client() as c:
        r = do_login(c, acct)
        sid = jwt_payload(r.json()["access_token"])["session_id"]
    set_idle(sid, 9 * 3600)

    async def go():
        import app.db as db
        await db.init_pool()
        try:
            return await sessions.cleanup_expired(get_settings())
        finally:
            await db.close_pool()
    assert asyncio.run(go()) >= 1
    assert db_val("select count(*) from auth.sessions where id = $1", uuid.UUID(sid)) == 0
    assert db_val("select revoked_reason from public.app_sessions where supabase_session_id = $1", uuid.UUID(sid)) == "expired"


# =====================================================================
#  5. First login: forced password change
# =====================================================================
def test_first_login_forces_a_password_change(factory):
    acct = factory.create("teacher", must_change=True)
    new_pw = "Brand-New-Pass-77"
    with new_client() as c, new_client() as other_device:
        r = do_login(c, acct)
        assert r.status_code == 200 and r.json()["user"]["must_change_password"] is True
        token = r.json()["access_token"]
        r2 = do_login(other_device, acct)
        other_token = r2.json()["access_token"]

        assert c.get("/api/auth/me", headers=bearer(token)).status_code == 200  # allowed, so the page can show the form
        blocked = c.get("/api/teacher/overview", headers=bearer(token))
        assert blocked.status_code == 403 and blocked.json()["code"] == "PASSWORD_CHANGE_REQUIRED"

        # wrong current password
        r = c.post("/api/auth/change-password", headers=bearer(token),
                   json={"current_password": "wrong-current-1", "new_password": new_pw})
        assert r.status_code == 400 and r.json()["code"] == "CURRENT_PASSWORD_INCORRECT"
        assert "current_password" in r.json()["fields"]

        # weak new passwords are refused with a helpful message
        for weak in ("short1", "alllettersnodigitsx", "123456789012", "Password123"):
            r = c.post("/api/auth/change-password", headers=bearer(token),
                       json={"current_password": acct.password, "new_password": weak})
            assert r.status_code == 422 and r.json()["code"] == "WEAK_PASSWORD", weak
            assert r.json()["fields"]["new_password"]
        r = c.post("/api/auth/change-password", headers=bearer(token),
                   json={"current_password": acct.password, "new_password": acct.password})
        assert r.status_code == 422 and "different" in r.json()["message"]
        assert c.get("/api/teacher/overview", headers=bearer(token)).status_code == 403  # still locked

        # the real change
        r = c.post("/api/auth/change-password", headers=bearer(token),
                   json={"current_password": acct.password, "new_password": new_pw})
        assert r.status_code == 200, r.text
        assert r.json()["user"]["must_change_password"] is False
        assert c.get("/api/teacher/overview", headers=bearer(token)).status_code == 200  # this device stays signed in
        gone = other_device.get("/api/auth/me", headers=bearer(other_token))
        assert gone.status_code == 401  # every OTHER device is signed out
        assert db_val("select must_change_password from public.profiles where id = $1", uuid.UUID(acct.user_id)) is False
        assert db_val("select count(*) from auth.sessions where user_id = $1", uuid.UUID(acct.user_id)) == 1  # only this one (no leftovers)

    with new_client() as fresh:
        assert do_login(fresh, acct).status_code == 401  # old password is dead
        ok = do_login(fresh, acct, password=new_pw)
        assert ok.status_code == 200 and ok.json()["user"]["must_change_password"] is False


# =====================================================================
#  6. Disabled accounts
# =====================================================================
def test_deactivated_account_cannot_log_in(factory, client):
    acct = factory.create("teacher", active=False)
    r = do_login(client, acct)
    assert r.status_code == 403 and r.json()["code"] == "ACCOUNT_DISABLED"
    assert "set-cookie" not in r.headers
    assert db_val("select count(*) from auth.sessions where user_id = $1", uuid.UUID(acct.user_id)) == 0
    assert db_val("select count(*) from public.app_sessions where user_id = $1", uuid.UUID(acct.user_id)) == 0


def test_account_deactivated_while_logged_in_is_cut_off(factory):
    acct = factory.create("teacher")
    with new_client() as c:
        token = do_login(c, acct).json()["access_token"]
        assert c.get("/api/teacher/overview", headers=bearer(token)).status_code == 200
        db_exec("update public.profiles set is_active = false where id = $1", uuid.UUID(acct.user_id))
        r = c.get("/api/teacher/overview", headers=bearer(token))
        assert r.status_code == 403 and r.json()["code"] == "ACCOUNT_DISABLED"
        assert db_val("select count(*) from auth.sessions where user_id = $1", uuid.UUID(acct.user_id)) == 0


# =====================================================================
#  7. No public signup, CORS, origin guard, host guard
# =====================================================================
def test_public_signup_is_closed():
    s = get_settings()
    before = db_val("select count(*) from auth.users")
    r = httpx.post(
        f"{s.supabase_url}/auth/v1/signup", headers={"apikey": s.supabase_anon_key},
        json={"email": f"intruder-{RUN_ID}@{DOMAIN}", "password": "Intruder-Pass-123"}, timeout=30,
    )
    assert r.status_code in (400, 401, 403, 422), r.text
    assert "signup" in r.text.lower()
    assert db_val("select count(*) from auth.users") == before
    # and our own API has no signup / register door either
    with new_client() as c:
        for path in ("/api/auth/signup", "/api/auth/register", "/api/signup"):
            assert c.post(path, json={"email": "x@y.co", "password": "Whatever-123"}).status_code in (404, 405)


def test_cors_origin_and_host_guards(server):
    base = server
    # an allowed origin may talk to the API (with cookies)
    r = httpx.options(f"{base}/api/auth/login", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN and r.headers["access-control-allow-credentials"] == "true"
    # a foreign website may not
    r = httpx.options(f"{base}/api/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers
    # state-changing requests from a foreign site are refused outright
    for origin in ("https://evil.example", "http://localhost:3000", "null"):
        r = httpx.post(f"{base}/api/auth/logout", headers={"Origin": origin})
        assert r.status_code == 403 and r.json()["code"] == "BAD_ORIGIN", origin
    # unknown host names (DNS rebinding) are refused
    assert httpx.get(f"{base}/api/health", headers={"Host": "evil.example"}).status_code == 400
    # the usual hardening headers are present
    h = httpx.get(f"{base}/api/health").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["cache-control"] == "no-store"
    assert httpx.get(f"{base}/api/docs").status_code == 404  # API docs page is switched off
