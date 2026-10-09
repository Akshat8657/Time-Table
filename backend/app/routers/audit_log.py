"""The Audit Log screen (plan Sections 5.1, 21): who did what, when - ADMIN ONLY.

  GET /api/audit          the log, newest first: filter by person, action, entity, school, source (ui / ai / system), free text and date range; paginated
  GET /api/audit/facets   what the filters can offer (actions, entities, people, schools) + how many entries there are

The table has been written by every phase since Phase 1 (audit.record, inside the same transaction as the change). Browsers cannot
read it directly either (RLS: admin only); this is the one door, and it checks the role.
Fast on purpose: every filter has an index (at, actor, action, entity, school) and the list never loads more than one page.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from .. import db
from ..errors import ApiError
from ..security.deps import CurrentUser, require_roles
from ..services.views import IST

router = APIRouter(prefix="/api/audit", tags=["audit"])
MAX_PAGE_SIZE = 100
MAX_OFFSET = 50_000


def _like(text: str) -> str:
    """A user's search text as a safe ILIKE pattern (the % and _ they type are literal)."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _day(raw: str | None, field: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ApiError(422, "VALIDATION_ERROR", "Use a date like 2026-10-12.", extra={"fields": {field: "Format YYYY-MM-DD."}}) from None


def _uuid(raw: str | None, field: str) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ApiError(422, "VALIDATION_ERROR", "That choice is not valid.", extra={"fields": {field: "Not valid."}}) from None


def _where(
    actor_id: uuid.UUID | None, action: str | None, entity: str | None, via: str | None, school_id: uuid.UUID | None,
    q: str | None, d_from: date | None, d_to: date | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    args: list[Any] = []

    def add(sql: str, value: Any) -> None:
        args.append(value)
        clauses.append(sql.replace("?", f"${len(args)}"))

    if actor_id:
        add("a.actor_id = ?", actor_id)
    if action:
        add("a.action = ?", action)
    if entity:
        add("a.entity = ?", entity)
    if via:
        add("a.via = ?", via)
    if school_id:
        add("a.school_id = ?", school_id)
    if q and q.strip():   # one pattern, used for both columns (LIKE's default escape character is the backslash)
        args.append(_like(q.strip()[:80]))
        clauses.append(f"(a.summary ilike ${len(args)} or a.actor_email ilike ${len(args)})")
    if d_from:
        add("a.at >= ?", datetime(d_from.year, d_from.month, d_from.day, tzinfo=IST))
    if d_to:
        nxt = d_to + timedelta(days=1)
        add("a.at < ?", datetime(nxt.year, nxt.month, nxt.day, tzinfo=IST))
    return (" where " + " and ".join(clauses)) if clauses else "", args


def _details(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


@router.get("")
async def list_audit(
    actor_id: str | None = None, action: str | None = None, entity: str | None = None, via: str | None = None, school_id: str | None = None,
    q: str | None = None, date_from: str | None = None, date_to: str | None = None, page: int = 1, page_size: int = 50,
    user: CurrentUser = Depends(require_roles("admin")),
) -> dict:
    if via and via not in ("ui", "ai", "system"):
        raise ApiError(422, "VALIDATION_ERROR", "Source must be ui, ai or system.", extra={"fields": {"via": "Not valid."}})
    d_from, d_to = _day(date_from, "date_from"), _day(date_to, "date_to")
    if d_from and d_to and d_to < d_from:
        raise ApiError(422, "VALIDATION_ERROR", "The 'to' date is before the 'from' date.", extra={"fields": {"date_to": "Must not be before the start."}})
    size = max(1, min(page_size, MAX_PAGE_SIZE))
    page = max(1, page)
    offset = (page - 1) * size
    if offset > MAX_OFFSET:
        raise ApiError(422, "TOO_DEEP", "That page is too far down. Narrow the filters (dates, person, action) to find older entries.")
    where, args = _where(_uuid(actor_id, "actor_id"), (action or "").strip()[:60] or None, (entity or "").strip()[:60] or None, via,
                         _uuid(school_id, "school_id"), q, d_from, d_to)
    page_sql = (
        "select a.id, a.at, a.actor_id, a.actor_email, a.actor_role, a.school_id, a.action, a.entity, a.entity_id, a.summary, a.details, a.via, "
        "p.full_name as actor_name, s.name as school_name "
        "from public.audit_log a left join public.profiles p on p.id = a.actor_id left join public.schools s on s.id = a.school_id"
        f"{where} order by a.at desc, a.id desc limit ${len(args) + 1} offset ${len(args) + 2}"
    )
    rows, total = await asyncio.gather(
        db.fetch(page_sql, *args, size, offset),
        db.fetchval(f"select count(*) from public.audit_log a{where}", *args),
    )
    return {
        "entries": [
            {
                "id": r["id"], "at": r["at"].astimezone(IST).isoformat(), "action": r["action"], "entity": r["entity"], "entity_id": r["entity_id"],
                "summary": r["summary"], "details": _details(r["details"]), "via": r["via"],
                "actor": {"id": str(r["actor_id"]) if r["actor_id"] else None, "name": r["actor_name"] or r["actor_email"] or "System", "email": r["actor_email"], "role": r["actor_role"]},
                "school": {"id": str(r["school_id"]), "name": r["school_name"]} if r["school_id"] else None,
            }
            for r in rows
        ],
        "total": total, "page": page, "page_size": size, "pages": max(1, -(-total // size)),
    }


@router.get("/facets")
async def facets(user: CurrentUser = Depends(require_roles("admin"))) -> dict:
    actions, entities, people, schools, span = await asyncio.gather(
        db.fetch("select action, count(*) as n from public.audit_log group by action order by action"),
        db.fetch("select entity, count(*) as n from public.audit_log group by entity order by entity"),
        db.fetch(
            "select a.actor_id, coalesce(max(p.full_name), max(a.actor_email), 'System') as name, max(a.actor_email) as email, max(a.actor_role) as role, count(*) as n "
            "from public.audit_log a left join public.profiles p on p.id = a.actor_id where a.actor_id is not null "
            "group by a.actor_id order by n desc limit 300"
        ),
        db.fetch("select id, name from public.schools order by name"),
        db.fetchrow("select count(*) as n, min(at) as first, max(at) as last from public.audit_log"),
    )
    return {
        "actions": [{"value": r["action"], "count": r["n"]} for r in actions],
        "entities": [{"value": r["entity"], "count": r["n"]} for r in entities],
        "people": [{"id": str(r["actor_id"]), "name": r["name"], "email": r["email"], "role": r["role"], "count": r["n"]} for r in people],
        "schools": [{"id": str(r["id"]), "name": r["name"]} for r in schools],
        "vias": ["ui", "ai", "system"],
        "total": span["n"], "first": span["first"].astimezone(IST).isoformat() if span["first"] else None,
        "last": span["last"].astimezone(IST).isoformat() if span["last"] else None,
    }
