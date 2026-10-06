"""Database access: one asyncpg pool to the Supabase Postgres (through the pooler)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg

from .config import get_settings
from .errors import ApiError

log = logging.getLogger("tt.db")

_pool: asyncpg.Pool | None = None

class _Connection(asyncpg.Connection):
    """Pooled connection WITHOUT the automatic 'reset' when it is handed back.

    asyncpg normally sends a RESET command every time a connection returns to the pool - one extra trip to the
    database (~150 ms from here) after EVERY request. We never use session-level state (advisory locks, LISTEN,
    SET ...), and transactions are always closed by transaction(), so the reset has nothing to clean up."""

    async def reset(self, *, timeout: float | None = None) -> None:
        return None


POOL_MIN = 4              # kept warm: opening a new connection to Supabase takes about a second
POOL_MAX = 8
ACQUIRE_TIMEOUT = 25.0    # never wait for a free connection forever

# Errors that mean "the pooled connection went stale" - safe to retry READS once.
_STALE = (
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    asyncpg.InvalidCachedStatementError,  # a table changed (migration) while a statement was remembered -> just run it again
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
        min_size=POOL_MIN,
        max_size=POOL_MAX,
        timeout=20,
        command_timeout=30,
        max_inactive_connection_lifetime=300,
        # Remembering prepared statements halves the time of every query (1 trip instead of 2). It only works with the
        # SESSION pooler (port 5432); the transaction pooler (port 6543) must not remember statements.
        statement_cache_size=100 if p["port"] == 5432 else 0,
        connection_class=_Connection,
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


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    """A pooled connection - but never wait for one forever (a leaked connection must not freeze the whole app)."""
    try:
        conn = await pool().acquire(timeout=ACQUIRE_TIMEOUT)
    except asyncio.TimeoutError:
        log.error("No free database connection after %.0f s", ACQUIRE_TIMEOUT)
        raise ApiError(503, "DB_BUSY", "The system is busy right now. Please try again in a moment.") from None
    try:
        yield conn
    finally:
        await pool().release(conn)


async def keep_warm() -> None:
    """Touches every kept-alive connection so the pooler never drops them (and the first click is not slow)."""
    async def ping() -> None:
        await fetchval("select 1")

    await asyncio.gather(*[ping() for _ in range(POOL_MIN)], return_exceptions=True)


async def _read(method: str, sql: str, *args: Any) -> Any:
    for attempt in (1, 2):
        try:
            async with acquire() as conn:
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
            async with acquire() as conn:
                return await conn.execute(sql, *args)
        except _STALE as exc:
            if not retry or attempt == 2:
                raise
            log.warning("Stale DB connection (%s) - retrying once", type(exc).__name__)
    raise RuntimeError("unreachable")


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    async with acquire() as conn:
        async with conn.transaction():
            yield conn
