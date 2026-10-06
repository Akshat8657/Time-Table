"""Thin async client for the Supabase Auth (GoTrue) REST API.

Passwords are only ever checked / stored by Supabase Auth - we just forward them over HTTPS.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger("tt.supabase")


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _key_headers(key: str) -> dict[str, str]:
    """New sb_publishable_/sb_secret_ keys travel ONLY in the apikey header.
    Legacy JWT-style keys (eyJ...) are also sent as a Bearer token."""
    headers = {"apikey": key}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


class SupabaseAuth:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.auth_base
        self._anon = settings.supabase_anon_key
        self._service = settings.supabase_service_role_key
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ plumbing
    async def _call(
        self,
        method: str,
        path: str,
        *,
        key: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        bearer: str | None = None,
    ) -> httpx.Response:
        headers = _key_headers(key)
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        try:
            return await self._http.request(method, f"{self._base}{path}", headers=headers, json=json, params=params)
        except httpx.TimeoutException as exc:
            raise AuthError(503, "AUTH_UNAVAILABLE", "The sign-in service timed out.") from exc
        except httpx.HTTPError as exc:
            raise AuthError(503, "AUTH_UNAVAILABLE", "Cannot reach the sign-in service.") from exc

    @staticmethod
    def _error(resp: httpx.Response) -> AuthError:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        code = str(body.get("error_code") or body.get("error") or "unknown")
        message = str(body.get("msg") or body.get("error_description") or body.get("message") or resp.reason_phrase)
        return AuthError(resp.status_code, code, message)

    async def _json(self, resp: httpx.Response, ok: tuple[int, ...] = (200,)) -> dict[str, Any]:
        if resp.status_code in ok:
            if resp.status_code == 204 or not resp.content:
                return {}
            return resp.json()
        raise self._error(resp)

    # ------------------------------------------------------------------ user-facing flows
    async def sign_in_password(self, email: str, password: str) -> dict[str, Any]:
        resp = await self._call(
            "POST", "/token", key=self._anon, params={"grant_type": "password"},
            json={"email": email, "password": password},
        )
        return await self._json(resp)

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        resp = await self._call(
            "POST", "/token", key=self._anon, params={"grant_type": "refresh_token"},
            json={"refresh_token": refresh_token},
        )
        return await self._json(resp)

    async def get_user(self, access_token: str) -> dict[str, Any]:
        resp = await self._call("GET", "/user", key=self._anon, bearer=access_token)
        return await self._json(resp)

    async def update_own_password(self, access_token: str, new_password: str) -> dict[str, Any]:
        """Changes the password using the person's OWN session token.

        Supabase then keeps THIS session alive and signs out all the others. (Changing it with the
        admin key instead would delete every session of the user - including the one in use.)
        """
        resp = await self._call("PUT", "/user", key=self._anon, bearer=access_token, json={"password": new_password})
        return await self._json(resp)

    # ------------------------------------------------------------------ public info
    async def settings(self) -> dict[str, Any]:
        return await self._json(await self._call("GET", "/settings", key=self._anon))

    async def jwks(self) -> dict[str, Any]:
        return await self._json(await self._call("GET", "/.well-known/jwks.json", key=self._anon))

    async def signup_attempt(self, email: str, password: str) -> httpx.Response:
        """Only used by the self-test: proves that public signup is really closed."""
        return await self._call("POST", "/signup", key=self._anon, json={"email": email, "password": password})

    # ------------------------------------------------------------------ admin API (service key)
    async def admin_create_user(
        self,
        *,
        email: str,
        password: str,
        user_metadata: dict[str, Any] | None = None,
        app_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"email": email, "password": password, "email_confirm": True}
        if user_metadata:
            payload["user_metadata"] = user_metadata
        if app_metadata:
            payload["app_metadata"] = app_metadata
        resp = await self._call("POST", "/admin/users", key=self._service, json=payload)
        return await self._json(resp, ok=(200, 201))

    async def admin_update_user(self, user_id: str, **attrs: Any) -> dict[str, Any]:
        resp = await self._call("PUT", f"/admin/users/{user_id}", key=self._service, json=attrs)
        return await self._json(resp)

    async def admin_delete_user(self, user_id: str) -> None:
        resp = await self._call("DELETE", f"/admin/users/{user_id}", key=self._service)
        if resp.status_code not in (200, 204, 404):
            raise self._error(resp)
