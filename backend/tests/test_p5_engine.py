"""Phase 5 pure-logic tests (no database, no server): the day-change data model, the day overlay (substitute / move / cancel),
the substitution ranking + free check, the one-day adjustment clash checks + smart suggestions, cancelled-lecture hours, and
the daily substitution sheet.  Reuses the Phase 2/4 made-up college (6 periods a day, lunch 13:10-13:40)."""
from __future__ import annotations

import io
import uuid
from datetime import date

from app.services import adjust as adjsvc
from app.services import exporter as ex
from app.services import hours as hr
from app.services import substitution as subsvc
from app.services import views as vw
from app.services.changes import EMPTY, Absence, Adjustment, DayChanges, Sub

from .test_p4_engine import FRI_, Fake, cal_entry, fake_with_lectures
from .test_p2_engine import MON, THU, TUE, WED

U = uuid.uuid4
MONDAY = date(2026, 10, 12)       # a Monday inside the fake term
TUESDAY = date(2026, 10, 13)


def monday_cells(f, who_rows, *, teacher_id=None, across, change, sem_ids=None):
    data = f.data()
    return data, vw.build_cells(
        data, who_rows(data), across_classes=across, teacher_id=teacher_id, sem_ids=sem_ids,
        day=1, dates={1: MONDAY}, with_extras=True, change=change,
    )


# ====================================================================================================
# 0) the DayChanges data model
# ====================================================================================================
def test_daychanges_indexes_absence_adjustment_substitution_and_covering():
    t, sub_t, sem, subj = U(), U(), U(), U()
    ab = Absence(U(), t, MONDAY, "sick", "self")
    adj = Adjustment(U(), MONDAY, "move", frozenset({sem}), 2, 1, 4, frozenset({subj}), None, "Maths", t, None, None, None)
    s_open = Sub(U(), ab.id, MONDAY, 3, 1, frozenset({sem}), frozenset({subj}), None, "Maths", t, "open", None, None, None, None)
    s_filled = Sub(U(), ab.id, MONDAY, 5, 1, frozenset({sem}), frozenset({subj}), None, "Phys", t, "filled", "auto", sub_t, None, None)
    s_cxl = Sub(U(), ab.id, MONDAY, 6, 1, frozenset({sem}), frozenset({subj}), None, "Gone", t, "cancelled", None, None, None, None)
    ch = DayChanges(absences=[ab], adjustments=[adj], subs=[s_open, s_filled, s_cxl])
    assert ch.is_absent(t, MONDAY) and not ch.is_absent(t, TUESDAY)
    assert ch.absence(t, MONDAY).reason == "sick"
    assert ch.adjustment_for(MONDAY, {sem}, 2) is adj and ch.adjustment_for(MONDAY, {sem}, 3) is None
    assert ch.adjustment_for(MONDAY, {U()}, 2) is None                       # different class
    assert [s.id for s in ch.subs_at(MONDAY, 3, {t})] == [s_open.id]
    assert ch.subs_at(MONDAY, 3, {U()}) == []                                # not that absent teacher
    assert [s.id for s in ch.subs_on(MONDAY)] == [s_open.id, s_filled.id]     # the cancelled one is dropped
    assert [s.id for s in ch.covering(sub_t, MONDAY)] == [s_filled.id]


# ====================================================================================================
# 1) the day overlay on the published timetable
# ====================================================================================================
def test_a_filled_substitution_shows_the_substitute_on_class_teacher_and_covering_views():
    f = fake_with_lectures()
    w = f.w
    sub = Sub(U(), U(), MONDAY, 1, 1, frozenset({w.A}), frozenset({w.s["maths"].id}), None, "Maths",
              w.t["maths"], "filled", "manual", w.t["phys"], None, None)
    change = DayChanges(absences=[Absence(U(), w.t["maths"], MONDAY, None, "self")], subs=[sub])
    # class view: the maths cell is annotated with the absent teacher + substitute
    data, cells = monday_cells(f, lambda d: d.rows_of_class(w.A), across=False, sem_ids={w.A}, change=change)
    mcell = next(c for c in cells if c["items"][0]["title"] == "Maths")
    assert mcell["substitutions"][0]["absent_name"] == "Maths Sir" and mcell["substitutions"][0]["substitute_name"] == "Physics Madam"
    assert mcell["items"][0]["absent"] is True and mcell["items"][0]["substitute_name"] == "Physics Madam"
    # the substitute's own day gets a "covering" cell (it is not in their published rows)
    _d2, pcells = monday_cells(f, lambda d: d.rows_of_teacher(w.t["phys"]), teacher_id=w.t["phys"], across=True, change=change)
    cover = [c for c in pcells if c["kind"] == "substitution"]
    assert len(cover) == 1 and cover[0]["items"][0]["title"] == "Maths" and cover[0]["substitution"]["for_name"] == "Maths Sir"
    assert cover[0]["start_period"] == 1
    # the absent teacher's own day shows the lecture as covered (mine_absent)
    _d3, acells = monday_cells(f, lambda d: d.rows_of_teacher(w.t["maths"]), teacher_id=w.t["maths"], across=True, change=change)
    acell = next(c for c in acells if c["items"][0]["title"] == "Maths")
    assert acell.get("mine_absent") is True and acell["substitutions"][0]["status"] == "filled"


