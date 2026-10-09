"""The notification service (plan Section 15) - ONE reusable sender for the whole app (Phase 5 uses it heavily).

    notices = [Notice(user_id, "timetable_published", "Your timetable is published", "...", "/my/timetable", dedupe_key="publish:<event>")]
    async with db.transaction() as conn:
        ... the change itself ...
        sent = await notify.create(conn, notices)      # written in the SAME transaction as the change
    await notify.deliver(sent)                         # after the commit: live bell (SSE) + browser Web Push

    or simply   await notify.send(notices)             # its own transaction + deliver

Rules that keep it from becoming spam:
  * `dedupe_key`: the same (person, key) pair is stored - and delivered - only once, even if the code runs twice
  * a person who is deactivated gets nothing
  * Web Push is best effort: a dead browser subscription is removed, a failing push never breaks the change itself
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

import asyncpg

from .. import db
from ..config import get_settings

log = logging.getLogger("tt.notify")

# The browser vendors' push services. Only these may be contacted (a user cannot make the server call any web address).
PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com", "push.apple.com",
    "notify.windows.com", "push.microsoft.com",
)
ALLOW_LOCAL_ENDPOINTS = False  # switched on by the automated tests only (they listen on 127.0.0.1)
MAX_FAILURES = 5


@dataclass
class Notice:
    user_id: uuid.UUID
    kind: str                                  # 'timetable_published' | 'timetable_changed' | 'extra_lecture' | ...
    title: str
    body: str | None = None
    link: str | None = None                    # an app path that works for every role, e.g. '/my/timetable'
    data: dict[str, Any] = field(default_factory=dict)
    school_id: uuid.UUID | None = None
    dedupe_key: str | None = None              # same person + same key = delivered once


@dataclass
class Sent:
    id: uuid.UUID
    user_id: uuid.UUID
    kind: str
    title: str
    body: str | None
    link: str | None
    data: dict[str, Any]
    created_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id), "kind": self.kind, "title": self.title, "body": self.body, "link": self.link,
            "data": self.data, "is_read": False, "read_at": None, "created_at": self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------------------------------- live stream hub
class Hub:
    """Who is listening right now (open bell streams). Lives inside the one server process."""

    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, user_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._queues.setdefault(user_id, set()).add(q)
        return q

    def unsubscribe(self, user_id: str, q: asyncio.Queue) -> None:
        group = self._queues.get(user_id)
        if group:
            group.discard(q)
            if not group:
                self._queues.pop(user_id, None)

    def publish(self, user_id: str, payload: dict[str, Any]) -> int:
        n = 0
        for q in list(self._queues.get(user_id, ())):
            try:
                q.put_nowait(payload)
                n += 1
            except asyncio.QueueFull:
                pass  # a stuck listener: it reloads the list when it reconnects
        return n

    def listeners(self, user_id: str) -> int:
        return len(self._queues.get(user_id, ()))


hub = Hub()
_background: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------------------------------- writing
_INSERT = """
insert into public.notifications (user_id, school_id, kind, title, body, link, data, dedupe_key)
select $1, $2, $3, $4, $5, $6, $7::jsonb, $8
 where exists (select 1 from public.profiles p where p.id = $1 and p.is_active)
