"""Accounts: admin creates HODs, HOD/admin create teachers (plan Sections 5, 17, 18).

HOD is locked to his own school: he only ever sees / changes the teachers of that school (and himself).
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db
from ..errors import ApiError
from ..scope import as_uuid, resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import accounts
from ..supabase_auth import AuthError
from ..validation import Email, Phone, optional_text, text

router = APIRouter(prefix="/api/users", tags=["accounts"])


class AccountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: text(2, 120)  # type: ignore[valid-type]
    email: Email
    role: Literal["hod", "teacher"] = "teacher"
    school_id: str | None = None
    specialization: optional_text(300) = None  # type: ignore[valid-type]
    phone: Phone = None
    is_visiting: bool = False
    visiting_days: list[int] = Field(default_factory=list, max_length=6)
    max_weekly_hours: int | None = Field(default=None, ge=1, le=60)
    subject_ids: list[str] = Field(default_factory=list, max_length=300)


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: text(2, 120) | None = None  # type: ignore[valid-type]
    email: Email | None = None
    specialization: optional_text(300) = None  # type: ignore[valid-type]
    phone: Phone = None
    is_visiting: bool | None = None
    visiting_days: list[int] | None = Field(default=None, max_length=6)
    max_weekly_hours: int | None = Field(default=None, ge=1, le=60)
    subject_ids: list[str] | None = Field(default=None, max_length=300)


def _ids(values: list[str]) -> list[uuid.UUID]:
    return [as_uuid(v, "subject") for v in values]


async def _load_target(user: CurrentUser, user_id: str):
    """Finds an account this person may manage. Anything else 'does not exist' (404)."""
    row = await db.fetchrow(accounts.ACCOUNT_SELECT + " where p.id = $1", as_uuid(user_id, "account"))
    if row is None or row["role"] == "admin":
        raise ApiError(404, "NOT_FOUND", "That account was not found.")
    if user.role == "hod":
        if row["school_id"] is None or str(row["school_id"]) != user.school_id:
            raise ApiError(404, "NOT_FOUND", "That account was not found.")
        if row["role"] == "hod" and str(row["id"]) != user.id:
            raise ApiError(404, "NOT_FOUND", "That account was not found.")
    return row


async def _detail(user_id) -> dict:
    row = await db.fetchrow(accounts.ACCOUNT_SELECT + " where p.id = $1", user_id)
    ids = [r["subject_id"] for r in await db.fetch("select subject_id from public.teacher_subjects where teacher_id = $1", user_id)]
    return accounts.account_out(row, ids)


# ---------------------------------------------------------------------- list / read
@router.get("")
async def list_accounts(
    role: Literal["hod", "teacher", "all"] = "all",
    school_id: str | None = None,
    status: Literal["active", "inactive", "all"] = "all",
    q: str | None = None,
    user: CurrentUser = Depends(require_roles("admin", "hod")),
) -> dict:
    where = ["p.role <> 'admin'"]
    args: list = []

    def add(clause: str, value) -> None:
        args.append(value)
        where.append(clause.replace("?", f"${len(args)}"))  # '?' may appear several times -> same parameter

    if user.role == "hod":
        if school_id is not None:
            await resolve_school(user, school_id)  # 403 when it is another school
        add("p.school_id = ?", uuid.UUID(user.school_id))  # type: ignore[arg-type]
    elif school_id is not None:
        add("p.school_id = ?", await resolve_school(user, school_id))
    if role != "all":
        add("p.role = ?", role)
    if status != "all":
        add("p.is_active = ?", status == "active")
    if q and q.strip():
        cleaned = q.strip()[:80].replace("%", " ").replace("_", " ")
        add("(p.full_name ilike ? or p.email ilike ? or coalesce(p.specialization, '') ilike ?)", f"%{cleaned}%")
    rows = await db.fetch(
        accounts.ACCOUNT_SELECT + " where " + " and ".join(where) + " order by p.role, lower(p.full_name) limit 500", *args
    )
    return {"users": [accounts.account_out(r) for r in rows], "total": len(rows)}


@router.get("/{user_id}")
async def get_account(user_id: str, user: CurrentUser = Depends(require_roles("admin", "hod"))) -> dict:
    row = await _load_target(user, user_id)
    return await _detail(row["id"])


# ---------------------------------------------------------------------- create
@router.post("", status_code=201)
async def create_account(request: Request, body: AccountIn, user: CurrentUser = Depends(require_roles("admin", "hod"))) -> dict:
    if user.role == "hod" and body.role != "teacher":
        raise ApiError(403, "FORBIDDEN", "Only the administrator can create HOD accounts.")
    school = await resolve_school(user, body.school_id)
    if not await db.fetchval("select is_active from public.schools where id = $1", school):
        raise ApiError(409, "SCHOOL_INACTIVE", "That school is inactive. Activate it first.")
    is_visiting, days, hours = await accounts.normalize_visiting(
        body.role, body.is_visiting, body.visiting_days, body.max_weekly_hours
    )
    account, temp = await accounts.create_account(
        request.app.state.auth, user,
        email=body.email, full_name=body.full_name, role=body.role, school_id=school,
        specialization=body.specialization, phone=body.phone,
        is_visiting=is_visiting, visiting_days=days, max_weekly_hours=hours, subject_ids=_ids(body.subject_ids),
    )
    return {"user": account, "temp_password": temp}


# ---------------------------------------------------------------------- edit
@router.patch("/{user_id}")
async def update_account(
    request: Request, user_id: str, body: AccountPatch, user: CurrentUser = Depends(require_roles("admin", "hod"))
) -> dict:
    row = await _load_target(user, user_id)
    fields = body.model_fields_set
    is_self = str(row["id"]) == user.id

    full_name = body.full_name if "full_name" in fields and body.full_name else row["full_name"]
    specialization = body.specialization if "specialization" in fields else row["specialization"]
    phone = body.phone if "phone" in fields else row["phone"]

    email = row["email"]
    if "email" in fields and body.email and body.email != row["email"]:
        if is_self:
            raise ApiError(403, "FORBIDDEN", "You cannot change your own email address here.")
        if await db.fetchval("select exists(select 1 from public.profiles where email = $1 and id <> $2)", body.email, row["id"]):
            raise ApiError(409, "ALREADY_EXISTS", "An account with this email address already exists.",
                           extra={"fields": {"email": "This email address is already in use."}})
        email = body.email

    # visiting-faculty details: never on an HOD, and never changed by the person himself
    is_visiting, days, hours = row["is_visiting"], list(row["visiting_days"] or []), row["max_weekly_hours"]
    if fields & {"is_visiting", "visiting_days", "max_weekly_hours"}:
        if is_self:
            raise ApiError(403, "FORBIDDEN", "Visiting-faculty settings can only be changed by the administrator or HOD.")
        is_visiting, days, hours = await accounts.normalize_visiting(
            row["role"],
            body.is_visiting if body.is_visiting is not None else row["is_visiting"],
            body.visiting_days if body.visiting_days is not None else days,
            body.max_weekly_hours if "max_weekly_hours" in fields else row["max_weekly_hours"],
        )

    subject_ids = _ids(body.subject_ids) if body.subject_ids is not None else None
    changes = [
        label for label, old, new in (
            ("name", row["full_name"], full_name), ("email", row["email"], email),
            ("specialization", row["specialization"], specialization), ("phone", row["phone"], phone),
            ("visiting", row["is_visiting"], is_visiting), ("visiting days", list(row["visiting_days"] or []), days),
            ("max weekly hours", row["max_weekly_hours"], hours),
        ) if old != new
    ]
    if subject_ids is not None:
        changes.append("subjects they can teach")

    async with db.transaction() as conn:
        await conn.execute(
            """
            update public.profiles
               set full_name = $2, email = $3, specialization = $4, phone = $5,
                   is_visiting = $6, visiting_days = $7, max_weekly_hours = $8
             where id = $1
            """,
            row["id"], full_name, email, specialization, phone, is_visiting, days, hours,
        )
        if subject_ids is not None:
            await accounts.set_teachable_subjects(conn, row["id"], row["school_id"], subject_ids)
        if email != row["email"]:  # the login itself must change too (rolled back with everything else if it fails)
            try:
                await request.app.state.auth.admin_update_user(str(row["id"]), email=email, email_confirm=True)
            except AuthError as exc:
                raise accounts.auth_failure(exc) from None
        if changes:
            await audit.record(
                conn, user, "update", row["role"], row["id"],
                f"Updated {row['full_name']}: {', '.join(changes)}", school_id=row["school_id"],
            )
    return await _detail(row["id"])


# ---------------------------------------------------------------------- deactivate / reactivate / reset password
@router.post("/{user_id}/deactivate")
async def deactivate(request: Request, user_id: str, user: CurrentUser = Depends(require_roles("admin", "hod"))) -> dict:
    row = await _load_target(user, user_id)
    if str(row["id"]) == user.id:
        raise ApiError(409, "NOT_ALLOWED", "You cannot deactivate your own account.")
    await accounts.set_active(request.app.state.auth, user, row, False)
    return await _detail(row["id"])


@router.post("/{user_id}/reactivate")
async def reactivate(request: Request, user_id: str, user: CurrentUser = Depends(require_roles("admin", "hod"))) -> dict:
    row = await _load_target(user, user_id)
    await accounts.set_active(request.app.state.auth, user, row, True)
    return await _detail(row["id"])


@router.post("/{user_id}/reset-password")
async def reset_password(request: Request, user_id: str, user: CurrentUser = Depends(require_roles("admin", "hod"))) -> dict:
    row = await _load_target(user, user_id)
    if str(row["id"]) == user.id:
        raise ApiError(409, "NOT_ALLOWED", "To change your own password, use 'Change password' from the account menu.")
    temp = await accounts.reset_password(request.app.state.auth, user, row)
    return {"user": await _detail(row["id"]), "temp_password": temp}
