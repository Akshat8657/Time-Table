"""Apply pending SQL migrations to the Supabase database.

Usage (from the backend folder):   python -m scripts.migrate
Safe to run any number of times - each migration is applied only once.
"""
from __future__ import annotations

import asyncio
import socket
import sys

import asyncpg
from pydantic import ValidationError

from app.config import explain_config_error, get_settings
from app.migrations import apply_pending


async def main() -> int:
    try:
        params = get_settings().db_params
    except (ValidationError, ValueError) as exc:
        print(explain_config_error(exc))
        return 1
    try:
        conn = await asyncpg.connect(
            host=params["host"], port=params["port"], user=params["user"], password=params["password"],
            database=params["database"], ssl="require", statement_cache_size=0, timeout=25,
        )
    except (socket.gaierror, OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
        print("\nDatabase se connect nahi ho paya.")
        print(f"  Technical detail: {type(exc).__name__}")
        print("  Check karo: .env me SUPABASE_DB_URL 'Session pooler' wali string hai aur password sahi hai.")
        print("  (Direct connection wali string aksar aapke internet par nahi chalti.)\n")
        return 1
    try:
        await apply_pending(conn)
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
