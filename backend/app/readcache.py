"""A tiny in-process read cache for the data every timetable screen needs (Phase 8 speed work).

Why it is safe: EVERY successful change request (POST / PUT / PATCH / DELETE, see main.py) and every background job that
changes something (the offer / preference sweeps) bumps one version number - a cached answer is only ever used while
nothing at all has changed since it was read, and never for longer than READ_CACHE_SECONDS (for changes made outside this
server, e.g. a script). Many people asking for the same thing at the same moment share ONE database read.

One server process = one cache (the app runs as one process; see PRODUCTION_CHECKLIST.md).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, TypeVar

from .config import get_settings

T = TypeVar("T")

_version = 0
_store: dict[Any, tuple[float, int, Any]] = {}
_inflight: dict[Any, asyncio.Future] = {}
MAX_ENTRIES = 400


def bump() -> None:
    """Something changed: every cached answer is stale from now on."""
    global _version
    _version += 1


def clear() -> None:
    _store.clear()
    bump()


def version() -> int:
    return _version


async def cached(key: Any, load: Callable[[], Awaitable[T]]) -> T:
    ttl = get_settings().read_cache_seconds
    if ttl <= 0:
        return await load()
    now = time.monotonic()
    hit = _store.get(key)
    if hit is not None and hit[1] == _version and now - hit[0] < ttl:
        return hit[2]
    started_at = _version
    flight = (key, started_at)       # only share a read that began AFTER the latest change
    waiting = _inflight.get(flight)
    if waiting is not None and not waiting.done():
        return await asyncio.shield(waiting)
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _inflight[flight] = fut
    try:
        value = await load()
    except BaseException as exc:
        if not fut.done():
            fut.set_exception(exc)
            fut.exception()          # mark as retrieved (nobody else may be waiting)
        raise
    finally:
        _inflight.pop(flight, None)
    if not fut.done():
        fut.set_result(value)
    if started_at == _version:       # nothing changed while we were reading: keep it
        if len(_store) >= MAX_ENTRIES:
            for k in sorted(_store, key=lambda k: _store[k][0])[: MAX_ENTRIES // 4]:
                _store.pop(k, None)
        _store[key] = (time.monotonic(), started_at, value)
    return value
