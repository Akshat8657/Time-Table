"""Turns the data the existing services already produce (view cells, free-teacher rows, profile rows) into the small, flat
"blocks" the chat screen draws.  Pure Python - no database - so every shape here is unit-tested.

The chat never shows text written by the AI model for data: the numbers, names and times in a block come from our own
services, which read the live database with the signed-in person's permissions.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable


def say(hinglish: bool, english: str, hindi: str) -> str:
    return hindi if hinglish else english


# ---------------------------------------------------------------------------------------------------- timetable cells
def _unique(seq: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def cell_title(cell: dict[str, Any]) -> str:
    titles = _unique(i.get("title") or "" for i in cell.get("items", []))
    if len(titles) > 1:
        return "Elective: " + " / ".join(titles)
    return titles[0] if titles else "Lecture"


def cell_teachers(cell: dict[str, Any]) -> str:
    names: list[str] = []
    for it in cell.get("items", []):
        who = [n for n in (it.get("teacher_name"), it.get("co_teacher_name")) if n]
        if it.get("absent") and it.get("substitute_name"):
            who = [f"{it['substitute_name']} (substitute for {it.get('absent_teacher_name') or 'absent teacher'})"]
        elif it.get("absent"):
            who = [f"{n} (absent)" if n == it.get("absent_teacher_name") else n for n in who]
        names += who
    return ", ".join(_unique(names))


def cell_tags(cell: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    if cell.get("is_lab"):
        tags.append("Lab")
    if cell.get("fixed_kind"):
        tags.append(str(cell["fixed_kind"]))
    elif cell.get("kind") == "fixed":
        tags.append("Fixed")
    if cell.get("kind") == "extra":
        tags.append("Extra lecture")
    if cell.get("combined"):
        tags.append("Combined")
    if cell.get("parallel") and len(_unique(i.get("title") or "" for i in cell.get("items", []))) > 1:
        tags.append("Parallel")
    if cell.get("status"):
        tags.append(str(cell["status"]).replace("_", " ").capitalize() + (f": {cell['status_note']}" if cell.get("status_note") else ""))
    if cell.get("mine_absent"):
        tags.append("Absent")
    sub = cell.get("substitution")
    if sub and sub.get("for_name"):
        tags.append(f"Covering for {sub['for_name']}")
    if cell.get("moved_from"):
        tags.append(f"Moved from period {cell['moved_from']}")
    return tags


def short_cell(cell: dict[str, Any], *, show_classes: bool) -> dict[str, Any]:
    status = cell.get("status")
    return {
        "day": cell["day"], "start": cell["start_period"], "length": cell["length"], "time": cell.get("time"),
        "title": cell_title(cell), "teachers": cell_teachers(cell),
        "classes": [c["label"] for c in cell.get("classes", [])] if show_classes else [],
        "tags": cell_tags(cell),
        "tone": "off" if status else ("lab" if cell.get("is_lab") else ("fixed" if cell.get("kind") == "fixed" else "normal")),
    }


def timetable_block(
    *, title: str, mode: str, grid: dict[str, Any], days: list[dict[str, Any]], cells: list[dict[str, Any]],
    show_classes: bool, published: bool, version: int | None = None, notes: list[str] | None = None,
    empty_text: str = "No lectures.", only_day: int | None = None,
) -> dict[str, Any]:
    """mode 'day' -> a list of that day's lectures; 'week' -> a Monday-to-Saturday table."""
    keep = [c for c in cells if only_day is None or c["day"] == only_day]
    shown_days = [d for d in days if only_day is None or d["day"] == only_day]
    lunch = grid.get("lunch")
    return {
        "type": "timetable", "mode": mode, "title": title,
        "periods": [{"number": p["number"], "time": f"{p['start']}-{p['end']}"} for p in grid.get("periods", [])],
        "lunch": f"{lunch['start']}-{lunch['end']}" if lunch else None,
        "days": [
            {"day": d["day"], "name": d["name"], "short": d["short"], "date": d["date"], "today": bool(d.get("is_today")),
             "notes": [b["title"] for b in d.get("banners", [])]}
            for d in shown_days
        ],
        "cells": [short_cell(c, show_classes=show_classes) for c in keep],
        "published": published, "version": version, "notes": notes or [], "empty_text": empty_text,
    }


# ---------------------------------------------------------------------------------------------------- free teachers
def free_block(
    res: dict[str, Any], rows: list[dict[str, Any]], *, managers: bool, multi_school: bool, cap: int = 60,
) -> dict[str, Any]:
    """`res` = the free-teacher report; `rows` = its teacher rows (already filtered to schools that have a published timetable).
    Asked about ONE period (or a run of periods) each teacher shows just those periods; asked about the whole day, all their free
    periods.  A teacher gets no information about who marked a period 'not available' - that is for HOD / admin only."""
    teachers: list[dict[str, Any]] = []
    only: set[int] | None = None
    if res.get("period"):
        only = set(range(res["period"], res["period"] + (res.get("length") or 1)))
    for r in rows[:cap]:
        free = [{"period": f["period"], "time": f["time"]} for f in r.get("free_periods", []) if only is None or f["period"] in only]
        item = {
            "name": r["name"], "specialization": r.get("specialization") or "", "role": r.get("role") or "Teacher",
            "school": r.get("school") if multi_school else "",
            "free": free, "free_count": len(free),
        }
        if managers:
            item["prefers_not"] = bool(r.get("marked_unavailable"))
        teachers.append(item)
    first = (res.get("schools") or [{}])[0]
    return {
        "type": "free_teachers", "date": res.get("date"), "period": res.get("period"), "time": first.get("time"),
        "length": res.get("length"), "now": bool(res.get("now")),
        "periods": first.get("periods", []), "teachers": teachers, "total": len(rows), "shown": len(teachers),
        "notes": list(res.get("notes", [])),
    }


# ---------------------------------------------------------------------------------------------------- small blocks
def facts_block(title: str, rows: list[tuple[str, Any]]) -> dict[str, Any]:
    return {"type": "facts", "title": title, "rows": [{"label": k, "value": "" if v is None else str(v)} for k, v in rows if v not in (None, "")]}


def list_block(title: str, items: list[dict[str, Any]], empty_text: str = "") -> dict[str, Any]:
    return {"type": "list", "title": title, "items": items, "empty_text": empty_text}


def note_block(text: str, tone: str = "info") -> dict[str, Any]:
    return {"type": "note", "tone": tone, "text": text}


def day_label(d: date) -> str:
    return d.strftime("%a %d %b %Y")
