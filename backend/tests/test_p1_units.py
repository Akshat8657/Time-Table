"""Phase 1 - pure logic tests (no network, no database): period grid, slot rules, validators, Excel reading."""
from __future__ import annotations

import io
from datetime import time

import pytest
from openpyxl import Workbook

from app.errors import ApiError
from app.security.passwords import generate_temp_password, validate_new_password
from app.services import excel
from app.services.grid import build_grid
from app.services.terms import parity_matches
from app.services.timing import DayRule, class_days, slot_notes, slot_problems
from app.validation import normalize_email, normalize_phone


def hm(g):
    return [(p.number, f"{p.start // 60:02d}:{p.start % 60:02d}", f"{p.end // 60:02d}:{p.end % 60:02d}") for p in g.periods]


# ---------------------------------------------------------------------- period grid (plan Section 7)
def test_plan_example_without_lunch():
    # 10:00-17:00, 1 hour lectures, 5 minute break after every lecture
    g = build_grid(time(10), time(17), 60, 5)
    assert hm(g) == [
        (1, "10:00", "11:00"), (2, "11:05", "12:05"), (3, "12:10", "13:10"),
        (4, "13:15", "14:15"), (5, "14:20", "15:20"), (6, "15:25", "16:25"),
    ]
    assert g.notes  # 16:30-17:00 is too short -> explained


def test_lunch_is_skipped_and_never_overlapped():
    g = build_grid(time(10), time(17), 60, 5, time(13), time(13, 45))
    assert hm(g) == [
        (1, "10:00", "11:00"), (2, "11:05", "12:05"),
        (3, "13:45", "14:45"), (4, "14:50", "15:50"), (5, "15:55", "16:55"),
    ]
    for p in g.periods:  # no period overlaps lunch
        assert p.end <= 13 * 60 or p.start >= 13 * 60 + 45
    assert any("lunch" in n for n in g.notes)  # the 12:10-13:00 gap is explained


def test_real_college_timing_gives_exactly_the_six_real_periods():
    # college 10:00-16:50, 1 hour lectures, 5 min breaks, lunch 13:10-13:40 (the real School of IT timetable)
    g = build_grid(time(10), time(16, 50), 60, 5, time(13, 10), time(13, 40))
    assert hm(g) == [
        (1, "10:00", "11:00"), (2, "11:05", "12:05"), (3, "12:10", "13:10"),
        (4, "13:40", "14:40"), (5, "14:45", "15:45"), (6, "15:50", "16:50"),
    ]
    assert g.notes == []                      # nothing wasted: P3 ends exactly when lunch starts, P6 exactly at closing


