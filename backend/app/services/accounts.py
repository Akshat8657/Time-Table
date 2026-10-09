"""Creating / changing login accounts (HOD + teachers). Passwords live ONLY in Supabase Auth.

A new account gets a random temporary password. It is returned ONCE to the admin / HOD, who hands it over by
word of mouth - this project never sends e-mail. At first login the person must choose their own password.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Iterable

import asyncpg

from .. import audit, db
from ..errors import ApiError
from ..security import sessions
from ..security.deps import CurrentUser
from ..security.passwords import generate_temp_password
from ..supabase_auth import AuthError, SupabaseAuth
from ..validation import DAY_NAMES

log = logging.getLogger("tt.accounts")

ACCOUNT_SELECT = """
select p.id, p.email, p.full_name, p.role, p.school_id, s.name as school_name, p.specialization, p.phone, p.photo_url,
       p.is_visiting, p.visiting_days, p.max_weekly_hours, p.is_active, p.must_change_password, p.created_at,
       (select count(*) from public.teacher_subjects ts where ts.teacher_id = p.id) as subjects_count
from public.profiles p
left join public.schools s on s.id = p.school_id
"""


def account_out(row: asyncpg.Record | dict[str, Any], subject_ids: Iterable[Any] | None = None) -> dict[str, Any]:
    out = {
        "id": str(row["id"]),
        "email": row["email"],
        "full_name": row["full_name"],
        "role": row["role"],
        "school_id": str(row["school_id"]) if row["school_id"] else None,
        "school_name": row["school_name"],
        "specialization": row["specialization"],
        "phone": row["phone"],
        "photo_url": row["photo_url"],
        "is_visiting": row["is_visiting"],
        "visiting_days": list(row["visiting_days"] or []),
        "max_weekly_hours": row["max_weekly_hours"],
        "is_active": row["is_active"],
        "must_change_password": row["must_change_password"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "subjects_count": row["subjects_count"],
    }
    if subject_ids is not None:
        out["subject_ids"] = [str(s) for s in subject_ids]
    return out


def auth_failure(exc: AuthError) -> ApiError:
    if exc.code in ("email_exists", "user_already_exists") or "already" in exc.message.lower():
        return ApiError(
            409, "ALREADY_EXISTS", "An account with this email address already exists.",
            extra={"fields": {"email": "This email address is already in use."}},
        )
    if exc.status == 429:
        return ApiError(429, "UPSTREAM_BUSY", "The sign-in service is busy. Please wait a minute and try again.")
    log.warning("Supabase admin call failed: %s %s", exc.status, exc.code)
    return ApiError(503, "AUTH_UNAVAILABLE", "We couldn't reach the account service. Please try again in a moment.")


# ---------------------------------------------------------------------- visiting-faculty rules
async def normalize_visiting(
    role: str, is_visiting: bool, days: list[int] | None, hours: int | None
) -> tuple[bool, list[int], int | None]:
    if role != "teacher":  # an HOD is never "visiting"
        return False, [], None
    if not is_visiting:
        return False, [], None
    working = await db.fetchval("select days_per_week from public.college_settings")
    fields: dict[str, str] = {}
    clean = sorted({int(d) for d in (days or [])})
    if not clean:
        fields["visiting_days"] = "Choose at least one day this visiting teacher can come."
    elif any(d < 1 or d > 6 for d in clean):
        fields["visiting_days"] = "Days must be Monday to Saturday."
    elif any(d > working for d in clean):
        names = ", ".join(DAY_NAMES[d] for d in clean if d > working)
        fields["visiting_days"] = f"{names} is not a working day in the college timing."
    if hours is None:
        fields["max_weekly_hours"] = "Please enter the maximum hours per week."
    if fields:
        raise ApiError(422, "VALIDATION_ERROR", "Please check the visiting-faculty details.", extra={"fields": fields})
    return True, clean, hours


async def check_subjects_in_school(conn: asyncpg.Connection, subject_ids: list[uuid.UUID], school_id: uuid.UUID) -> None:
    if not subject_ids:
        return
    found = await conn.fetchval(
        "select count(*) from public.subjects where school_id = $1 and id = any($2::uuid[])", school_id, subject_ids
    )
    if found != len(set(subject_ids)):
        raise ApiError(422, "VALIDATION_ERROR", "One of the chosen subjects was not found in this school.",
                       extra={"fields": {"subject_ids": "Choose subjects of this school only."}})


async def set_teachable_subjects(
    conn: asyncpg.Connection, teacher_id: uuid.UUID, school_id: uuid.UUID, subject_ids: list[uuid.UUID]
) -> None:
    """Replaces the 'subjects this person can teach' list. Subjects they are already assigned stay protected."""
    wanted = set(subject_ids)
    await check_subjects_in_school(conn, list(wanted), school_id)
    blocked = await conn.fetch(
        """
        select s.name, s.code, t.kind, t.year
        from public.assignments a
        join public.subjects s on s.id = a.subject_id
        join public.academic_terms t on t.id = a.term_id
        where (a.teacher_id = $1 or a.co_teacher_id = $1) and not (a.subject_id = any($2::uuid[]))
        """,
        teacher_id, list(wanted),
    )
    if blocked:
        first = blocked[0]
        raise ApiError(
            409, "IN_USE",
            f"'{first['name']}' ({first['code']}) is assigned to this teacher in {first['kind'].capitalize()} {first['year']}. "
            "Remove that assignment before taking the subject off the teacher's list.",
            extra={"fields": {"subject_ids": "A subject that is already assigned cannot be removed."}},
        )
    await conn.execute(
        "delete from public.teacher_subjects where teacher_id = $1 and not (subject_id = any($2::uuid[]))",
        teacher_id, list(wanted),
    )
    if wanted:
        await conn.execute(
            "insert into public.teacher_subjects (teacher_id, subject_id, school_id) "
            "select $1, x, $3 from unnest($2::uuid[]) x on conflict do nothing",
            teacher_id, list(wanted), school_id,
        )


# ---------------------------------------------------------------------- create
async def create_account(
    auth: SupabaseAuth,
    actor: CurrentUser,
    *,
    email: str,
    full_name: str,
    role: str,
    school_id: uuid.UUID,
    specialization: str | None,
    phone: str | None,
    is_visiting: bool,
    visiting_days: list[int],
    max_weekly_hours: int | None,
    subject_ids: list[uuid.UUID],
    audit_note: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Creates the Supabase login + the profile (+ teachable subjects). Returns (account, temporary password)."""
    if await db.fetchval("select exists(select 1 from public.profiles where email = $1)", email):
        raise ApiError(409, "ALREADY_EXISTS", "An account with this email address already exists.",
                       extra={"fields": {"email": "This email address is already in use."}})
    if subject_ids:  # fail early, before a login is created
        async with db.acquire() as conn:
            await check_subjects_in_school(conn, subject_ids, school_id)

    temp = generate_temp_password()
    try:
        created = await auth.admin_create_user(email=email, password=temp, user_metadata={"full_name": full_name})
    except AuthError as exc:
        raise auth_failure(exc) from None
    user_id = uuid.UUID(created["id"])
    try:
        async with db.transaction() as conn:
            await conn.execute(
                """
                insert into public.profiles (id, full_name, email, role, school_id, specialization, phone,
                                             is_visiting, visiting_days, max_weekly_hours, must_change_password)
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, true)
                """,
                user_id, full_name, email, role, school_id, specialization, phone,
                is_visiting, visiting_days, max_weekly_hours,
            )
            if subject_ids:
                await set_teachable_subjects(conn, user_id, school_id, subject_ids)
            await audit.record(
                conn, actor, "create", role, user_id,
                f"Created {'HOD' if role == 'hod' else 'teacher'} account for {full_name} ({email})" + (f" {audit_note}" if audit_note else ""),
                school_id=school_id,
            )
            row = await conn.fetchrow(ACCOUNT_SELECT + " where p.id = $1", user_id)
    except Exception:
        try:  # never leave a login without a profile behind
            await auth.admin_delete_user(str(user_id))
        except Exception:
            log.exception("Could not remove the half-created login %s", user_id)
        raise
    return account_out(row, subject_ids), temp


