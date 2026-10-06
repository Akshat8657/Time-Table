"""Unit tests - no network, no database."""
from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from app.config import Settings, parse_db_url
from app.errors import ApiError
from app.security import sessions
from app.security.jwt_verify import TokenVerifier
from app.security.passwords import validate_new_password
from app.security.rate_limit import AttemptLimiter
from app.supabase_auth import AuthError

ISSUER = "https://abcdefghijklmnopqrst.supabase.co/auth/v1"


# ---------------------------------------------------------------------- config
def test_db_url_with_nasty_password_is_parsed_without_encoding():
    p = parse_db_url("postgresql://postgres.abcd:p@ss/w#rd?x:y@aws-0-ap-south-1.pooler.supabase.com:5432/postgres")
    assert p["user"] == "postgres.abcd"
    assert p["password"] == "p@ss/w#rd?x:y"
    assert p["host"] == "aws-0-ap-south-1.pooler.supabase.com"
    assert p["port"] == 5432 and p["database"] == "postgres"


def test_dashboard_url_pasted_by_mistake_is_repaired():
    s = Settings(
        _env_file=None,
        supabase_url="https://supabase.com/dashboard/project/abcdefghijklmnopqrst/settings/general",
        supabase_anon_key="a", supabase_service_role_key="b", supabase_db_url="postgresql://u:p@h:5432/postgres",
    )
    assert s.supabase_url == "https://abcdefghijklmnopqrst.supabase.co"
    assert s.auth_base == "https://abcdefghijklmnopqrst.supabase.co/auth/v1"


def test_settings_reject_empty_and_nonsense():
    with pytest.raises(Exception):
        Settings(_env_file=None, supabase_url="", supabase_anon_key="a", supabase_service_role_key="b", supabase_db_url="x")
    with pytest.raises(Exception):
        Settings(_env_file=None, supabase_url="https://x.supabase.co", supabase_anon_key="a",
                 supabase_service_role_key="b", supabase_db_url="x", session_idle_timeout_hours=0)


# ---------------------------------------------------------------------- passwords
@pytest.mark.parametrize(
    "new, current, expected_ok",
    [
        ("Sunrise2026x", "Old-Pass-1", True),
        ("short1", "Old-Pass-1", False),            # too short
        ("onlyletterslong", "Old-Pass-1", False),   # no number
        ("12345678901", "Old-Pass-1", False),       # no letter
        ("Old-Pass-1", "Old-Pass-1", False),        # same as current
        ("Password123", "Old-Pass-1", False),       # too common
        ("a" * 80 + "1", "Old-Pass-1", False),      # longer than 72 bytes
    ],
)
def test_password_rules(new, current, expected_ok):
    assert (validate_new_password(new, current, "someone@college.edu") is None) is expected_ok


def test_password_may_not_contain_email_name():
    assert validate_new_password("rahulsharma99", "x", "rahulsharma@college.edu") is not None


# ---------------------------------------------------------------------- rate limiter
def _limiter(max_subject=3, max_ip=10, window=0.3) -> AttemptLimiter:
    stub = SimpleNamespace(login_window_seconds=window, login_max_failed_attempts=max_subject, login_ip_max_attempts=max_ip)
    return AttemptLimiter(stub, prefix="t")  # type: ignore[arg-type]


def test_limiter_blocks_after_n_failures_then_recovers():
    lim = _limiter()
    for _ in range(3):
        lim.check("1.1.1.1", "a@b.co")
        lim.failure("1.1.1.1", "a@b.co")
    with pytest.raises(ApiError) as e:
        lim.check("1.1.1.1", "a@b.co")
    assert e.value.status_code == 429 and e.value.code == "TOO_MANY_ATTEMPTS"
    assert int(e.value.headers["Retry-After"]) >= 1
    lim.check("1.1.1.1", "other@b.co")  # another account is not affected
    time.sleep(0.35)
    lim.check("1.1.1.1", "a@b.co")      # window passed -> allowed again


def test_limiter_success_clears_account_counter():
    lim = _limiter()
    lim.failure("2.2.2.2", "a@b.co")
    lim.failure("2.2.2.2", "a@b.co")
    lim.success("a@b.co")
    lim.failure("2.2.2.2", "a@b.co")
    lim.check("2.2.2.2", "a@b.co")  # only 1 failure counted since the success


def test_limiter_blocks_one_computer_guessing_many_accounts():
    lim = _limiter(max_subject=3, max_ip=4)
    for i in range(4):
        lim.failure("3.3.3.3", f"user{i}@b.co")
    with pytest.raises(ApiError):
        lim.check("3.3.3.3", "brand-new@b.co")


# ---------------------------------------------------------------------- session expiry rules
def _row(**kw):
    now = datetime.now(timezone.utc)
    base = dict(
        remember_me=False, last_activity_at=now, absolute_expires_at=now + timedelta(hours=24),
        revoked_at=None, revoked_reason=None,
    )
    base.update(kw)
    return base


def _settings():
    return SimpleNamespace(
        idle_timeout=timedelta(hours=8), remember_idle=timedelta(days=30),
        absolute_max=timedelta(hours=24), remember_absolute_max=timedelta(days=90),
    )


def test_fresh_session_is_valid():
    assert sessions.expiry_reason(_row(), _settings()) is None  # type: ignore[arg-type]


def test_idle_session_expires_after_8_hours_but_not_before():
    now = datetime.now(timezone.utc)
    assert sessions.expiry_reason(_row(last_activity_at=now - timedelta(hours=7, minutes=59)), _settings()) is None  # type: ignore[arg-type]
    assert sessions.expiry_reason(_row(last_activity_at=now - timedelta(hours=8, minutes=1)), _settings()) == "idle"  # type: ignore[arg-type]


