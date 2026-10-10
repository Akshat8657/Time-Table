"""Phase 8 speed work: the read cache must never serve stale data after a change, and many people asking for the same thing
at once must share ONE database read."""
from __future__ import annotations

import asyncio

import pytest

from app import readcache
from app.config import get_settings


@pytest.fixture(autouse=True)
def cache_on(monkeypatch):
    real = get_settings()
    monkeypatch.setattr(readcache, "get_settings", lambda: real.model_copy(update={"read_cache_seconds": 30.0}))
    readcache.clear()
    yield
    readcache.clear()


def test_a_second_read_comes_from_memory_until_something_changes():
    calls = []

    async def load():
        calls.append(1)
        return {"n": len(calls)}

    async def go():
        a = await readcache.cached(("k",), load)
        b = await readcache.cached(("k",), load)
        readcache.bump()                         # any change through the app
        c = await readcache.cached(("k",), load)
        return a, b, c

    a, b, c = asyncio.run(go())
    assert a == b == {"n": 1} and c == {"n": 2} and len(calls) == 2


def test_many_people_at_once_share_one_read():
    calls = []

    async def load():
        calls.append(1)
        await asyncio.sleep(0.05)
        return "data"

    async def go():
        return await asyncio.gather(*(readcache.cached(("same",), load) for _ in range(50)))

    assert asyncio.run(go()) == ["data"] * 50 and len(calls) == 1


def test_a_change_during_a_slow_read_is_not_hidden():
    """Read starts -> a change lands -> the (now old) result must not be kept for the next person."""
    calls = []

    async def load():
        calls.append(1)
        await asyncio.sleep(0.05)
        return len(calls)

    async def go():
        first = asyncio.ensure_future(readcache.cached(("x",), load))
        await asyncio.sleep(0.01)
        readcache.bump()
        await first
        return await readcache.cached(("x",), load)

    assert asyncio.run(go()) == 2


def test_a_reader_after_a_change_never_joins_an_older_read():
    """Slow read starts -> a change lands (the user's save returns) -> the user's next read must NOT share the old read."""
    calls = []

    async def load():
        calls.append(1)
        n = len(calls)
        await asyncio.sleep(0.05)
        return n

    async def go():
        old = asyncio.ensure_future(readcache.cached(("y",), load))
        await asyncio.sleep(0.01)
        readcache.bump()
        fresh = await readcache.cached(("y",), load)
        return await old, fresh

    assert asyncio.run(go()) == (1, 2)


def test_a_failed_read_is_not_cached_and_waiting_callers_see_the_error():
    async def bad():
        await asyncio.sleep(0.02)
        raise RuntimeError("database down")

    async def go():
        results = await asyncio.gather(*(readcache.cached(("e",), bad) for _ in range(3)), return_exceptions=True)
        return results

    assert all(isinstance(r, RuntimeError) for r in asyncio.run(go()))

    async def good():
        return "fine"
    assert asyncio.run(readcache.cached(("e",), good)) == "fine"


def test_switched_off_means_always_fresh(monkeypatch):
    real = get_settings()
    monkeypatch.setattr(readcache, "get_settings", lambda: real.model_copy(update={"read_cache_seconds": 0.0}))
    calls = []

    async def load():
        calls.append(1)
        return len(calls)

    async def go():
        return [await readcache.cached(("off",), load) for _ in range(3)]

    assert asyncio.run(go()) == [1, 2, 3]


def test_a_generation_that_ends_in_the_background_clears_the_cache(monkeypatch):
    """The generator saves its drafts from a background task (not a change request): when it ends, cached screens re-read."""
    import threading
    import uuid
    from types import SimpleNamespace

    from app.routers import generation as g

    def no_timetable(*a, **k):
        raise RuntimeError("no timetable")

    async def no_db(*a, **k):
        return "UPDATE 0"

    monkeypatch.setattr(g.gen, "solve", no_timetable)
    monkeypatch.setattr(g.db, "execute", no_db)
    run_id = uuid.uuid4()
    g.JOBS[str(run_id)] = SimpleNamespace(progress=1, message="", cancel=threading.Event(), school_id="x", task=None)
    v0 = readcache.version()
    asyncio.run(g._run_job(run_id, None, None, "t", None, {}, 5, None, None))
    assert readcache.version() > v0 and str(run_id) not in g.JOBS


def test_every_data_changing_request_clears_the_cache():
    """main.py bumps the version after a successful POST / PUT / PATCH / DELETE (sign-in and bell calls excepted)."""
    import httpx

    from app.main import app

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost", headers={"Origin": "http://localhost:5173"}) as c:
            v0 = readcache.version()
            await c.get("/api/health")
            v1 = readcache.version()
            r = await c.post("/api/subjects", json={})            # refused (no login) -> nothing changed
            v2 = readcache.version()
            return v0, v1, v2, r.status_code

    v0, v1, v2, status = asyncio.run(go())
    assert v0 == v1 == v2 and status == 401
    from app import main as m
    assert m._NO_DATA_CHANGE[0] == "/api/auth/" and "/api/notifications/" in m._NO_DATA_CHANGE