# ---------------------------------------------------------------------- reset / deactivate / reactivate
async def reset_password(auth: SupabaseAuth, actor: CurrentUser, target: asyncpg.Record) -> str:
    temp = generate_temp_password()
    async with db.transaction() as conn:
        await conn.execute("update public.profiles set must_change_password = true where id = $1", target["id"])
        await audit.record(
            conn, actor, "reset_password", target["role"], target["id"],
            f"Reset the password of {target['full_name']} ({target['email']})", school_id=target["school_id"],
        )
        try:
            await auth.admin_update_user(str(target["id"]), password=temp)
        except AuthError as exc:
            raise auth_failure(exc) from None
    await sessions.revoke_all_for_user(str(target["id"]), "admin_reset")
    return temp


async def set_active(auth: SupabaseAuth, actor: CurrentUser, target: asyncpg.Record, active: bool) -> None:
    async with db.transaction() as conn:
        if not active:
            busy = await conn.fetchval(
                """
                select (select count(*) from public.assignments a join public.academic_terms t on t.id = a.term_id
                        where (a.teacher_id = $1 or a.co_teacher_id = $1) and t.is_active)
                     + (select count(distinct coalesce(f.combined_group_id, f.id)) from public.fixed_slots f join public.academic_terms t on t.id = f.term_id
                        where (f.teacher_id = $1 or f.co_teacher_id = $1) and t.is_active)
                """,
                target["id"],
            )
            if busy:
                raise ApiError(
                    409, "IN_USE",
                    f"{target['full_name']} still has {busy} assignment(s) / locked slot(s) in the active term. "
                    "Please reassign them first, then deactivate.",
                )
        await conn.execute("update public.profiles set is_active = $2 where id = $1", target["id"], active)
        await audit.record(
            conn, actor, "reactivate" if active else "deactivate", target["role"], target["id"],
            f"{'Reactivated' if active else 'Deactivated'} the account of {target['full_name']} ({target['email']})",
            school_id=target["school_id"],
        )
        try:  # a deactivated person can no longer sign in at all
            await auth.admin_update_user(str(target["id"]), ban_duration="none" if active else "876000h")
        except AuthError as exc:
            raise auth_failure(exc) from None
    if not active:
        await sessions.revoke_all_for_user(str(target["id"]), "account_disabled")
