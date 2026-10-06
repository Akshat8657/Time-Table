"""'My Profile' for teachers and HODs (plan Sections 5.3, 17) + profile photos.

A person can edit only: name, phone, specialization, the subjects they can teach, and their photo.
Role, school, e-mail and the visiting-faculty settings are NOT accepted here (extra fields are refused).
"""
from __future__ import annotations

import io
import time
import uuid

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from .. import audit, db
from ..errors import ApiError
from ..scope import as_uuid
from ..security.deps import CurrentUser, require_roles
from ..services import accounts
from ..validation import Phone, optional_text, text

router = APIRouter(prefix="/api/profile", tags=["profile"])

MAX_UPLOAD = 5 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000  # refuse "decompression bomb" pictures


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: text(2, 120) | None = None  # type: ignore[valid-type]
    phone: Phone = None
    specialization: optional_text(300) = None  # type: ignore[valid-type]
    subject_ids: list[str] | None = Field(default=None, max_length=300)


async def _me(user: CurrentUser) -> dict:
    uid = uuid.UUID(user.id)
    row = await db.fetchrow(accounts.ACCOUNT_SELECT + " where p.id = $1", uid)
    mine = [r["subject_id"] for r in await db.fetch("select subject_id from public.teacher_subjects where teacher_id = $1", uid)]
    subjects = await db.fetch(
        """
        select s.id, s.name, s.code, sm.number, c.name as course_name
        from public.subjects s
        join public.semesters sm on sm.id = s.semester_id
        join public.courses c on c.id = sm.course_id
        where s.school_id = $1
        order by c.name, sm.number, s.name
        """,
        uuid.UUID(user.school_id),  # type: ignore[arg-type]
    )
    return {
        "profile": accounts.account_out(row, mine),
        "available_subjects": [
            {
                "id": str(s["id"]), "name": s["name"], "code": s["code"],
                "course": s["course_name"], "semester": s["number"],
                "label": f"{s['name']} ({s['code']})  -  {s['course_name']} Sem {s['number']}",
            }
            for s in subjects
        ],
    }


@router.get("/me")
async def my_profile(user: CurrentUser = Depends(require_roles("teacher", "hod"))) -> dict:
    return await _me(user)


@router.patch("/me")
async def update_my_profile(body: ProfilePatch, user: CurrentUser = Depends(require_roles("teacher", "hod"))) -> dict:
    uid = uuid.UUID(user.id)
    fields = body.model_fields_set
    row = await db.fetchrow(accounts.ACCOUNT_SELECT + " where p.id = $1", uid)
    full_name = body.full_name if "full_name" in fields and body.full_name else row["full_name"]
    phone = body.phone if "phone" in fields else row["phone"]
    specialization = body.specialization if "specialization" in fields else row["specialization"]
    subject_ids = [as_uuid(s, "subject") for s in body.subject_ids] if body.subject_ids is not None else None

    changes = [
        label for label, old, new in (
            ("name", row["full_name"], full_name), ("phone", row["phone"], phone),
            ("specialization", row["specialization"], specialization),
        ) if old != new
    ]
    if subject_ids is not None:
        changes.append("subjects I can teach")
    async with db.transaction() as conn:
        await conn.execute(
            "update public.profiles set full_name = $2, phone = $3, specialization = $4 where id = $1",
            uid, full_name, phone, specialization,
        )
        if subject_ids is not None:
            await accounts.set_teachable_subjects(conn, uid, uuid.UUID(user.school_id), subject_ids)  # type: ignore[arg-type]
        if changes:
            await audit.record(
                conn, user, "update", "profile", uid, f"{full_name} updated their own profile: {', '.join(changes)}",
                school_id=user.school_id,
            )
    return await _me(user)


# ---------------------------------------------------------------------- photo
def _prepare_photo(raw: bytes) -> bytes:
    """Any normal picture -> a small square JPEG (256 x 256). Rejects anything that is not really a picture."""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in ("JPEG", "PNG", "WEBP", "GIF"):
                raise ValueError("format")
            img.load()
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1])
                img = background
            else:
                img = img.convert("RGB")
            img = ImageOps.fit(img, (256, 256), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, "JPEG", quality=85, optimize=True)  # re-encoding also strips any hidden metadata
            return out.getvalue()
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        raise ApiError(
            422, "BAD_IMAGE", "That file is not a usable picture. Please upload a JPG, PNG or WEBP photo.",
            extra={"fields": {"file": "Please choose a JPG, PNG or WEBP picture."}},
        ) from None


@router.put("/me/photo")
async def upload_photo(file: UploadFile, user: CurrentUser = Depends(require_roles("teacher", "hod"))) -> dict:
    raw = await file.read(MAX_UPLOAD + 1)
    if not raw:
        raise ApiError(422, "BAD_IMAGE", "That file is empty.", extra={"fields": {"file": "The file is empty."}})
    if len(raw) > MAX_UPLOAD:
        raise ApiError(422, "BAD_IMAGE", "That picture is too large (5 MB maximum).", extra={"fields": {"file": "Maximum 5 MB."}})
    jpeg = _prepare_photo(raw)
    uid = uuid.UUID(user.id)
    url = f"/api/profile/photo/{uid}?v={int(time.time())}"
    async with db.transaction() as conn:
        await conn.execute(
            """
            insert into public.profile_photos (user_id, content_type, data) values ($1, 'image/jpeg', $2)
            on conflict (user_id) do update set data = excluded.data, updated_at = now()
            """,
            uid, jpeg,
        )
        await conn.execute("update public.profiles set photo_url = $2 where id = $1", uid, url)
        await audit.record(conn, user, "update", "profile", uid, f"{user.full_name} changed their photo", school_id=user.school_id)
    return {"photo_url": url}


@router.delete("/me/photo")
async def delete_photo(user: CurrentUser = Depends(require_roles("teacher", "hod"))) -> dict:
    uid = uuid.UUID(user.id)
    async with db.transaction() as conn:
        await conn.execute("delete from public.profile_photos where user_id = $1", uid)
        await conn.execute("update public.profiles set photo_url = null where id = $1", uid)
        await audit.record(conn, user, "update", "profile", uid, f"{user.full_name} removed their photo", school_id=user.school_id)
    return {"photo_url": None}


@router.get("/photo/{user_id}")
async def get_photo(user_id: str, user: CurrentUser = Depends(require_roles("admin", "hod", "teacher"))) -> Response:
    """Photos are only visible to people of the same school (and the admin)."""
    uid = as_uuid(user_id, "account")
    row = await db.fetchrow(
        "select p.data, pr.school_id from public.profile_photos p join public.profiles pr on pr.id = p.user_id where p.user_id = $1",
        uid,
    )
    if row is None or (user.role != "admin" and str(row["school_id"]) != user.school_id):
        raise ApiError(404, "NOT_FOUND", "No photo.")
    return Response(row["data"], media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
