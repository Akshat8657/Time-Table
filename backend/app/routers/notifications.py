"""Notifications (plan Section 15): the in-app bell (list, unread count, mark read, live stream) and browser Web Push.

  GET  /api/notifications                 my notifications (newest first) + the unread count
  GET  /api/notifications/unread-count    just the number (the bell asks this - it does NOT keep an idle login alive)
  POST /api/notifications/read            mark some / all as read
  GET  /api/notifications/stream          live feed (server-sent events): a new notification reaches the open bell at once
  GET  /api/push/config                   is Web Push on, and the public key the browser needs
  POST /api/push/subscribe, /unsubscribe  turn browser alerts on / off for THIS browser
  POST /api/push/test                     send myself a test notification (checks the whole chain)

Everybody only ever sees their own notifications - the owner is taken from the login, never from the request.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import db
from ..config import get_settings
from ..errors import ApiError
from ..scope import as_uuid
from ..security.deps import CurrentUser, require_roles
from ..services import notify

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
push_router = APIRouter(prefix="/api/push", tags=["notifications"])
ALL = ("admin", "hod", "teacher")
STREAM_MAX_SECONDS = 600  # the stream ends now and then; the browser reconnects with a fresh login check


def note_out(r: Any) -> dict[str, Any]:
    data = r["data"]
    if isinstance(data, str):  # asyncpg returns jsonb as text
        data = json.loads(data)
    return {
        "id": str(r["id"]), "kind": r["kind"], "title": r["title"], "body": r["body"], "link": r["link"], "data": data,
        "is_read": r["is_read"], "read_at": r["read_at"].isoformat() if r["read_at"] else None,
        "created_at": r["created_at"].isoformat(),
    }


@router.get("")
async def list_notifications(
    unread_only: bool = False,
    limit: int = 30,
    before: datetime | None = None,
    user: CurrentUser = Depends(require_roles(*ALL, touch=False)),
) -> dict:
    limit = max(1, min(limit, 100))
    args: list[Any] = [uuid.UUID(user.id)]
    where = "user_id = $1"
    if unread_only:
        where += " and not is_read"
    if before is not None:
        args.append(before)
        where += f" and created_at < ${len(args)}"
    rows = await db.fetch(
        f"select id, kind, title, body, link, data, is_read, read_at, created_at from public.notifications where {where} "
        f"order by created_at desc, id limit {limit + 1}",
        *args,
    )
    unread = await db.fetchval("select count(*) from public.notifications where user_id = $1 and not is_read", uuid.UUID(user.id))
    return {"notifications": [note_out(r) for r in rows[:limit]], "unread": int(unread), "has_more": len(rows) > limit}


@router.get("/unread-count")
async def unread_count(user: CurrentUser = Depends(require_roles(*ALL, touch=False))) -> dict:
    n = await db.fetchval("select count(*) from public.notifications where user_id = $1 and not is_read", uuid.UUID(user.id))
    return {"unread": int(n)}


class ReadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(default_factory=list, max_length=200)
    all: bool = False


@router.post("/read")
async def mark_read(body: ReadIn, user: CurrentUser = Depends(require_roles(*ALL))) -> dict:
    me = uuid.UUID(user.id)
    if body.all:
        await db.execute("update public.notifications set is_read = true, read_at = now() where user_id = $1 and not is_read", me)
    elif body.ids:
        ids = [as_uuid(i, "notification") for i in body.ids]
        await db.execute(
            "update public.notifications set is_read = true, read_at = now() where user_id = $1 and id = any($2::uuid[]) and not is_read",
            me, ids,
        )
    n = await db.fetchval("select count(*) from public.notifications where user_id = $1 and not is_read", me)
    return {"unread": int(n)}


@router.get("/stream")
async def stream(request: Request, user: CurrentUser = Depends(require_roles(*ALL, touch=False))) -> StreamingResponse:
    queue = notify.hub.subscribe(user.id)

    async def events():
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STREAM_MAX_SECONDS
        try:
            yield "retry: 4000\n: connected\n\n"
            while loop.time() < deadline:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": ping\n\n"
                    continue
                yield f"event: notification\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
            yield "event: bye\ndata: {}\n\n"
        finally:
            notify.hub.unsubscribe(user.id, queue)

    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ---------------------------------------------------------------------------------------------------- Web Push
class Keys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=10, max_length=100)


class SubscribeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")  # browsers add expirationTime etc.
    endpoint: str = Field(min_length=10, max_length=1000)
    keys: Keys


class UnsubscribeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    endpoint: str = Field(min_length=10, max_length=1000)


@push_router.get("/config")
async def push_config(user: CurrentUser = Depends(require_roles(*ALL, touch=False))) -> dict:
    st = get_settings()
    n = await db.fetchval("select count(*) from public.push_subscriptions where user_id = $1", uuid.UUID(user.id))
    return {"enabled": st.push_enabled, "public_key": st.vapid_public_key.strip() if st.push_enabled else None, "devices": int(n)}


@push_router.post("/subscribe")
async def subscribe(body: SubscribeIn, request: Request, user: CurrentUser = Depends(require_roles(*ALL))) -> dict:
    if not get_settings().push_enabled:
        raise ApiError(409, "PUSH_OFF", "Browser alerts are not set up on this server (no VAPID keys in .env).")
    if not notify.endpoint_allowed(body.endpoint):
        raise ApiError(422, "BAD_ENDPOINT", "That browser address is not a known push service.", extra={"fields": {"endpoint": "Not a known push service."}})
    agent = (request.headers.get("user-agent") or "")[:300] or None
    me = uuid.UUID(user.id)
    # one browser = one endpoint; if someone else signed in on the same browser, the endpoint now belongs to the newest person
    await db.execute(
        """
        insert into public.push_subscriptions (user_id, endpoint, p256dh, auth, user_agent)
        values ($1, $2, $3, $4, $5)
        on conflict (endpoint) do update
           set user_id = excluded.user_id, p256dh = excluded.p256dh, auth = excluded.auth,
               user_agent = excluded.user_agent, failures = 0
        """,
        me, body.endpoint, body.keys.p256dh, body.keys.auth, agent,
    )
    n = await db.fetchval("select count(*) from public.push_subscriptions where user_id = $1", me)
    return {"subscribed": True, "devices": int(n)}


@push_router.post("/unsubscribe")
async def unsubscribe(body: UnsubscribeIn, user: CurrentUser = Depends(require_roles(*ALL))) -> dict:
    me = uuid.UUID(user.id)
    await db.execute("delete from public.push_subscriptions where user_id = $1 and endpoint = $2", me, body.endpoint)
    n = await db.fetchval("select count(*) from public.push_subscriptions where user_id = $1", me)
    return {"subscribed": bool(n), "devices": int(n)}


@push_router.post("/test")
async def push_test(user: CurrentUser = Depends(require_roles(*ALL))) -> dict:
    me = uuid.UUID(user.id)
    sent = await notify.send([notify.Notice(
        me, "test", "Test notification", "If you can read this, your alerts are working.", "/my/notifications",
        school_id=uuid.UUID(user.school_id) if user.school_id else None,
    )])
    return {"sent": len(sent)}
