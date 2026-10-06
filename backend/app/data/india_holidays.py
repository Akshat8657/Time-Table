"""Standard India holiday list (official Central Government gazetted holidays) for 2026 and 2027.

2026: DoPT order F.No.12/2/2023-JCA dated 03-Jul-2025      2027: DoPT order dated 16-Jul-2026
Festival dates follow the lunar calendar - the admin can edit or delete any entry.
Must stay in sync with the seed in migrations/002_phase1.sql.
"""
from __future__ import annotations

from datetime import date

_RAW: list[tuple[str, str]] = [
    ("Republic Day", "2026-01-26"),
    ("Holi", "2026-03-04"),
    ("Id-ul-Fitr", "2026-03-21"),
    ("Ram Navami", "2026-03-26"),
    ("Mahavir Jayanti", "2026-03-31"),
    ("Good Friday", "2026-04-03"),
    ("Buddha Purnima", "2026-05-01"),
    ("Id-ul-Zuha (Bakrid)", "2026-05-27"),
    ("Muharram", "2026-06-26"),
    ("Independence Day", "2026-08-15"),
    ("Milad-un-Nabi (Id-e-Milad)", "2026-08-26"),
    ("Janmashtami", "2026-09-04"),
    ("Mahatma Gandhi Jayanti", "2026-10-02"),
    ("Dussehra (Vijaya Dashami)", "2026-10-20"),
    ("Diwali (Deepavali)", "2026-11-08"),
    ("Guru Nanak Jayanti", "2026-11-24"),
    ("Christmas Day", "2026-12-25"),
    ("Republic Day", "2027-01-26"),
    ("Id-ul-Fitr", "2027-03-10"),
    ("Holi", "2027-03-23"),
    ("Good Friday", "2027-03-26"),
    ("Ram Navami", "2027-04-15"),
    ("Mahavir Jayanti", "2027-04-19"),
    ("Id-ul-Zuha (Bakrid)", "2027-05-17"),
    ("Buddha Purnima", "2027-05-20"),
    ("Muharram", "2027-06-16"),
    ("Independence Day", "2027-08-15"),
    ("Milad-un-Nabi (Id-e-Milad)", "2027-08-15"),
    ("Janmashtami", "2027-08-25"),
    ("Mahatma Gandhi Jayanti", "2027-10-02"),
    ("Dussehra (Vijaya Dashami)", "2027-10-09"),
    ("Diwali (Deepavali)", "2027-10-29"),
    ("Guru Nanak Jayanti", "2027-11-14"),
    ("Christmas Day", "2027-12-25"),
]

INDIA_HOLIDAYS: list[tuple[str, date]] = [(title, date.fromisoformat(d)) for title, d in _RAW]


def note_for(d: date) -> str:
    return f"Official gazetted holiday {d.year} (DoPT). Festival dates follow the lunar calendar - check locally."
