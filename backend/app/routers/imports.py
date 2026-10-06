"""Excel bulk import of teachers and subjects (plan Section 19, Phase 1).

Flow: download the sample template -> fill it -> upload with dry_run=true to see EVERY row checked
-> upload again with dry_run=false: only the valid rows are imported, the others are reported row by row.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import Response

from .. import audit, db
from ..errors import ApiError
from ..scope import resolve_school
from ..security.deps import CurrentUser, require_roles
from ..services import accounts
from ..services import excel
from ..services import terms as term_svc
from ..validation import DAY_NAMES, normalize_email, normalize_phone
from .subjects import group_warnings

router = APIRouter(prefix="/api/imports", tags=["excel-import"])
ROLES = ("admin", "hod")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_TEACHERS = 100

_DAY_LOOKUP = {}
for _n, _name in DAY_NAMES.items():
    _DAY_LOOKUP[_name.lower()] = _n
    _DAY_LOOKUP[_name[:3].lower()] = _n
    _DAY_LOOKUP[str(_n)] = _n
_DAY_LOOKUP["tues"] = 2
_DAY_LOOKUP["thur"] = 4
_DAY_LOOKUP["thurs"] = 4

TEACHER_COLUMNS = {
    "full_name": "fullname", "email": "email", "phone": "phone", "specialization": "specialization",
    "visiting": "visiting", "days": "visitingdays", "hours": "maxweeklyhours", "subjects": "subjectcodes",
}
SUBJECT_COLUMNS = {
    "course": "course", "semester": "semester", "name": "subjectname", "code": "subjectcode", "credits": "credits",
    "hours": "semesterhours", "lab": "lab", "elective": "elective", "group": "electivegroup", "special": "special",
}


# ---------------------------------------------------------------------- templates
@router.get("/templates/{kind}")
async def template(kind: Literal["teachers", "subjects"], user: CurrentUser = Depends(require_roles(*ROLES))) -> Response:
    if kind == "teachers":
        data = excel.build_template(
            "Teachers",
            ["Full Name *", "Email *", "Phone", "Specialization", "Visiting Faculty (Yes/No)",
             "Visiting Days (e.g. Mon, Wed)", "Max Weekly Hours", "Subject Codes They Can Teach (comma separated)"],
            [26, 32, 18, 30, 16, 22, 14, 40],
            [["Asha Patel", "asha.patel@example.com", "9876543210", "AI, Machine Learning", "No", "", "", "BSC501, BSC502"],
             ["Ravi Shah", "ravi.shah@example.com", "", "Cyber Security", "Yes", "Tue, Thu", 6, "BSC503"]],
            ["How to fill the Teachers sheet",
             "* = required. One teacher per row. Delete the two example rows before uploading your own list.",
             "Email: must be unique. A temporary password is made for every new teacher (shown once after the import).",
             "Visiting Faculty: Yes / No. If Yes, fill the allowed days (Mon, Tue, Wed, Thu, Fri, Sat) and the max weekly hours.",
             "Subject Codes: codes of subjects that already exist in your school (Subjects & Credits screen). Separate with commas. Optional.",
             "Every row is checked first. Rows with a problem are listed with the reason, and only the correct rows are imported."],
            yes_no_columns=[5],
        )
        name = "teachers_template.xlsx"
    else:
        data = excel.build_template(
            "Subjects",
            ["Course *", "Semester *", "Subject Name *", "Subject Code *", "Credits *", "Semester Hours *",
             "Lab (Yes/No)", "Elective (Yes/No)", "Elective Group", "Special (PE / SOL)"],
            [14, 10, 34, 14, 10, 14, 12, 14, 18, 16],
            [["B.Sc. IT", 5, "Database Management", "BSC501", 4, 60, "No", "No", "", ""],
             ["B.Sc. IT", 5, "Artificial Intelligence", "BSC5E1", 3, 45, "No", "Yes", "Group A", ""],
             ["B.Sc. IT", 5, "Physical Education", "BSCPE5", 1, 30, "No", "No", "", "PE"]],
            ["How to fill the Subjects sheet",
             "* = required. One subject per row. Delete the example rows before uploading your own list.",
             "Course: the course name or code exactly as in College Setup (for example B.Sc. IT).",
             "Semester: a number. Only semesters that run in the ACTIVE term are accepted (odd term = 1, 3, 5, 7; even term = 2, 4, 6).",
             "Credits = minimum lectures per week. Semester Hours = total hours for the semester.",
             "Lab: a lab session is always 2 lectures back-to-back.",
             "Elective Group: only for electives. Electives with the same group name run in parallel. Give them the same credits.",
             "Special: leave empty, or PE / SOL (their weekly lectures come from the Fixed Slots screen, not from credits).",
             "Every row is checked first. Rows with a problem are listed with the reason, and only the correct rows are imported."],
            yes_no_columns=[7, 8],
        )
        name = "subjects_template.xlsx"
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def _read_upload(file: UploadFile) -> bytes:
    name = (file.filename or "").lower()
    if not name.endswith(".xlsx"):
        raise ApiError(422, "BAD_FILE", "Please upload an Excel file with the .xlsx ending.", extra={"fields": {"file": "Only .xlsx files."}})
    return await file.read(excel.MAX_FILE_BYTES + 1)


def _row_result(row: int, label: str, errors: list[str]) -> dict[str, Any]:
    return {"row": row, "label": label, "status": "error" if errors else "ok", "errors": errors}


def _summary(results: list[dict[str, Any]], dry_run: bool, **extra: Any) -> dict[str, Any]:
    invalid = sum(1 for r in results if r["status"] == "error")
    return {
        "dry_run": dry_run,
        "total": len(results),
        "valid": len(results) - invalid,
        "invalid": invalid,
        "imported": sum(1 for r in results if r["status"] == "imported"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "rows": results,
        **extra,
    }


# ---------------------------------------------------------------------- teachers
@router.post("/teachers")
async def import_teachers(
    request: Request,
    file: UploadFile,
    dry_run: bool = True,
    school_id: str | None = None,
    user: CurrentUser = Depends(require_roles(*ROLES)),
) -> dict:
    sid = await resolve_school(user, school_id)
    rows = excel.read_rows(await _read_upload(file), TEACHER_COLUMNS, ["full_name", "email"])
    if not rows:
        raise ApiError(422, "BAD_FILE", "No teachers were found in the file.", extra={"fields": {"file": "The sheet has no rows."}})
    if len(rows) > MAX_TEACHERS:
        raise ApiError(422, "BAD_FILE", f"Please import at most {MAX_TEACHERS} teachers at a time.", extra={"fields": {"file": "Too many rows."}})

    college_days = await db.fetchval("select days_per_week from public.college_settings")
    existing_emails = {r["email"] for r in await db.fetch("select email from public.profiles")}
    subject_ids = {r["code"].strip().lower(): r["id"] for r in await db.fetch("select id, code from public.subjects where school_id = $1", sid)}
    seen: set[str] = set()

    results: list[dict[str, Any]] = []
    prepared: dict[int, dict[str, Any]] = {}
    for number, item in rows:
        errors: list[str] = []
        name = item.get("full_name", "")
        if not 2 <= len(name) <= 120:
            errors.append("Full Name must be 2 to 120 characters.")
        email = ""
        try:
            email = normalize_email(item.get("email", ""))
        except ValueError as exc:
            errors.append(f"Email: {exc}")
        if email:
            if email in seen:
                errors.append("This email appears twice in the file.")
            elif email in existing_emails:
                errors.append("An account with this email already exists.")
            seen.add(email)
        phone = None
        try:
            phone = normalize_phone(item.get("phone", ""))
        except ValueError as exc:
            errors.append(f"Phone: {exc}")
        spec = item.get("specialization", "") or None
        if spec and len(spec) > 300:
            errors.append("Specialization is too long (300 characters maximum).")
        visiting = excel.yes_no(item.get("visiting", ""))
        days: list[int] = []
        hours: int | None = None
        if visiting is None:
            errors.append("Visiting Faculty must be Yes or No.")
            visiting = False
        if visiting:
            tokens = [t for t in re.split(r"[,;/\s]+", item.get("days", "").lower()) if t]
            unknown = [t for t in tokens if t not in _DAY_LOOKUP]
            days = sorted({_DAY_LOOKUP[t] for t in tokens if t in _DAY_LOOKUP})
            if unknown:
                errors.append("Unknown day(s): " + ", ".join(unknown) + ". Use Mon, Tue, Wed, Thu, Fri, Sat.")
            elif not days:
                errors.append("A visiting teacher needs at least one allowed day.")
            elif any(d > college_days for d in days):
                errors.append("One of the days is not a working day in the college timing.")
            if not item.get("hours", "").isdigit() or not 1 <= int(item["hours"]) <= 60:
                errors.append("A visiting teacher needs Max Weekly Hours between 1 and 60.")
            else:
                hours = int(item["hours"])
        wanted: list[uuid.UUID] = []
        codes = [c.strip() for c in re.split(r"[,;]+", item.get("subjects", "")) if c.strip()]
        missing = [c for c in codes if c.lower() not in subject_ids]
        if missing:
            errors.append("Unknown subject code(s): " + ", ".join(missing) + " (add them on the Subjects screen first).")
        else:
            wanted = list({subject_ids[c.lower()] for c in codes})
        results.append(_row_result(number, f"{name or '(no name)'} <{email or item.get('email', '')}>", errors))
        if not errors:
            prepared[number] = {
                "full_name": name, "email": email, "phone": phone, "specialization": spec,
                "is_visiting": bool(visiting), "visiting_days": days, "max_weekly_hours": hours, "subject_ids": wanted,
            }

    credentials: list[dict[str, str]] = []
    if not dry_run:
        for result in results:
            data = prepared.get(result["row"])
            if data is None:
                continue
            try:
                account, temp = await accounts.create_account(
                    request.app.state.auth, user, role="teacher", school_id=sid, audit_note="via Excel import", **data
                )
            except ApiError as exc:
                result["status"], result["errors"] = "failed", [exc.message]
                continue
            result["status"] = "imported"
            credentials.append({"row": result["row"], "name": account["full_name"], "email": account["email"], "temp_password": temp})
        async with db.transaction() as conn:
            await audit.record(
                conn, user, "import", "teacher", None,
                f"Excel import of teachers: {len(credentials)} imported, {sum(1 for r in results if r['status'] != 'imported')} not imported",
                school_id=sid,
            )
    return _summary(results, dry_run, credentials=credentials)


# ---------------------------------------------------------------------- subjects
@router.post("/subjects")
async def import_subjects(
    file: UploadFile,
    dry_run: bool = True,
    school_id: str | None = None,
    user: CurrentUser = Depends(require_roles(*ROLES)),
) -> dict:
    sid = await resolve_school(user, school_id)
    term = await term_svc.require_active_term()
    rows = excel.read_rows(
        await _read_upload(file), SUBJECT_COLUMNS, ["course", "semester", "name", "code", "credits", "hours"]
    )
    if not rows:
        raise ApiError(422, "BAD_FILE", "No subjects were found in the file.", extra={"fields": {"file": "The sheet has no rows."}})

    courses = {}
    for c in await db.fetch("select id, name, code, num_semesters, is_active from public.courses where school_id = $1", sid):
        courses[c["name"].strip().lower()] = c
        if c["code"]:
            courses.setdefault(c["code"].strip().lower(), c)
    semesters = {(r["course_id"], r["number"]): r["id"] for r in await db.fetch("select id, course_id, number from public.semesters where school_id = $1", sid)}
    existing_codes = {r["code"].strip().lower() for r in await db.fetch("select code from public.subjects where school_id = $1", sid)}
    seen: set[str] = set()

    results: list[dict[str, Any]] = []
    prepared: dict[int, dict[str, Any]] = {}
    for number, item in rows:
        errors: list[str] = []
        name, code = item.get("name", ""), item.get("code", "")
        if not 1 <= len(name) <= 150:
            errors.append("Subject Name must be 1 to 150 characters.")
        if not 1 <= len(code) <= 30:
            errors.append("Subject Code must be 1 to 30 characters.")
        elif code.lower() in seen:
            errors.append("This subject code appears twice in the file.")
        elif code.lower() in existing_codes:
            errors.append("This subject code already exists in the school.")
        seen.add(code.lower())

        course = courses.get(item.get("course", "").strip().lower())
        semester_id = None
        sem_no = item.get("semester", "")
        if course is None:
            errors.append(f"Course '{item.get('course', '')}' was not found in this school.")
        elif not course["is_active"]:
            errors.append(f"Course '{course['name']}' is inactive.")
        elif not sem_no.isdigit() or not 1 <= int(sem_no) <= course["num_semesters"]:
            errors.append(f"Semester must be a number from 1 to {course['num_semesters']} for {course['name']}.")
        else:
            n = int(sem_no)
            if not term_svc.parity_matches(term["kind"], n):
                errors.append(f"Semester {n} does not run in the active term ({term_svc.term_name(term['kind'], term['year'])}).")
            else:
                semester_id = semesters.get((course["id"], n))
        credits = hours = None
        if not item.get("credits", "").isdigit() or not 1 <= int(item["credits"]) <= 30:
            errors.append("Credits must be a whole number from 1 to 30.")
        else:
            credits = int(item["credits"])
        if not item.get("hours", "").isdigit() or not 1 <= int(item["hours"]) <= 400:
            errors.append("Semester Hours must be a whole number from 1 to 400.")
        else:
            hours = int(item["hours"])
        lab, elective = excel.yes_no(item.get("lab", "")), excel.yes_no(item.get("elective", ""))
        if lab is None:
            errors.append("Lab must be Yes or No.")
        if elective is None:
            errors.append("Elective must be Yes or No.")
        special = item.get("special", "").upper() or None
        if special not in (None, "PE", "SOL"):
            errors.append("Special must be empty, PE or SOL.")
            special = None
        if special and (lab or elective):
            errors.append("A PE / SOL subject cannot also be a lab or an elective.")
        group = item.get("group", "") or None
        if group and not elective:
            errors.append("Elective Group can only be used for electives.")
        if group and len(group) > 60:
            errors.append("Elective Group name is too long (60 characters maximum).")
        results.append(_row_result(number, f"{name or '(no name)'} ({code})", errors))
        if not errors and semester_id is not None:
            prepared[number] = {
                "semester_id": semester_id, "name": name, "code": code, "credits": credits, "hours": hours,
                "is_lab": bool(lab), "is_elective": bool(elective), "special": special, "group": group,
            }

    warnings: list[str] = []
    if not dry_run:
        touched_groups: set[uuid.UUID] = set()
        async with db.transaction() as conn:
            for result in results:
                data = prepared.get(result["row"])
                if data is None:
                    continue
                try:
                    async with conn.transaction():  # a savepoint: one bad row cannot spoil the others
                        gid = None
                        if data["group"]:
                            gid = await conn.fetchval(
                                "select id from public.elective_groups where semester_id = $1 and lower(btrim(name)) = lower(btrim($2))",
                                data["semester_id"], data["group"],
                            )
                            if gid is None:
                                gid = await conn.fetchval(
                                    "insert into public.elective_groups (school_id, semester_id, name) values ($1, $2, $3) returning id",
                                    sid, data["semester_id"], data["group"],
                                )
                        new_id = await conn.fetchval(
                            """
                            insert into public.subjects (school_id, semester_id, name, code, credits, semester_hours, is_lab,
                                                         is_elective, special_kind, elective_group_id, created_by)
                            values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) returning id
                            """,
                            sid, data["semester_id"], data["name"], data["code"], data["credits"], data["hours"],
                            data["is_lab"], data["is_elective"], data["special"], gid, uuid.UUID(user.id),
                        )
                        await audit.record(
                            conn, user, "create", "subject", new_id,
                            f"Created subject {data['name']} ({data['code']}) via Excel import", school_id=sid,
                        )
                        if gid:
                            touched_groups.add(gid)
                    result["status"] = "imported"
                except Exception:
                    result["status"], result["errors"] = "failed", ["This row could not be saved (it may clash with existing data)."]
            for gid in touched_groups:
                warnings += await group_warnings(conn, gid)
            imported = sum(1 for r in results if r["status"] == "imported")
            await audit.record(
                conn, user, "import", "subject", None,
                f"Excel import of subjects: {imported} imported, {len(results) - imported} not imported", school_id=sid,
            )
    return _summary(results, dry_run, warnings=warnings)