def test_lunch_that_fits_between_lectures_wastes_nothing():
    g = build_grid(time(10), time(17), 60, 5, time(12, 5), time(12, 50))
    assert [(p.start // 60, p.start % 60) for p in g.periods][:3] == [(10, 0), (11, 5), (12, 50)]


def test_last_lecture_must_end_by_closing_time():
    g = build_grid(time(10), time(16, 20), 60, 5)   # a 6th lecture would end at 16:25 -> not allowed
    assert g.periods[-1].end <= 16 * 60 + 20
    assert len(g.periods) == 5
    assert len(build_grid(time(10), time(16, 25), 60, 5).periods) == 6   # ending exactly at closing time is fine


def test_custom_lecture_and_break_lengths():
    g = build_grid(time(9), time(13), 45, 10)
    assert hm(g)[0] == (1, "09:00", "09:45") and hm(g)[1] == (2, "09:55", "10:40")


def test_zero_break_means_back_to_back():
    g = build_grid(time(10), time(13), 60, 0)
    assert len(g.periods) == 3 and g.is_continuous(1, 3)


def test_no_lecture_fits():
    g = build_grid(time(10), time(10, 30), 60, 5)
    assert g.periods == [] and g.notes


def test_bad_input_is_refused():
    with pytest.raises(ValueError):
        build_grid(time(17), time(10), 60, 5)
    with pytest.raises(ValueError):
        build_grid(time(10), time(17), 60, 5, time(14), time(13))


def test_continuity_for_double_blocks():
    g = build_grid(time(10), time(17), 60, 5, time(13), time(13, 45))
    assert g.is_continuous(1, 2) and g.is_continuous(3, 3)
    assert not g.is_continuous(2, 2)  # period 2 -> 3 is separated by lunch (strictly continuous = no lunch in between)
    assert not g.is_continuous(5, 2)  # runs off the end of the day


def test_lunch_inside_a_block():
    g = build_grid(time(10), time(16, 50), 60, 5, time(13, 10), time(13, 40))
    assert g.lunch_inside(3, 2) == (13 * 60 + 10, 13 * 60 + 40)     # P3 + P4 = the real SOL III block
    assert g.lunch_inside(1, 2) is None and g.lunch_inside(4, 3) is None and g.lunch_inside(3, 1) is None
    assert build_grid(time(10), time(17), 60, 5).lunch_inside(1, 6) is None   # no lunch set


def test_early_release_counts_periods():
    g = build_grid(time(10), time(17), 60, 5, time(13), time(13, 45))
    assert g.available_periods(None) == 5
    assert g.available_periods(time(15)) == 3          # 14:45 is the last end before 15:00
    assert g.available_periods(time(14, 45)) == 3
    assert g.available_periods(time(14, 44)) == 2
    assert g.available_periods(time(9)) == 0


# ---------------------------------------------------------------------- locked-slot rules (plan V8)
@pytest.fixture
def grid():
    return build_grid(time(10), time(16, 50), 60, 5, time(13, 10), time(13, 40))


def test_double_block_in_a_normal_place_is_fine(grid):
    assert slot_problems(grid, 5, None, 1, 1, 2) == []
    assert slot_problems(grid, 5, None, 5, 4, 2) == []


def test_double_block_around_lunch_is_allowed(grid):
    # "back-to-back" = 2 consecutive PERIODS: SOL III is Period 3 + Period 4 with lunch in between
    assert slot_problems(grid, 5, None, 4, 3, 2) == []
    assert slot_problems(grid, 5, None, 4, 2, 3) == []                         # even 3 periods across the lunch
    notes = slot_notes(grid, 3, 2)
    assert len(notes) == 1 and "lunch" in notes[0].lower() and "13:10-13:40" in notes[0]
    assert slot_notes(grid, 1, 2) == [] and slot_notes(grid, 3, 1) == []


def test_slot_after_the_days_end_time_is_refused(grid):
    ends_1440 = DayRule(end_time=time(14, 40))
    assert slot_problems(grid, 5, ends_1440, 4, 3, 2) == []                    # P3+P4 end exactly at 14:40
    problems = slot_problems(grid, 5, ends_1440, 4, 4, 2)                      # P5 ends 15:45 > 14:40
    assert problems and "end time on Thursday (14:40)" in problems[0]
    assert slot_problems(grid, 5, DayRule(end_time=time(12, 30)), 1, 3, 1)     # P3 ends 13:10
    assert slot_problems(grid, 5, DayRule(end_time=time(11, 0)), 1, 1, 1) == []


def test_slot_on_an_off_day_is_refused(grid):
    problems = slot_problems(grid, 5, DayRule(is_off=True), 5, 1, 2)
    assert problems and "no lectures on Friday" in problems[0]


def test_slot_on_a_non_working_day_or_outside_the_day(grid):
    assert "not a working day" in slot_problems(grid, 5, None, 6, 1, 2)[0]
    assert slot_problems(grid, 6, None, 6, 1, 2) == []
    assert "do not exist" in slot_problems(grid, 5, None, 1, 6, 2)[0]          # periods 6-7: the day has 6
    assert slot_problems(grid, 5, None, 1, 9, 1)


def test_class_week_summary(grid):
    rules = {1: DayRule(end_time=time(14, 40)), 5: DayRule(is_off=True)}
    week = class_days(grid, 5, rules)
    assert [d["periods_available"] for d in week] == [4, 6, 6, 6, 0, 0]       # Mon till 14:40, Fri OFF, Sat not a working day
    assert week[0]["end_time"] == "14:40" and week[4]["is_off"] and not week[5]["working"]
    assert week[1]["end_time"] is None                                        # default = the college closing time


# ---------------------------------------------------------------------- odd / even
@pytest.mark.parametrize("kind,number,expected", [
    ("odd", 1, True), ("odd", 2, False), ("odd", 7, True), ("even", 2, True), ("even", 6, True), ("even", 3, False),
])
def test_odd_even_semesters(kind, number, expected):
    assert parity_matches(kind, number) is expected


# ---------------------------------------------------------------------- generated temporary passwords
def test_temp_passwords_are_strong_and_unique():
    seen = {generate_temp_password() for _ in range(300)}
    assert len(seen) == 300
    for pw in seen:
        raw = pw.replace("-", "")
        assert len(raw) == 12 and pw.count("-") == 2
        assert any(c.isupper() for c in raw) and any(c.islower() for c in raw) and any(c.isdigit() for c in raw)
        assert not set(raw) & set("0O1lI")           # no look-alike characters
        assert validate_new_password(pw, "x", "someone@example.com") is None  # also acceptable as a real password


# ---------------------------------------------------------------------- validators
@pytest.mark.parametrize("value", ["a@b.co", "First.Last+tag@Sub.Example.IN", "o'brien@example.com"])
def test_good_emails(value):
    assert normalize_email(value) == value.lower()


@pytest.mark.parametrize("value", ["", "a@b", "a b@c.com", "@x.com", "a@@x.com", "x" * 260 + "@a.com", None, 5])
def test_bad_emails(value):
    with pytest.raises(ValueError):
        normalize_email(value)


def test_phone_rules():
    assert normalize_phone("") is None and normalize_phone(None) is None
    assert normalize_phone(" +91 98765-43210 ") == "+91 98765-43210"
    for bad in ("abc", "12", "98765<script>", "1" * 40):
        with pytest.raises(ValueError):
            normalize_phone(bad)


# ---------------------------------------------------------------------- Excel reading
def make_xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


COLS = {"name": "fullname", "email": "email", "hours": "maxweeklyhours"}


def test_excel_reads_rows_and_skips_blank_ones():
    raw = make_xlsx([["Full Name *", "Email *", "Max Weekly Hours"], ["Asha", "a@x.com", 6], [None, None, None], ["Ravi", "r@x.com", 4.0]])
    rows = excel.read_rows(raw, COLS, ["name", "email"])
    assert rows == [(2, {"name": "Asha", "email": "a@x.com", "hours": "6"}), (4, {"name": "Ravi", "email": "r@x.com", "hours": "4"})]


def test_excel_missing_column_is_explained():
    raw = make_xlsx([["Name", "Phone"], ["Asha", "1"]])
    with pytest.raises(ApiError) as e:
        excel.read_rows(raw, COLS, ["name", "email"])
    assert e.value.code == "BAD_FILE" and "email" in e.value.message


@pytest.mark.parametrize("raw", [b"", b"not a zip at all", b"PK\x03\x04garbage" * 10])
def test_excel_rejects_garbage(raw):
    with pytest.raises(ApiError) as e:
        excel.read_rows(raw, COLS, ["name"])
    assert e.value.code == "BAD_FILE"


def test_excel_rejects_a_huge_upload():
    with pytest.raises(ApiError):
        excel.read_rows(b"0" * (excel.MAX_FILE_BYTES + 1), COLS, ["name"])


def test_excel_templates_can_be_read_back():
    raw = excel.build_template("Teachers", ["Full Name *", "Email *"], [20, 20], [["A", "a@x.com"]], ["How to fill", "line"], yes_no_columns=[2])
    assert excel.read_rows(raw, {"name": "fullname", "email": "email"}, ["name"]) == [(2, {"name": "A", "email": "a@x.com"})]


def test_yes_no_parsing():
    assert excel.yes_no("Yes") is True and excel.yes_no(" y ") is True and excel.yes_no("") is False
    assert excel.yes_no("No") is False and excel.yes_no("maybe") is None


# ---------------------------------------------------------------------- optional fields: null / empty / too long (found by the browser test)
def test_optional_text_accepts_null_empty_and_text_but_refuses_too_long():
    from pydantic import BaseModel, ValidationError

    from app.validation import optional_text

    class M(BaseModel):
        note: optional_text(10) = None  # type: ignore[valid-type]

    assert M(note=None).note is None
    assert M(note="").note is None
    assert M(note="   ").note is None
    assert M(note="  hello  ").note == "hello"
    with pytest.raises(ValidationError):
        M(note="x" * 11)


def test_every_optional_text_field_of_the_api_accepts_null():
    """The browser sends null for every empty optional field - none of these may crash."""
    from app.routers.calendar import EntryIn
    from app.routers.fixed_slots import SlotIn, SlotPatch
    from app.routers.profile import ProfilePatch
    from app.routers.structure import CourseIn
    from app.routers.users import AccountIn, AccountPatch

    assert SlotIn(semester_id="x", day=1, start_period=1, kind="PE", label=None, note=None, teacher_id=None).note is None
    assert SlotPatch(note=None, label=None).note is None
    assert EntryIn(title="t", date_from="2026-01-01", note=None).note is None
    assert CourseIn(school_id="x", name="n", code=None, num_semesters=3).code is None
    assert AccountIn(full_name="Ab", email="a@b.co", specialization=None, phone=None).phone is None
    assert AccountPatch(specialization=None, phone=None).specialization is None
    assert ProfilePatch(specialization=None, phone=None).phone is None
