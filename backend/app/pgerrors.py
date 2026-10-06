"""Turns raw database errors into friendly API errors (the last safety net behind every check we do in Python)."""
from __future__ import annotations

import logging

import asyncpg

from .errors import ApiError

log = logging.getLogger("tt.db")

_UNIQUE = {
    "schools_name_unique": "A school with this name already exists.",
    "courses_school_name_unique": "This school already has a course with that name.",
    "subjects_school_code_unique": "That subject code is already used in this school.",
    "elective_groups_name_unique": "This class already has an elective group with that name.",
    "profiles_email_unique": "An account with this email address already exists.",
    "assignments_term_id_subject_id_key": "This subject already has a teacher for the active term.",
    "calendar_preloaded_unique": "That holiday is already in the list.",
    "academic_terms_kind_year_key": "That term already exists.",
    "academic_terms_one_active": "Another term is still active. Please try again.",
}
_EXCLUDE = {
    "fixed_slots_no_overlap_class": "This class already has a locked slot at that time.",
    "fixed_slots_no_overlap_teacher": "That teacher is already busy in another locked slot at that time.",
}
_FOREIGN = {
    "assignments_teacher_id_subject_id_fkey": "That teacher is not allowed to teach this subject. Add the subject to the teacher's 'subjects they can teach' first.",
}


def translate(exc: asyncpg.PostgresError) -> ApiError:
    constraint = getattr(exc, "constraint_name", None) or ""
    if isinstance(exc, asyncpg.UniqueViolationError):
        return ApiError(409, "ALREADY_EXISTS", _UNIQUE.get(constraint, "That already exists."))
    if isinstance(exc, asyncpg.ExclusionViolationError):
        return ApiError(409, "SLOT_TAKEN", _EXCLUDE.get(constraint, "That time is already taken."))
    if isinstance(exc, asyncpg.ForeignKeyViolationError):
        if constraint in _FOREIGN:
            return ApiError(422, "NOT_ALLOWED", _FOREIGN[constraint])
        return ApiError(409, "IN_USE", "That is still linked to other data, or the linked item does not exist.")
    if isinstance(exc, asyncpg.CheckViolationError):
        return ApiError(422, "VALIDATION_ERROR", "Those values are not allowed. Please check them and try again.")
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        return ApiError(403, "FORBIDDEN", "You don't have permission to do that.")
    log.error("Unexpected database error: %s (%s)", type(exc).__name__, constraint or "-")
    return ApiError(500, "SERVER_ERROR", "Something went wrong on our side. Please try again.")
