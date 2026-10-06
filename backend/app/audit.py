"""Audit log: who did what, when - written in the SAME database transaction as the change itself."""
from __future__ import annotations

import json
from typing import Any

import asyncpg

from .security.deps import CurrentUser


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
    via: str = "ui",
) -> None:
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
