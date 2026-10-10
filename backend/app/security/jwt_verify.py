"""Verifies Supabase access tokens (JWT).

Normal case  : the project signs tokens with an asymmetric key (ES256). We check the signature
               locally with the public key from the project's JWKS endpoint (fast, no network per request).
Fallback case: very old projects using a shared secret (HS256) - then we ask Supabase Auth itself.
Either way the token must be genuine, not expired, for the right audience and the right project.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWK, PyJWKError

from ..config import Settings
from ..errors import ApiError
from ..supabase_auth import AuthError, SupabaseAuth

log = logging.getLogger("tt.jwt")

_ASYMMETRIC = {"ES256", "RS256", "EdDSA"}
_KEY_TTL = 600.0          # re-read the public keys every 10 minutes
_REFETCH_COOLDOWN = 20.0  # never hammer the JWKS endpoint, even if someone sends random key ids
_LEGACY_TTL = 20.0        # cache for the HS256 fallback (seconds)


@dataclass(frozen=True)
class TokenClaims:
    user_id: str
    session_id: str
    email: str | None
    exp: int


def _invalid(message: str = "Your sign-in is not valid. Please sign in again.") -> ApiError:
    return ApiError(401, "INVALID_TOKEN", message)


class TokenVerifier:
    def __init__(self, settings: Settings, auth: SupabaseAuth) -> None:
        self._issuer = settings.auth_base
        self._auth = auth
        self._keys: dict[str, PyJWK] = {}
        self._keys_loaded_at = 0.0
        self._last_refetch = 0.0
        self._lock = asyncio.Lock()
        self._legacy: dict[str, tuple[float, TokenClaims]] = {}

    # ------------------------------------------------------------------ public API
    async def verify(self, token: str, *, allow_expired: bool = False) -> TokenClaims:
        if not token or len(token) > 4096:
            raise _invalid()
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise _invalid() from None
        alg = header.get("alg")
        if alg in _ASYMMETRIC:
            return await self._verify_asymmetric(token, alg, header.get("kid"), allow_expired)
        if alg == "HS256":
            # A project that publishes asymmetric signing keys never issues HS256 tokens: such a token can only be forged
            # (the classic "algorithm switch" trick), so it is refused without asking anyone.
            await self._refresh_keys()
            if self._keys:
                raise _invalid()
            return await self._verify_via_auth_server(token, allow_expired)
        raise _invalid()  # includes alg=none and anything unexpected

    # ------------------------------------------------------------------ asymmetric (normal) path
    async def _refresh_keys(self, *, force: bool = False) -> None:
        async with self._lock:
            now = time.monotonic()
            fresh = bool(self._keys) and now - self._keys_loaded_at < _KEY_TTL
            if fresh and not force:
                return
            if self._keys and now - self._last_refetch < _REFETCH_COOLDOWN:
                return
            self._last_refetch = now
            try:
                data = await self._auth.jwks()
            except AuthError as exc:
                if self._keys:
                    log.warning("Could not refresh signing keys (%s) - using cached keys", exc.code)
                    return
                raise ApiError(503, "AUTH_UNAVAILABLE", "The sign-in service is not reachable right now.") from exc
            keys: dict[str, PyJWK] = {}
            for jwk in data.get("keys", []):
                try:
                    keys[jwk.get("kid") or ""] = PyJWK(jwk)
                except PyJWKError:
                    continue
            if keys:
                self._keys = keys
                self._keys_loaded_at = now

    async def _key_for(self, kid: str | None) -> PyJWK:
        await self._refresh_keys()
        key = self._keys.get(kid or "")
        if key is None and kid is None and len(self._keys) == 1:
            key = next(iter(self._keys.values()))
        if key is None:
            await self._refresh_keys(force=True)  # maybe the keys were just rotated
            key = self._keys.get(kid or "")
        if key is None:
            raise _invalid()
        return key

    async def _verify_asymmetric(self, token: str, alg: str, kid: str | None, allow_expired: bool) -> TokenClaims:
        key = await self._key_for(kid)
        try:
            payload = jwt.decode(
                token,
                key.key,
                algorithms=[alg],
                audience="authenticated",
                issuer=self._issuer,
                leeway=10,
                options={"require": ["exp", "sub"], "verify_exp": not allow_expired},
            )
        except jwt.ExpiredSignatureError:
            raise ApiError(401, "TOKEN_EXPIRED", "Please refresh your sign-in.") from None
        except jwt.PyJWTError:
            raise _invalid() from None
        return self._to_claims(payload)

    # ------------------------------------------------------------------ legacy HS256 fallback
    async def _verify_via_auth_server(self, token: str, allow_expired: bool) -> TokenClaims:
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        hit = self._legacy.get(digest)
        if hit and hit[0] > now:
            claims = hit[1]
        else:
            try:
                await self._auth.get_user(token)
            except AuthError as exc:
                if exc.status in (401, 403):
                    # only a token the auth server called EXPIRED is genuine-but-old; anything else (a bad signature, a made-up
                    # token) is refused even where an expired token would do (logout) - it is never read unchecked
                    if "expired" not in exc.message.lower():
                        raise _invalid() from None
                    if not allow_expired:
                        raise ApiError(401, "TOKEN_EXPIRED", "Please refresh your sign-in.") from None
                else:
                    raise ApiError(503, "AUTH_UNAVAILABLE", "The sign-in service is not reachable right now.") from exc
            payload = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
            claims = self._to_claims(payload)
            if len(self._legacy) > 2000:
                self._legacy.clear()
            self._legacy[digest] = (now + _LEGACY_TTL, claims)
        if not allow_expired and claims.exp < time.time() - 10:
            raise ApiError(401, "TOKEN_EXPIRED", "Please refresh your sign-in.")
        return claims

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _to_claims(payload: dict[str, Any]) -> TokenClaims:
        try:
            user_id = str(uuid.UUID(str(payload["sub"])))
            session_id = str(uuid.UUID(str(payload["session_id"])))
            exp = int(payload["exp"])
        except (KeyError, ValueError, TypeError):
            raise _invalid() from None
        email = payload.get("email")
        return TokenClaims(user_id=user_id, session_id=session_id, email=email if isinstance(email, str) else None, exp=exp)
