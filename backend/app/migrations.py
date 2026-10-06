"""Database migration helpers (used by scripts/migrate.py and by the server's start-up check)."""
from __future__ import annotations

from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
LOCK_KEY = 727274  # arbitrary number: stops two migration runs from overlapping

BOOTSTRAP_SQL = """
create table if not exists public.schema_migrations (
  version     text primary key,
  applied_at  timestamptz not null default now()
);
alter table public.schema_migrations enable row level security;
revoke all on public.schema_migrations from anon, authenticated;
"""


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def applied_versions(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch("select version from public.schema_migrations")
    return {r["version"] for r in rows}


async def pending_versions(conn: asyncpg.Connection) -> list[str]:
    exists = await conn.fetchval("select to_regclass('public.schema_migrations') is not null")
    done = await applied_versions(conn) if exists else set()
    return [p.stem for p in migration_files() if p.stem not in done]


async def apply_pending(conn: asyncpg.Connection, *, verbose: bool = True) -> list[str]:
    """Apply every migration file that has not been applied yet. Returns the versions applied now."""
    await conn.execute(BOOTSTRAP_SQL)
    done = await applied_versions(conn)
    newly: list[str] = []
    for path in migration_files():
        version = path.stem
        if version in done:
            continue
        sql = path.read_text(encoding="utf-8")
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock($1)", LOCK_KEY)
            if version in await applied_versions(conn):  # someone else applied it meanwhile
                continue
            await conn.execute(sql)
            await conn.execute("insert into public.schema_migrations (version) values ($1)", version)
        newly.append(version)
        if verbose:
            print(f"  + database update laga diya: {version}")
    if verbose and not newly:
        print("  database pehle se up-to-date hai")
    return newly
