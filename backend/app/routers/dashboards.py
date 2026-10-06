"""Placeholder dashboards (Phase 0). Each route is locked to the right roles."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from .. import db
from ..security.deps import CurrentUser, require_roles

router = APIRouter(prefix="/api", tags=["dashboards"])


@router.get("/admin/overview")
async def admin_overview(user: CurrentUser = Depends(require_roles("admin"))) -> dict:
    schools = await db.fetch("select id, name, is_active from public.schools order by name")
    counts = await db.fetchrow(
        """
        select count(*) filter (where role = 'admin')   as admins,
               count(*) filter (where role = 'hod')     as hods,
               count(*) filter (where role = 'teacher') as teachers
        from public.profiles where is_active
        """
    )
    return {
        "scope": "college",
        "schools": [{"id": str(s["id"]), "name": s["name"], "is_active": s["is_active"]} for s in schools],
        "counts": {"admins": counts["admins"], "hods": counts["hods"], "teachers": counts["teachers"]},
    }


@router.get("/hod/overview")
async def hod_overview(user: CurrentUser = Depends(require_roles("hod", "admin"))) -> dict:
    if user.role == "admin":  # admin can do everything an HOD can, in every school
        schools = await db.fetch("select id, name from public.schools where is_active order by name")
        return {"scope": "all-schools", "school": None, "schools": [{"id": str(s["id"]), "name": s["name"]} for s in schools]}
    school_id = uuid.UUID(user.school_id)  # type: ignore[arg-type]
    school = await db.fetchrow("select id, name from public.schools where id = $1", school_id)
    teachers = await db.fetchval(
        "select count(*) from public.profiles where school_id = $1 and role = 'teacher' and is_active", school_id
    )
    return {
        "scope": "school",
        "school": {"id": str(school["id"]), "name": school["name"]} if school else None,
        "counts": {"teachers": teachers},
    }


@router.get("/teacher/overview")
async def teacher_overview(user: CurrentUser = Depends(require_roles("teacher", "hod"))) -> dict:
    # HOD is also a teacher, so HOD gets Teacher Mode too.
    return {
        "scope": "me",
        "mode": "hod-teacher-mode" if user.role == "hod" else "teacher",
        "school": {"id": user.school_id, "name": user.school_name},
    }
