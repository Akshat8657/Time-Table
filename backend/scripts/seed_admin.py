"""Create (or reset) the first ADMIN account.

  python -m scripts.seed_admin           creates the admin if missing; prints the temporary password ONCE
  python -m scripts.seed_admin --reset   gives the existing admin a NEW temporary password
                                         (and forces a password change at the next login)

The admin email comes from SEED_ADMIN_EMAIL in .env.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import secrets
import sys
import uuid
from typing import Any

from pydantic import ValidationError

from app.config import Settings, explain_config_error, get_settings
from app.db import connect_one
from app.supabase_auth import AuthError, SupabaseAuth

# no 0/O/1/l/I - easy to read and type
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def make_temp_password() -> str:
    """Like  Kp7m-Xw3q-Rt9v  (12 random characters, at least one capital, small letter and number)."""
    while True:
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(12))
        if any(c.isupper() for c in raw) and any(c.islower() for c in raw) and any(c.isdigit() for c in raw):
            return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


async def ensure_admin(settings: Settings, email: str, name: str, *, reset: bool = False) -> dict[str, Any]:
    email = email.strip().lower()
    if not _EMAIL.match(email):
        raise ValueError("SEED_ADMIN_EMAIL in .env is not a valid email address")

    conn = await connect_one()
    auth = SupabaseAuth(settings)
    try:
        existing = await conn.fetchrow("select id from auth.users where lower(email) = $1", email)

        # ---- brand-new admin
        if existing is None:
            password = make_temp_password()
            user = await auth.admin_create_user(email=email, password=password)
            user_id = uuid.UUID(user["id"])
            try:
                await conn.execute(
                    "insert into public.profiles (id, full_name, email, role, school_id, must_change_password) "
                    "values ($1, $2, $3, 'admin', null, true)",
                    user_id, name, email,
                )
            except Exception:
                await auth.admin_delete_user(str(user_id))  # do not leave a half-made account behind
                raise
            return {"status": "created", "email": email, "user_id": str(user_id), "temp_password": password}

        # ---- the auth user already exists
        user_id = existing["id"]
        profile = await conn.fetchrow("select role from public.profiles where id = $1", user_id)
        if profile is not None and profile["role"] != "admin":
            raise ValueError(f"{email} already exists as a '{profile['role']}' - I will not silently turn it into an admin")

        if profile is None:  # login exists (made by hand in the dashboard) but has no profile yet
            await conn.execute(
                "insert into public.profiles (id, full_name, email, role, school_id, must_change_password) "
                "values ($1, $2, $3, 'admin', null, true)",
                user_id, name, email,
            )

        if reset:
            password = make_temp_password()
            await auth.admin_update_user(str(user_id), password=password)
            await conn.execute(
                "update public.profiles set must_change_password = true, is_active = true where id = $1", user_id
            )
            await conn.execute(
                "update public.app_sessions set revoked_at = now(), revoked_reason = 'admin_reset' "
                "where user_id = $1 and revoked_at is null", user_id,
            )
            await conn.execute("delete from auth.sessions where user_id = $1", user_id)
            return {"status": "reset", "email": email, "user_id": str(user_id), "temp_password": password}

        return {"status": "profile_added" if profile is None else "exists", "email": email, "user_id": str(user_id)}
    finally:
        await auth.aclose()
        await conn.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Create / reset the first admin account")
    parser.add_argument("--reset", action="store_true", help="give the existing admin a new temporary password")
    args = parser.parse_args()

    try:
        settings = get_settings()
    except (ValidationError, ValueError) as exc:
        print(explain_config_error(exc))
        return 1
    if not settings.seed_admin_email:
        print("\n.env me SEED_ADMIN_EMAIL khali hai. Wahan admin ka email likho, save karo, phir dobara chalao.\n")
        return 1

    try:
        result = await ensure_admin(settings, settings.seed_admin_email, settings.seed_admin_name, reset=args.reset)
    except (AuthError, ValueError) as exc:
        print(f"\nAdmin account nahi ban paya: {getattr(exc, 'message', exc)}\n")
        return 1

    print()
    if result["status"] in ("created", "reset"):
        verb = "BAN GAYA" if result["status"] == "created" else "NAYA TEMPORARY PASSWORD SET HO GAYA"
        print(f"=== ADMIN ACCOUNT {verb} ===")
        print(f"  Email              : {result['email']}")
        print(f"  Temporary password : {result['temp_password']}")
        print("  (Pehli baar login par naya password set karna padega.)")
    elif result["status"] == "profile_added":
        print(f"Admin profile add ho gayi for {result['email']} (purana password hi chalega, pehle login par badalna padega).")
    else:
        print(f"Admin account '{result['email']}' pehle se maujood hai. Naya password chahiye to:  python -m scripts.seed_admin --reset")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
