"""Phase 1 API tests, part D: a class that has NO PE and NO SOL (COLLEGE_SEED_DATA V2: B.Sc. Sem 5 and Sem 7).

The Fixed Slots checklist must not nag about such a class, and a PE / SOL slot must not be placed on it by mistake.
Runs in the throw-away school against the REAL API + Supabase (every request ~0.5 s).
"""
from __future__ import annotations

import uuid

import pytest

from .conftest import db_fetch, db_val

SEM = 5      # an odd semester: running in "Odd 2026"
EVEN = 6     # not running in the odd term


@pytest.fixture(scope="module", autouse=True)
def active_odd_term(p1):
    assert p1.api(p1.admin, "PUT", "/api/terms/active", json={"kind": "odd", "year": 2026}).status_code == 200
    assert p1.api(p1.admin, "PUT", "/api/timing/college", json={"college_start": "10:00", "college_end": "16:50", "lecture_minutes": 60, "break_minutes": 5, "days_per_week": 5}).status_code == 200


def _class(p1, n: int = SEM) -> dict:
    data = p1.api(p1.hod, "GET", "/api/fixed-slots").json()
    return next(c for c in data["classes"] if c["semester_id"] == p1.sems[n])


def _mark(p1, who, value: bool, n: int = SEM, **kw):
    return p1.api(who, "PUT", f"/api/fixed-slots/classes/{p1.sems[n]}/no-pe-sol", json={"no_pe_sol": value}, **kw)


def _slot(p1, kind: str, day: int, start: int, n: int = SEM):
    return p1.api(p1.hod, "POST", "/api/fixed-slots", json={"semester_id": p1.sems[n], "day": day, "start_period": start, "kind": kind})


def _setup(p1) -> dict:
    return p1.api(p1.hod, "GET", "/api/hod/overview").json()["setup"]


def test_a_class_is_expected_to_have_pe_and_sol_until_it_is_marked_otherwise(p1):
    c = _class(p1)
    assert c["no_pe_sol"] is False
    assert db_val("select no_pe_sol from public.semesters where id = $1", uuid.UUID(p1.sems[SEM])) is False
    assert _setup(p1)["no_pe_sol_classes"] == 0


def test_marking_a_class_without_pe_sol_is_saved_audited_and_shown(p1):
    before = db_val("select coalesce(max(id), 0) from public.audit_log")
    r = _mark(p1, p1.hod, True)
    assert r.status_code == 200 and r.json() == {"semester_id": p1.sems[SEM], "no_pe_sol": True}
    assert _class(p1)["no_pe_sol"] is True
    assert db_val("select no_pe_sol from public.semesters where id = $1", uuid.UUID(p1.sems[SEM])) is True
    # the other classes are not touched
    assert _class(p1, 1)["no_pe_sol"] is False and _class(p1, 3)["no_pe_sol"] is False
    rows = db_fetch("select action, entity, summary, school_id from public.audit_log where id > $1 and entity = 'class_pe_sol' order by id", before)
    assert len(rows) == 1 and rows[0]["action"] == "update" and "no PE / SOL" in rows[0]["summary"] and str(rows[0]["school_id"]) == p1.school
    # marking twice is harmless
    assert _mark(p1, p1.hod, True).status_code == 200


def test_the_setup_checklist_counts_only_the_classes_that_need_pe_sol(p1):
    setup = _setup(p1)
    assert setup["no_pe_sol_classes"] == 1
    assert setup["classes"] >= 1                       # the class is still a class; the dashboard subtracts the exempt ones


def test_a_pe_or_sol_slot_is_refused_on_such_a_class_but_other_slots_are_fine(p1):
    for kind in ("PE", "SOL"):
        r = _slot(p1, kind, 1, 1)
        assert r.status_code == 422 and r.json()["code"] == "SLOT_NOT_POSSIBLE" and "no PE / SOL" in r.json()["message"], r.text
        c = p1.api(p1.hod, "POST", "/api/fixed-slots/check", json={"semester_id": p1.sems[SEM], "day": 1, "start_period": 1, "kind": kind})
        assert c.status_code == 200 and c.json()["ok"] is False and any("no PE / SOL" in p for p in c.json()["problems"])
    assert db_val("select count(*) from public.fixed_slots where semester_id = $1 and kind in ('PE','SOL')", uuid.UUID(p1.sems[SEM])) == 0
    other = p1.api(p1.hod, "POST", "/api/fixed-slots/check", json={"semester_id": p1.sems[SEM], "day": 1, "start_period": 1, "kind": "OTHER", "length": 1, "label": "Seminar"})
    assert other.status_code == 200 and other.json()["ok"] is True


