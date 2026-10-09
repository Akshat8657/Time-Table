"""Publishing (plan Section 19, Phase 4) - pure logic, no database, no web.

  snapshot_class()    the rows that will be published for one class: its primary draft + the locked slots + the teachers NOW
  fingerprint()       a comparable signature of a set of rows ("is the live timetable still equal to the draft?")
  diff_rows()         what was added / removed between two sets of rows (the "unpublished changes" indicator)
  teacher_changes()   per teacher: which of THEIR lectures were added / removed (who must be notified, and about what)
  teacher_clashes()   is the published state free of teacher double-bookings?  (a class may be published on its own)

A published timetable is a SNAPSHOT: later edits to a draft, to the assignments or to the locked slots change nothing
that teachers see until the HOD publishes again.
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from ..validation import DAY_SHORT
from . import schedule as sch


@dataclass(frozen=True)
class PubRow:
    id: uuid.UUID
    semester_id: uuid.UUID
    kind: str                       # 'lecture' | 'fixed'
    subject_id: uuid.UUID | None
    fixed_kind: str | None          # 'PE' | 'SOL' | 'OTHER' for kind == 'fixed'
    label: str
    day: int
    start: int
    length: int
    group_key: uuid.UUID | None     # parallel electives / classes of one combined session
    teacher_id: uuid.UUID | None
    co_teacher_id: uuid.UUID | None
    combined_with: str | None = None

    @property
    def key(self) -> uuid.UUID:
        return self.group_key or self.id

    @property
    def teachers(self) -> tuple[uuid.UUID, ...]:
        return tuple(t for t in (self.teacher_id, self.co_teacher_id) if t is not None)

    @property
    def identity(self) -> tuple:
        """What a teacher recognises as 'the same lecture': when it is, and what it is."""
        return (self.day, self.start, self.length, str(self.subject_id) if self.subject_id else self.label)


def row_from_record(r: Mapping[str, Any]) -> PubRow:
    return PubRow(
        r["id"], r["semester_id"], r["kind"], r["subject_id"], r["fixed_kind"], r["label"], r["day"], r["start_period"],
        r["length"], r["group_key"], r["teacher_id"], r["co_teacher_id"], r["combined_with"],
    )


# ---------------------------------------------------------------------------------------------------- the snapshot
def snapshot_class(
    ctx: sch.Ctx, draft_id: uuid.UUID | None, sem_id: uuid.UUID, new_id: Callable[[], uuid.UUID] = uuid.uuid4
) -> list[PubRow]:
    """The rows to publish for one class, taken from the loaded context (primary draft, locked slots, current teachers)."""
    rows: list[PubRow] = []
    for e in ctx.entries_by_draft.get(draft_id, []) if draft_id else []:
        s = ctx.subjects[e.subject_id]
        t, co = ctx.assign.get(s.id, (None, None))
        rows.append(PubRow(new_id(), sem_id, "lecture", s.id, None, s.name, e.day, e.start, e.length, e.parallel_key, t, co))
    own = ctx.labels.get(sem_id, "")
    seen: set[uuid.UUID] = set()
    for (sid, _day), fixed_rows in sorted(ctx.fixed_by_class.items(), key=lambda kv: kv[0][1]):
        if sid != sem_id:
            continue
        for f in fixed_rows:
            if f.id in seen:
                continue
            seen.add(f.id)
            others = sorted(c for c in ctx.fixed_classes.get(f.key, []) if c != own)
            rows.append(PubRow(
                new_id(), sem_id, "fixed", f.subject_id, f.kind, f.label, f.day, f.start, f.length,
                f.key if others else None, f.teacher_id, f.co_teacher_id, " + ".join(others) if others else None,
            ))
    return sorted(rows, key=lambda r: (r.day, r.start, r.label))


# ---------------------------------------------------------------------------------------------------- comparing
def fp_of(r: PubRow) -> tuple:
    return (r.kind, r.subject_id, r.fixed_kind, r.label, r.day, r.start, r.length, r.teacher_id, r.co_teacher_id, r.combined_with)


def fingerprint(rows: Iterable[PubRow]) -> Counter:
    return Counter(fp_of(r) for r in rows)


def diff_rows(old: Iterable[PubRow], new: Iterable[PubRow]) -> tuple[list[PubRow], list[PubRow]]:
    """(added, removed) between two sets of rows of one class."""
    old, new = list(old), list(new)
    fo, fn = fingerprint(old), fingerprint(new)
    by_fp_new = {fp_of(r): r for r in new}
    by_fp_old = {fp_of(r): r for r in old}
    added = [by_fp_new[fp] for fp, n in (fn - fo).items() for _ in range(n)]
    removed = [by_fp_old[fp] for fp, n in (fo - fn).items() for _ in range(n)]
    key = lambda r: (r.day, r.start, r.label)  # noqa: E731
    return sorted(added, key=key), sorted(removed, key=key)


def describe_row(r: PubRow, class_label: str, time_text: str | None = None) -> str:
    when = f"{DAY_SHORT.get(r.day, r.day)} period {r.start}" + (f"-{r.start + r.length - 1}" if r.length > 1 else "")
    if time_text:
        when += f" ({time_text})"
    return f"{r.label} - {class_label}, {when}"


# ---------------------------------------------------------------------------------------------------- per teacher
def teacher_lectures(rows: Iterable[PubRow]) -> dict[uuid.UUID, dict[tuple, list[PubRow]]]:
    """For each teacher (primary or co-teacher): their distinct lectures. A combined session counts once."""
    out: dict[uuid.UUID, dict[tuple, list[PubRow]]] = defaultdict(dict)
    for r in rows:
        for t in r.teachers:
            out[t].setdefault(r.identity, []).append(r)
    return out


@dataclass
class TeacherChange:
    teacher_id: uuid.UUID
    added: list[list[PubRow]] = field(default_factory=list)      # each item = the rows of ONE lecture
    removed: list[list[PubRow]] = field(default_factory=list)
    moved: list[tuple[list[PubRow], list[PubRow]]] = field(default_factory=list)   # (from, to)
    total_after: int = 0
    first_time: bool = False                                     # they had no published lecture before

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.moved)


def teacher_changes(old_rows: Iterable[PubRow], new_rows: Iterable[PubRow]) -> dict[uuid.UUID, TeacherChange]:
    """Only teachers whose OWN lectures differ between the old and the new published set appear here."""
    old, new = teacher_lectures(old_rows), teacher_lectures(new_rows)
    changes: dict[uuid.UUID, TeacherChange] = {}
    for t in set(old) | set(new):
        o, n = old.get(t, {}), new.get(t, {})
        added = {k: v for k, v in n.items() if k not in o}
        removed = {k: v for k, v in o.items() if k not in n}
        if not added and not removed:
            continue
        ch = TeacherChange(t, total_after=len(n), first_time=not o)
        # the same subject lecture at another time = "moved"
        subject_key = lambda rows: (rows[0].subject_id or rows[0].label)  # noqa: E731
        free_added = dict(added)
        for k, rows in removed.items():
            match = next((ak for ak, arows in free_added.items() if subject_key(arows) == subject_key(rows)), None)
            if match is not None:
                ch.moved.append((rows, free_added.pop(match)))
            else:
                ch.removed.append(rows)
        ch.added = list(free_added.values())
        changes[t] = ch
    return changes


# ---------------------------------------------------------------------------------------------------- the published state
def teacher_clashes(
    rows: Iterable[PubRow], labels: Mapping[uuid.UUID, str], names: Mapping[uuid.UUID, str]
) -> list[dict[str, Any]]:
    """Teachers who would be in two different places at once in the published state."""
    per_teacher: dict[uuid.UUID, dict[uuid.UUID, PubRow]] = defaultdict(dict)
    for r in rows:
        for t in r.teachers:
            per_teacher[t].setdefault(r.key, r)  # parallel rows / one combined session = ONE place
    out: list[dict[str, Any]] = []
    for t, by_key in per_teacher.items():
        items = sorted(by_key.values(), key=lambda r: (r.day, r.start))
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if b.day != a.day:
                    break
                if sch.overlap(a.start, a.length, b.start, b.length):
                    who = names.get(t, "A teacher")
                    out.append({
                        "teacher_id": t, "teacher": who, "semesters": {a.semester_id, b.semester_id},
                        "message": (
                            f"{who} would be in two places on {DAY_SHORT.get(a.day)}: '{a.label}' ({labels.get(a.semester_id, 'a class')}, "
                            f"{sch.span(a.start, a.length)}) and '{b.label}' ({labels.get(b.semester_id, 'a class')}, {sch.span(b.start, b.length)})."
                        ),
                    })
    return out
