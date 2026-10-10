"""Audit log: who did what, when - written in the SAME database transaction as the change itself."""
from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

import asyncpg

from .security.deps import CurrentUser

# "ui" for a normal click. The AI assistant wraps the existing services in `via_ai()` so that EVERY audit line they write
# (the absence, the notification...) is marked via='ai' without those services knowing anything about the assistant.
_via: ContextVar[str] = ContextVar("audit_via", default="ui")


@contextmanager
def via_ai() -> Iterator[None]:
    token = _via.set("ai")
    try:
        yield
    finally:
        _via.reset(token)


async def record(
    conn: asyncpg.Connection,
    actor: CurrentUser,
    action: str,
    entity: str,
    entity_id: Any,
    summary: str,
    *,
    school_id: Any = None,
    details: dict[str, Any] | None = None,
    via: str | None = None,
) -> None:
    via = via or _via.get()
    await conn.execute(
        """
        insert into public.audit_log (actor_id, actor_email, actor_role, school_id, action, entity, entity_id, summary, details, via)
        values ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
        """,
        _uuid(actor.id), actor.email, actor.role, _uuid(school_id), action, entity,
        None if entity_id is None else str(entity_id), summary[:500],
        None if details is None else json.dumps(details, default=str), via,
    )


def _uuid(value: Any):
    import uuid

    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