def test_an_open_substitution_marks_needs_cover_but_adds_no_covering_cell():
    f = fake_with_lectures()
    w = f.w
    sub = Sub(U(), U(), MONDAY, 1, 1, frozenset({w.A}), frozenset({w.s["maths"].id}), None, "Maths", w.t["maths"], "open", None, None, None, None)
    change = DayChanges(absences=[Absence(U(), w.t["maths"], MONDAY, None, "self")], subs=[sub])
    data, cells = monday_cells(f, lambda d: d.rows_of_class(w.A), across=False, sem_ids={w.A}, change=change)
    mcell = next(c for c in cells if c["items"][0]["title"] == "Maths")
    assert mcell["substitutions"][0]["status"] == "open" and mcell["substitutions"][0]["substitute_name"] is None
    assert vw.day_extra_stats(cells)["needs_sub"] == 1 and vw.day_extra_stats(cells)["covering"] == 0


def test_a_one_day_move_relocates_the_cell_and_a_cancel_marks_it_cancelled():
    f = fake_with_lectures()
    w = f.w
    moved = Adjustment(U(), MONDAY, "move", frozenset({w.A}), 1, 1, 3, frozenset({w.s["maths"].id}), None, "Maths", w.t["maths"], None, None, None)
    data, cells = monday_cells(f, lambda d: d.rows_of_class(w.A), across=False, sem_ids={w.A}, change=DayChanges(adjustments=[moved]))
    mcell = next(c for c in cells if c["items"][0]["title"] == "Maths")
    assert mcell["start_period"] == 3 and mcell["moved_from"] == 1 and mcell["adjusted"]["kind"] == "move" and mcell["status"] is None
    assert mcell["time"] == "12:10-13:10"
    cancelled = Adjustment(U(), MONDAY, "cancel", frozenset({w.A}), 1, 1, None, frozenset({w.s["maths"].id}), None, "Maths", w.t["maths"], None, None, None)
    data, cells = monday_cells(f, lambda d: d.rows_of_class(w.A), across=False, sem_ids={w.A}, change=DayChanges(adjustments=[cancelled]))
    mcell = next(c for c in cells if c["items"][0]["title"] == "Maths")
    assert mcell["status"] == "cancelled" and mcell["adjusted"]["kind"] == "cancel"
    assert vw.day_extra_stats(cells)["cancelled_today"] == 1


def test_change_none_is_identical_to_the_published_timetable():
    f = fake_with_lectures()
    w = f.w
    data = f.data()
    plain = vw.build_cells(data, data.rows_of_class(w.A), across_classes=False, sem_ids={w.A}, dates={1: MONDAY}, day=1, with_extras=True)
    overlaid = vw.build_cells(data, data.rows_of_class(w.A), across_classes=False, sem_ids={w.A}, dates={1: MONDAY}, day=1, with_extras=True, change=EMPTY)
    assert plain == overlaid                                                  # an empty change changes nothing


# ====================================================================================================
# 2) affected lectures
# ====================================================================================================
def test_affected_lectures_lists_the_teachers_scheduled_slots_and_respects_holidays_and_co_teachers():
    f = fake_with_lectures()
    w = f.w
    data = f.data()
    got = subsvc.affected_lectures(data, w.t["maths"], MONDAY)
    assert [(s.start, s.label) for s in got] == [(1, "Maths")]               # Monday: only the P1 maths lecture
    assert subsvc.affected_lectures(data, w.t["maths"], date(2026, 10, 14))[0].start == 1   # Wednesday also P1
    # a co-taught lecture with the co-teacher present is "covered by colleague"
    w.put(w.dA, "co", TUE, 1)                                                 # "Co Subject" = maths teacher + co teacher
    data = f.data()
    tue_slot = next(s for s in subsvc.affected_lectures(data, w.t["maths"], TUESDAY) if s.label == "Co Subject")
    assert tue_slot.covered_by_colleague and w.t["co"] in tue_slot.present_others
    # a full-day holiday removes the slot entirely
    f.calendar.append(cal_entry(school_id=f.school, title="Holiday", date_from=MONDAY, date_to=MONDAY))
    assert subsvc.affected_lectures(f.data(), w.t["maths"], MONDAY) == []