def test_unmarking_brings_pe_and_sol_back_and_a_marked_class_cannot_get_slots_by_accident(p1):
    r = _mark(p1, p1.hod, False)
    assert r.status_code == 200 and r.json()["no_pe_sol"] is False
    assert _class(p1)["no_pe_sol"] is False and _setup(p1)["no_pe_sol_classes"] == 0
    made = _slot(p1, "PE", 1, 1)
    assert made.status_code == 201, made.text


def test_a_class_that_already_has_pe_or_sol_slots_cannot_be_marked(p1):
    r = _mark(p1, p1.hod, True)
    assert r.status_code == 409 and r.json()["code"] == "IN_USE" and "PE / SOL slot" in r.json()["message"], r.text
    assert _class(p1)["no_pe_sol"] is False
    slots = [s for s in p1.api(p1.hod, "GET", "/api/fixed-slots").json()["slots"] if s["semester_id"] == p1.sems[SEM] and s["kind"] == "PE"]
    assert len(slots) == 1
    assert p1.api(p1.hod, "DELETE", f"/api/fixed-slots/{slots[0]['id']}").status_code == 200
    assert _mark(p1, p1.hod, True).status_code == 200            # after removing the slot it works
    assert _mark(p1, p1.hod, False).status_code == 200           # leave the class as it was


def test_the_switch_checks_its_input_the_class_the_term_and_the_role(p1):
    assert p1.api(p1.hod, "PUT", f"/api/fixed-slots/classes/{p1.sems[SEM]}/no-pe-sol", json={}).status_code == 422
    assert p1.api(p1.hod, "PUT", f"/api/fixed-slots/classes/{p1.sems[SEM]}/no-pe-sol", json={"no_pe_sol": "maybe"}).status_code == 422
    assert p1.api(p1.hod, "PUT", f"/api/fixed-slots/classes/{p1.sems[SEM]}/no-pe-sol", json={"no_pe_sol": True, "extra": 1}).status_code == 422
    assert p1.api(p1.hod, "PUT", "/api/fixed-slots/classes/not-an-id/no-pe-sol", json={"no_pe_sol": True}).status_code in (404, 422)
    assert p1.api(p1.hod, "PUT", f"/api/fixed-slots/classes/{uuid.uuid4()}/no-pe-sol", json={"no_pe_sol": True}).status_code == 404
    r = _mark(p1, p1.hod, True, EVEN)                            # Sem 6 does not run in an odd term
    assert r.status_code == 409 and r.json()["code"] == "SEMESTER_NOT_ACTIVE"
    assert db_val("select no_pe_sol from public.semesters where id = $1", uuid.UUID(p1.sems[EVEN])) is False
    assert _mark(p1, p1.teacher, True).status_code == 403
    assert _class(p1)["no_pe_sol"] is False


def test_an_hod_cannot_mark_a_class_of_another_school(p1):
    other = p1.b_sems[3]                                          # Sem 3 of the other school: running, and it has no PE / SOL slot
    r = p1.api(p1.hod, "PUT", f"/api/fixed-slots/classes/{other}/no-pe-sol", json={"no_pe_sol": True})
    assert r.status_code == 404
    assert db_val("select no_pe_sol from public.semesters where id = $1", uuid.UUID(other)) is False
    # the admin may mark a class of any school - and the audit row carries that school
    before = db_val("select coalesce(max(id), 0) from public.audit_log")
    r = p1.api(p1.admin, "PUT", f"/api/fixed-slots/classes/{other}/no-pe-sol", json={"no_pe_sol": True})
    assert r.status_code == 200, r.text
    assert db_val("select no_pe_sol from public.semesters where id = $1", uuid.UUID(other)) is True
    assert db_val("select school_id from public.audit_log where id > $1 and entity = 'class_pe_sol'", before) == uuid.UUID(p1.school_b)
    assert p1.api(p1.admin, "PUT", f"/api/fixed-slots/classes/{other}/no-pe-sol", json={"no_pe_sol": False}).status_code == 200