on conflict (user_id, dedupe_key) where dedupe_key is not null do nothing
returning id, created_at
"""


def _clip(value: str | None, n: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value if len(value) <= n else value[: n - 1].rstrip() + "…"


async def create(conn: asyncpg.Connection, notices: list[Notice]) -> list[Sent]:
    """Stores the notifications (inside the caller's transaction). Duplicates and deactivated people are skipped."""
    sent: list[Sent] = []
    for n in notices:
        title, body, link = _clip(n.title, 160) or "Notification", _clip(n.body, 800), _clip(n.link, 200)
        row = await conn.fetchrow(
            _INSERT, n.user_id, n.school_id, n.kind[:40], title, body, link, json.dumps(n.data, default=str),
            _clip(n.dedupe_key, 120),
        )
        if row is not None:
            sent.append(Sent(row["id"], n.user_id, n.kind, title, body, link, n.data, row["created_at"]))
    return sent


async def deliver(sent: list[Sent]) -> None:
    """After the commit: tell open bells at once and start the browser pushes (never raises, never waits for them)."""
    for s in sent:
        hub.publish(str(s.user_id), s.as_dict())
    if sent and get_settings().push_enabled:
        task = asyncio.create_task(_push_all(sent))
        _background.add(task)
        task.add_done_callback(_background.discard)


async def send(notices: list[Notice]) -> list[Sent]:
    async with db.transaction() as conn:
        sent = await create(conn, notices)
    await deliver(sent)
    return sent


# ---------------------------------------------------------------------------------------------------- Web Push
def endpoint_allowed(endpoint: str) -> bool:
    try:
        u = urlparse(endpoint)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if ALLOW_LOCAL_ENDPOINTS and u.scheme in ("http", "https") and host in ("127.0.0.1", "localhost"):
        return True
    if u.scheme != "https" or not host:
        return False
    return any(host == s or host.endswith("." + s) for s in PUSH_HOST_SUFFIXES)


@lru_cache(maxsize=1)
def _vapid():
    from py_vapid import Vapid02

    return Vapid02.from_raw(get_settings().vapid_private_key.strip().encode())


def _push_payload(s: Sent) -> str:
    return json.dumps(
        {"id": str(s.id), "title": s.title, "body": s.body or "", "url": s.link or "/", "tag": s.kind},
        separators=(",", ":"), ensure_ascii=False,
    )


def _push_one(endpoint: str, p256dh: str, auth: str, payload: str) -> tuple[str, int | None]:
    """Runs in a worker thread. ('ok'|'gone'|'fail', http status)"""
    from pywebpush import WebPushException, webpush

    st = get_settings()
    try:
        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=payload,
            vapid_private_key=_vapid(),
            vapid_claims={"sub": st.vapid_subject},
            ttl=86400,
            timeout=10,
        )
        return "ok", None
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return ("gone" if status in (404, 410) else "fail"), status
    except Exception:  # noqa: BLE001 - network trouble etc.
        return "fail", None


async def push_to_user(user_id: uuid.UUID, s: Sent) -> dict[str, int]:
    """Pushes one notification to every browser the person turned alerts on in. Returns counts: ok / gone / fail."""
    counts = {"ok": 0, "gone": 0, "fail": 0}
    subs = await db.fetch("select id, endpoint, p256dh, auth from public.push_subscriptions where user_id = $1", user_id)
    payload = _push_payload(s)
    for sub in subs:
        if not endpoint_allowed(sub["endpoint"]):
            await db.execute("delete from public.push_subscriptions where id = $1", sub["id"])
            continue
        outcome, status = await asyncio.to_thread(_push_one, sub["endpoint"], sub["p256dh"], sub["auth"], payload)
        counts[outcome] += 1
        try:
            if outcome == "ok":
                await db.execute("update public.push_subscriptions set last_ok_at = now(), failures = 0 where id = $1", sub["id"])
            elif outcome == "gone":
                await db.execute("delete from public.push_subscriptions where id = $1", sub["id"])
            else:
                await db.execute(
                    "update public.push_subscriptions set failures = failures + 1 where id = $1", sub["id"]
                )
                await db.execute("delete from public.push_subscriptions where id = $1 and failures >= $2", sub["id"], MAX_FAILURES)
                log.info("Web Push to one browser failed (status %s)", status)
        except Exception:  # noqa: BLE001
            log.exception("Could not update a push subscription")
    return counts


async def _push_all(sent: list[Sent]) -> None:
    for s in sent:
        try:
            await push_to_user(s.user_id, s)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Web Push failed")


async def drain() -> None:
    """Waits for the pushes that are still on their way (used by tests and at shut-down)."""
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)