def test_affected_lectures_of_a_combined_session_is_one_slot_across_its_classes():
    f = Fake()
    w = f.w
    k = w.lock(w.A, MON, 5, kind="PE", teacher="pe")
    w.lock(w.B, MON, 5, kind="PE", teacher="pe", key=k)
    slots = subsvc.affected_lectures(f.data(), w.t["pe"], MONDAY)
    assert len(slots) == 1 and slots[0].semester_ids == frozenset({w.A, w.B}) and slots[0].kind == "fixed"


# ====================================================================================================
# 3) is a teacher free?  and the ranking
# ====================================================================================================
def test_free_at_respects_busy_absent_visiting_and_the_days_changes():
    f = fake_with_lectures()
    w = f.w
    data = f.data()
    # phys teaches Tuesday P2 only -> free Monday P1, busy Tuesday P2
    assert subsvc.free_at(data, EMPTY, w.t["phys"], MONDAY, 1, 1)[0] is True
    assert subsvc.free_at(data, EMPTY, w.t["phys"], TUESDAY, 2, 1)[0] is False
    # absent that day -> not free
    ch = DayChanges(absences=[Absence(U(), w.t["phys"], MONDAY, None, "self")])
    assert subsvc.free_at(data, ch, w.t["phys"], MONDAY, 1, 1) == (False, "absent that day")
    # already covering another lecture that day (a filled sub) -> busy there
    cover = Sub(U(), U(), MONDAY, 1, 1, frozenset({w.A}), frozenset({w.s["maths"].id}), None, "Maths", w.t["maths"], "filled", "auto", w.t["phys"], None, None)
    assert subsvc.free_at(data, DayChanges(subs=[cover]), w.t["phys"], MONDAY, 1, 1)[0] is False
    # a visiting teacher only on their days (World: visiting Mon-Wed); 15 Oct 2026 is a Thursday
    assert subsvc.free_at(data, EMPTY, w.t["visit"], date(2026, 10, 15), 1, 1) == (False, "visiting faculty - does not come that day")


def test_ranking_excludes_the_absent_and_busy_and_orders_by_subject_class_and_load():
    f = fake_with_lectures()
    w = f.w
    data = f.data()
    sub = Sub(U(), None, MONDAY, 1, 1, frozenset({w.A}), frozenset({w.s["maths"].id}), None, "Maths", w.t["maths"], "open", None, None, None, None)
    change = DayChanges(absences=[Absence(U(), w.t["maths"], MONDAY, None, "self"), Absence(U(), w.t["lab"], MONDAY, None, "self")])
    ranked = subsvc.rank_candidates(
        data, change, sub,
        can_teach={w.t["phys"]: {w.s["maths"].id}},                          # physics teacher may teach maths
        taught_class={w.t["b1"]: {w.A}},                                     # b1 has taught class A
        specialization={w.t["ai"]: "Maths and statistics"},                  # ai's specialization mentions maths
    )
    ids = [c.teacher_id for c in ranked]
    assert w.t["maths"] not in ids                                           # the absent subject teacher is never a candidate
    assert w.t["lab"] not in ids                                             # absent that day
    top = ranked[0]
    assert top.teacher_id == w.t["phys"] and top.can_teach and "Can teach this subject" in top.reasons
    ai = next(c for c in ranked if c.teacher_id == w.t["ai"])
    assert any("Specialization matches" in r for r in ai.reasons)
    assert all("Free in that slot" in c.reasons for c in ranked)
    # a limit returns only the top few
    assert len(subsvc.rank_candidates(data, change, sub, can_teach={w.t["phys"]: {w.s["maths"].id}}, limit=2)) == 2


# ====================================================================================================
# 4) one-day adjustments: clash checks + suggestions
# ====================================================================================================
def _two_lecture_monday():
    f = Fake()
    w = f.w
    w.put(w.dA, "maths", MON, 1)
    w.put(w.dA, "phys", MON, 2)
    return f, w