def test_remember_me_session_survives_a_long_idle_time():
    now = datetime.now(timezone.utc)
    assert sessions.expiry_reason(_row(remember_me=True, last_activity_at=now - timedelta(days=20)), _settings()) is None  # type: ignore[arg-type]
    assert sessions.expiry_reason(_row(remember_me=True, last_activity_at=now - timedelta(days=31)), _settings()) == "idle"  # type: ignore[arg-type]


def test_absolute_limit_and_revocation():
    now = datetime.now(timezone.utc)
    assert sessions.expiry_reason(_row(absolute_expires_at=now - timedelta(seconds=1)), _settings()) == "absolute"  # type: ignore[arg-type]
    assert sessions.expiry_reason(_row(revoked_at=now, revoked_reason="logout"), _settings()) == "revoked"  # type: ignore[arg-type]
    assert sessions.expiry_reason(_row(revoked_at=now, revoked_reason="idle"), _settings()) == "idle"  # type: ignore[arg-type]


# ---------------------------------------------------------------------- JWT verification (local test key, no network)
def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


class FakeAuth:
    def __init__(self, jwks: dict) -> None:
        self._jwks = jwks
        self.jwks_calls = 0

    async def jwks(self):
        self.jwks_calls += 1
        return self._jwks

    async def get_user(self, token: str):
        raise AuthError(401, "bad_jwt", "invalid JWT")


@pytest.fixture
def keypair():
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update({"kid": "k1", "alg": "ES256", "use": "sig"})
    return key, {"keys": [jwk]}


def _payload(**kw):
    now = int(time.time())
    p = {
        "sub": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "aud": "authenticated", "iss": ISSUER,
        "iat": now, "exp": now + 3600, "email": "x@y.co", "role": "authenticated",
    }
    p.update(kw)
    return p


def _verifier(jwks):
    auth = FakeAuth(jwks)
    return TokenVerifier(SimpleNamespace(auth_base=ISSUER), auth), auth  # type: ignore[arg-type]


def _sign(key, payload, kid="k1"):
    return jwt.encode(payload, key, algorithm="ES256", headers={"kid": kid})


async def test_valid_token_is_accepted(keypair):
    key, jwks = keypair
    v, _ = _verifier(jwks)
    p = _payload()
    claims = await v.verify(_sign(key, p))
    assert claims.user_id == p["sub"] and claims.session_id == p["session_id"]


async def test_expired_token_is_reported_as_expired_but_can_be_read_for_logout(keypair):
    key, jwks = keypair
    v, _ = _verifier(jwks)
    token = _sign(key, _payload(exp=int(time.time()) - 600))
    with pytest.raises(ApiError) as e:
        await v.verify(token)
    assert e.value.code == "TOKEN_EXPIRED"
    assert (await v.verify(token, allow_expired=True)).session_id


@pytest.mark.parametrize("bad", [{"aud": "somebody-else"}, {"iss": "https://evil.example/auth/v1"}, {"session_id": None}, {"sub": "not-a-uuid"}])
async def test_token_with_wrong_claims_is_refused(keypair, bad):
    key, jwks = keypair
    v, _ = _verifier(jwks)
    payload = _payload(**bad)
    if bad.get("session_id", 1) is None:
        payload.pop("session_id")
    with pytest.raises(ApiError) as e:
        await v.verify(_sign(key, payload))
    assert e.value.status_code == 401 and e.value.code == "INVALID_TOKEN"


async def test_token_signed_by_a_different_key_is_refused(keypair):
    _, jwks = keypair
    attacker = ec.generate_private_key(ec.SECP256R1())
    v, _ = _verifier(jwks)
    with pytest.raises(ApiError) as e:
        await v.verify(_sign(attacker, _payload()))  # claims to be key k1, but is not
    assert e.value.code == "INVALID_TOKEN"


async def test_unknown_key_id_is_refused(keypair):
    key, jwks = keypair
    v, _ = _verifier(jwks)
    with pytest.raises(ApiError) as e:
        await v.verify(_sign(key, _payload(), kid="nope"))
    assert e.value.code == "INVALID_TOKEN"


async def test_unsigned_alg_none_token_is_refused(keypair):
    _, jwks = keypair
    v, _ = _verifier(jwks)
    forged = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_payload())}."
    with pytest.raises(ApiError) as e:
        await v.verify(forged)
    assert e.value.code == "INVALID_TOKEN"


async def test_hs256_token_forged_with_a_guessed_secret_is_refused(keypair):
    _, jwks = keypair
    v, _ = _verifier(jwks)
    forged = jwt.encode(_payload(), "guessed-secret-guessed-secret-1234", algorithm="HS256")
    with pytest.raises(ApiError) as e:
        await v.verify(forged)  # falls back to asking Supabase, which says "invalid"
    assert e.value.code == "INVALID_TOKEN"


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer x", "x" * 5000])
async def test_garbage_is_refused(keypair, garbage):
    _, jwks = keypair
    v, _ = _verifier(jwks)
    with pytest.raises(ApiError) as e:
        await v.verify(garbage)
    assert e.value.code == "INVALID_TOKEN"


async def test_public_keys_are_cached(keypair):
    key, jwks = keypair
    v, auth = _verifier(jwks)
    for _ in range(5):
        await v.verify(_sign(key, _payload()))
    assert auth.jwks_calls == 1
