"""Database access: one asyncpg pool to the Supabase Postgres (through the pooler)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg

from .config import get_settings

log = logging.getLogger("tt.db")

_pool: asyncpg.Pool | None = None

# Errors that mean "the pooled connection went stale" - safe to retry READS once.
_STALE = (
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    asyncpg.exceptions.AdminShutdownError,
    asyncpg.exceptions.CrashShutdownError,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
)


async def connect_one() -> asyncpg.Connection:
    """A single standalone connection (used by scripts and tests)."""
    p = get_settings().db_params
    return await asyncpg.connect(
        host=p["host"], port=p["port"], user=p["user"], password=p["password"], database=p["database"],
        ssl="require", statement_cache_size=0, timeout=20,
        server_settings={"application_name": "timetablepro-script"},
    )


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    p = get_settings().db_params
    _pool = await asyncpg.create_pool(
        host=p["host"], port=p["port"], user=p["user"], password=p["password"], database=p["database"],
        ssl="require",
        min_size=1,
        max_size=8,
        timeout=20,
        command_timeout=30,
        max_inactive_connection_lifetime=240,
        statement_cache_size=0,  # works with every Supabase pooler mode
        server_settings={"application_name": "timetablepro-api"},
    )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not started")
    return _pool


async def _read(method: str, sql: str, *args: Any) -> Any:
    for attempt in (1, 2):
        try:
            async with pool().acquire() as conn:
                return await getattr(conn, method)(sql, *args)
        except _STALE as exc:
            if attempt == 2:
                raise
            log.warning("Stale DB connection (%s) - retrying once", type(exc).__name__)


async def fetchrow(sql: str, *args: Any) -> asyncpg.Record | None:
    return await _read("fetchrow", sql, *args)


async def fetch(sql: str, *args: Any) -> list[asyncpg.Record]:
    return await _read("fetch", sql, *args)


async def fetchval(sql: str, *args: Any) -> Any:
    return await _read("fetchval", sql, *args)


async def execute(sql: str, *args: Any, retry: bool = False) -> str:
    """Run a write. Only pass retry=True for statements that are safe to repeat."""
    for attempt in (1, 2):
        try:
            async with pool().acquire() as conn:
                return await conn.execute(sql, *args)
        except _STALE as exc:
            if not retry or attempt == 2:
                raise
            log.warning("Stale DB connection (%s) - retrying once", type(exc).__name__)
    raise RuntimeError("unreachable")


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    async with pool().acquire() as conn:
        async with conn.transaction():
            yield conn