def test_can_move_checks_class_and_teacher_free_and_valid_slot():
    f, w = _two_lecture_monday()
    data = f.data()
    ok, _ = adjsvc.can_move(data, EMPTY, sem_ids=[w.A], d=MONDAY, orig_start=1, length=1, new_start=3, teachers={w.t["maths"]})
    assert ok
    busy, why = adjsvc.can_move(data, EMPTY, sem_ids=[w.A], d=MONDAY, orig_start=1, length=1, new_start=2, teachers={w.t["maths"]})
    assert not busy and "already has a lecture" in why
    off, why = adjsvc.can_move(data, EMPTY, sem_ids=[w.A], d=MONDAY, orig_start=1, length=1, new_start=9, teachers={w.t["maths"]})
    assert not off and "only has" in why
    same, why = adjsvc.can_move(data, EMPTY, sem_ids=[w.A], d=MONDAY, orig_start=1, length=1, new_start=1, teachers={w.t["maths"]})
    assert not same and "current period" in why


def test_can_move_blocks_when_the_teacher_is_busy_in_another_class():
    f = Fake()
    w = f.w
    w.put(w.dA, "maths", MON, 1)                      # maths teacher, class A, P1
    w.put(w.dB, "b_maths", MON, 3)                    # the SAME maths teacher, class B, P3 (World: b_maths -> maths teacher)
    data = f.data()
    ok, why = adjsvc.can_move(data, EMPTY, sem_ids=[w.A], d=MONDAY, orig_start=1, length=1, new_start=3, teachers={w.t["maths"]})
    assert not ok and "teaching another class" in why


def test_suggest_moves_and_swaps_are_clash_free():
    f, w = _two_lecture_monday()
    data = f.data()
    moves = adjsvc.suggest_moves(data, EMPTY, sem_ids=[w.A], d=MONDAY, orig_start=1, length=1, teachers={w.t["maths"]})
    starts = {m["new_start"] for m in moves}
    assert 2 not in starts and 1 not in starts and {3, 4, 5, 6} <= starts
    assert all(m["kind"] == "move" and "free" in " ".join(m["reasons"]).lower() for m in moves)
    swaps = adjsvc.suggest_swaps(data, EMPTY, sem_id=w.A, d=MONDAY, orig_start=1, length=1, teachers={w.t["maths"]})
    assert [s["swap_with_start"] for s in swaps] == [2] and "Phys" in swaps[0]["with_label"]


def test_a_lab_block_cannot_swap_with_a_single_period():
    f = Fake()
    w = f.w
    w.put(w.dA, "maths", MON, 1)
    w.put(w.dA, "chem_lab", MON, 3)                  # a 2-period lab
    data = f.data()
    swaps = adjsvc.suggest_swaps(data, EMPTY, sem_id=w.A, d=MONDAY, orig_start=1, length=1, teachers={w.t["maths"]})
    assert swaps == []                                # different lengths - no swap offered


# ====================================================================================================
# 5) cancelled lectures reduce the hours (Phase 4 tracking, Phase 5 input)
# ====================================================================================================
def test_a_cancelled_lecture_counts_as_lost_in_the_hours():
    f = fake_with_lectures()
    w = f.w
    data = f.data()
    base = next(h for h in hr.compute_hours(data, date(2026, 10, 12), {w.A})[w.A] if h.subject.name == "Maths")
    cancelled = {(w.A, date(2026, 10, 12), 1)}        # cancel the Monday 12 Oct maths lecture
    after = next(h for h in hr.compute_hours(data, date(2026, 10, 12), {w.A}, cancelled=cancelled)[w.A] if h.subject.name == "Maths")
    assert after.scheduled == base.scheduled - 1 and after.lost == base.lost + 1


# ====================================================================================================
# 6) the daily substitution sheet
# ====================================================================================================
def test_substitution_sheet_pdf_lists_the_day_and_who_covers():
    f = fake_with_lectures()
    w = f.w
    data = f.data()
    rows = [
        {"start_period": 1, "length": 1, "label": "Maths", "semester_ids": [w.A], "status": "filled", "mode": "manual", "absent_name": "Maths Sir", "sub_name": "Physics Madam"},
        {"start_period": 2, "length": 1, "label": "Physics", "semester_ids": [w.A], "status": "unfilled", "mode": None, "absent_name": "Physics Madam", "sub_name": None},
    ]
    raw = ex.substitution_sheet_pdf(data, date(2026, 10, 12), rows)
    from pypdf import PdfReader

    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(raw)).pages)
    assert raw[:5] == b"%PDF-"
    for needle in ("Daily Substitution Sheet", "Monday, 12 October 2026", "Maths Sir", "Physics Madam", "NOT COVERED"):
        assert needle in text, needle


def test_an_empty_sheet_is_still_a_valid_pdf():
    f = fake_with_lectures()
    raw = ex.substitution_sheet_pdf(f.data(), date(2026, 10, 12), [])
    from pypdf import PdfReader

    assert raw[:5] == b"%PDF-" and "nothing to substitute" in "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(raw)).pages)
