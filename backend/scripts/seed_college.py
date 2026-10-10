"""Seed the REAL School of IT data (COLLEGE_SEED_DATA.md V4) through the backend API.

  python -m scripts.seed_college            seeds everything (safe to run again: it only creates what is missing)
  python -m scripts.seed_college --verify   checks the result against the seed file and prints a report (changes nothing)

How: the FastAPI app is called IN-PROCESS (no server needed), as the real admin account (no password needed), so every
validation rule and every audit-log entry runs exactly as if the admin had clicked the buttons.
Teacher accounts get a random temporary password; each is written to  seed_accounts.txt  in the project root
(git-ignored, excluded from backups, NEVER printed). Order = Section 9 of the seed file.
"""
from __future__ import annotations

import os

os.environ.setdefault("READ_CACHE_SECONDS", "0")   # these scripts also write straight to the database: always read fresh

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app import db
from app.config import get_settings
from app.main import app
from app.security import deps

ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS_FILE = ROOT / "seed_accounts.txt"
EMAIL_DOMAIN = "school-it.test"

TERM = {"kind": "odd", "year": 2026}
SEM_START, SEM_END = "2026-07-01", "2026-11-30"
FULL = None  # a working day with the college end time
TILL_240 = "14:40"

# ---------------------------------------------------------------------------------------------------- classes (Section 1 / 3)
# key -> (course, semester, {day: "OFF" | end time | None=full})   days: 1 Mon ... 6 Sat
DAYS = {
    "BSC1": {6: "OFF"},
    "BSC3": {3: TILL_240, 4: TILL_240, 5: TILL_240, 6: "OFF"},
    "BSC5": {1: TILL_240, 2: TILL_240, 3: TILL_240, 6: "OFF"},
    "BSC7": {3: TILL_240, 4: "OFF", 5: "OFF", 6: "OFF"},
    "MIT1": {6: "OFF"},
    "MIT3": {6: "OFF"},
    "MAI1": {6: "OFF"},
    "MAI3": {6: "OFF"},
}
CLASSES = {
    "BSC1": ("B.Sc. IT", 1), "BSC3": ("B.Sc. IT", 3), "BSC5": ("B.Sc. IT", 5), "BSC7": ("B.Sc. IT", 7),
    "MIT1": ("M.Sc. IT", 1), "MIT3": ("M.Sc. IT", 3), "MAI1": ("M.Sc. AI", 1), "MAI3": ("M.Sc. AI", 3),
}
NO_PE_SOL = ["BSC5", "BSC7"]

# ---------------------------------------------------------------------------------------------------- teachers (Section 4)
@dataclass
class Person:
    key: str
    name: str
    email: str
    specialization: str
    role: str = "teacher"
    visiting_days: list[int] = field(default_factory=list)


PEOPLE = [
    Person("PAPRI", "Dr. Papri Das", "papri.das", "IoT, Networks, Mathematics, Quantum", role="hod"),
    Person("CHIRAG", "Dr. Chirag Mehta", "chirag.mehta", "Programming (Python, JAVA, Full Stack)"),
    Person("DHAVAL", "Dr. Dhaval Thaker", "dhaval.thaker", "Data Mining, Databases, DevOps, RL"),
    Person("JUHI", "Dr. Juhi Khengar", "juhi.khengar", "Web, OS, Databases"),
    Person("SUNIL", "Dr. Sunil Kumar", "sunil.kumar", "AI, Blockchain, Big Data"),
    Person("SHREYA", "Ms. Shreya Chakrabarty", "shreya.chakrabarty", "Cyber Security, Mobile"),
    Person("MAITRI", "Maitri Hingu", "maitri.hingu", "Software Engineering, Cloud, Quantum, UI/UX"),
    Person("JUHIKADEVI", "Ms. Juhikadevi Bhanjdeo", "juhikadevi.bhanjdeo", "Science of Living"),
    Person("SUBARNA", "Ms. Subarna Ghosh", "subarna.ghosh", "Science of Living"),
    Person("RICHA", "Dr. Richa Chaudhary", "richa.chaudhary", "Psychology/Behavior"),
    Person("SRINITA", "Dr. Srinita Bhattacharjee", "srinita.bhattacharjee", "Communication"),
    Person("NIMESH", "Prof. Nimesh Joshi", "nimesh.joshi", "Indian Culture"),
    Person("SUSHANT", "Mr. Sushant Singh", "sushant.singh", "Physical Education"),
    # V4: "External Faculty" is TWO different people, one per B.Sc. specialization track
    Person("EXT_AI", "External Faculty (AI&ML)", "external.faculty.aiml", "AI & Machine Learning track"),
    Person("EXT_CYBER", "External Faculty (Cyber)", "external.faculty.cyber", "Cyber Security & Forensics track"),
    # visiting faculty: Monday + Tuesday + Wednesday only (hard rule). The seed file gives no weekly-hours limit and the
    # account form requires one -> 18 = three days x six periods, i.e. no limit beyond the three allowed days.
    Person("ANJANA", "Anjana Prajapati", "anjana.prajapati", "AI & Machine Learning, Data Structures & Algorithms", visiting_days=[1, 2, 3]),
    Person("FALGUNI", "Falguni Thakker", "falguni.thakker", "Generative AI, Data Visualization, Digital Image Processing", visiting_days=[1, 2, 3]),
]
VISITING_MAX_HOURS = 18
# before V4 there was ONE account "External Faculty": it is renamed into the AI&ML account (not orphaned)
LEGACY_EXTERNAL_EMAIL = f"external.faculty@{EMAIL_DOMAIN}"
LEGACY_EXTERNAL_KEY = "EXT_AI"

# ---------------------------------------------------------------------------------------------------- subjects (Sections 6-8)
# (code, name, credits, teacher key | None, flags)   flags: lab | elective | sol | pe ; group = track group name
def theory_lab(code: str, name: str, credits: int, teacher: str, **kw) -> list[dict]:
    """A '3 theory + 1 lab block' subject = a theory subject plus its LAB subject (1 credit, one double block a week)."""
    return [
        {"code": code, "name": name, "credits": credits, "teacher": teacher, **kw},
        {"code": f"{code}-LAB", "name": f"{name} LAB", "credits": 1, "teacher": teacher, "lab": True},
    ]


def track(label: str, ai: tuple[str, str], cyber: tuple[str, str], credits: int = 3) -> list[dict]:
    """Track majors (V4): the AI-track and the Cyber-track subject run in the SAME parallel slots (one elective group) and so do
    their two LABS (a second group; the shared lab of V3 is split per track). Everything of the AI track goes to External Faculty
    (AI&ML), everything of the Cyber track to External Faculty (Cyber). `ai` / `cyber` = (short code, subject name)."""
    tag = label.replace(" ", "")
    majors, labs = f"{label} - Track Majors", f"{label} LAB - Track Labs"
    rows: list[dict] = []
    for kind, (short, name), who in (("AI", ai, "EXT_AI"), ("CYBER", cyber, "EXT_CYBER")):
        rows.append({"code": f"{tag}-{short}", "name": name, "credits": credits, "teacher": who, "elective": True, "group": majors, "track": kind})
    for kind, (short, name), who in (("AI", ai, "EXT_AI"), ("CYBER", cyber, "EXT_CYBER")):
        rows.append({
            "code": f"{tag}-{short}-LAB", "name": f"{name} LAB", "credits": 1, "teacher": who, "lab": True, "elective": True, "group": labs,
            "track": kind, "legacy_code": f"{tag}-LAB" if kind == "AI" else None,   # the V3 shared lab becomes the AI-track lab
        })
    return rows


SUBJECTS: dict[str, list[dict]] = {
    "BSC1": [
        *track("Major 1", ("AIML", "Intro to AI & ML with Python"), ("INFOSEC", "Information Security Fundamentals")),
        *theory_lab("MAJ2", "Data Structures & Algorithms using C", 3, "ANJANA"),
        *theory_lab("MIN1", "Fundamentals of DBMS", 3, "JUHI"),
        {"code": "MD1", "name": "Fundamentals of Mathematics", "credits": 2, "teacher": "PAPRI"},
        {"code": "MD2", "name": "Dynamics of Human Behavior", "credits": 2, "teacher": "RICHA"},
        {"code": "AEC1", "name": "Communication Skills", "credits": 2, "teacher": "SRINITA"},
        {"code": "SEC1", "name": "Computer Organization & Architecture", "credits": 2, "teacher": "MAITRI"},
        {"code": "VAC1", "name": "Science of Living I", "credits": 2, "teacher": "DHAVAL", "sol": True},
        {"code": "PE1", "name": "PE, Sports & Yoga - I", "credits": 1, "teacher": "SUSHANT", "pe": True},
    ],
    "BSC3": [
        *track("Major 5", ("DL", "Deep Learning"), ("ITADS", "IT Application & Data Security")),
        *theory_lab("MAJ6", "OOP using JAVA", 3, "CHIRAG"),
        {"code": "MAJ7", "name": "Operating Systems", "credits": 4, "teacher": "JUHI"},
        {"code": "SEC3", "name": "Data Visualization", "credits": 1, "teacher": "FALGUNI"},
        {"code": "SEC3-LAB", "name": "Data Visualization LAB", "credits": 1, "teacher": "FALGUNI", "lab": True},
        {"code": "MD5", "name": "Computer Networks", "credits": 2, "teacher": "PAPRI"},
        {"code": "MD6", "name": "Digital Literacy", "credits": 2, "teacher": "SHREYA"},
        {"code": "AEC3", "name": "Communication Skills / French / Sanskrit / Gujarati / Hindi", "credits": 2, "teacher": None},   # unassigned for now
        {"code": "VAC3", "name": "Science of Living III", "credits": 2, "teacher": "SUBARNA", "sol": True},
        {"code": "PE3", "name": "PE, Sports & Yoga - III", "credits": 1, "teacher": "SUSHANT", "pe": True},
    ],
    "BSC5": [
        *track("Major 11", ("PRAD", "Pattern Recognition & Anomaly Detection / LLM Dev & Deployment"), ("DF", "Digital Forensics")),
        *theory_lab("MAJ12", "Blockchain Technology", 3, "SUNIL"),
        *theory_lab("MAJ13", "Full Stack Development", 3, "CHIRAG"),
        {"code": "MIN4", "name": "Data Governance & Privacy Engineering", "credits": 4, "teacher": "SHREYA"},
        *theory_lab("MIN5", "Introduction to DevOps", 3, "DHAVAL"),
        {"code": "SEC5", "name": "UI/UX Design", "credits": 1, "teacher": "MAITRI"},
        {"code": "SEC5-LAB", "name": "UI/UX Design LAB", "credits": 1, "teacher": "MAITRI", "lab": True},
        {"code": "VAC5", "name": "Foundation of Indian Culture - I", "credits": 2, "teacher": "NIMESH"},
    ],
    "BSC7": [
        *track("Major 17", ("PRAD", "Pattern Recognition & Anomaly Detection"), ("CWAS", "Cryptography & Web App Security")),
        *theory_lab("MAJ18", "Big Data Analytics", 3, "SUNIL"),
        *theory_lab("MAJ19", "Digital Image Processing", 3, "FALGUNI"),
        {"code": "MIN7", "name": "Introduction to Quantum Computing", "credits": 4, "teacher": "PAPRI", "co_teacher": "MAITRI"},
        # OJT 1 (6 credits) is NOT a subject here: it is the Thu/Fri/Sat OFF days (seed file Section 9, step 8)
    ],
    "MIT1": [
        *theory_lab("ADVWEB", "Advanced Web Development", 3, "JUHI"),
        *theory_lab("NOSQL", "NO SQL", 3, "DHAVAL"),
        {"code": "MOBILE", "name": "Mobile Computing", "credits": 4, "teacher": "SHREYA"},
        *theory_lab("ELEC1", "Network Security", 3, "SHREYA", elective=True),
        *theory_lab("ELEC2", "Advanced Python Programming", 3, "CHIRAG", elective=True),
        {"code": "SOL1", "name": "Science of Living I", "credits": 2, "teacher": "JUHIKADEVI", "sol": True},
        {"code": "PE1", "name": "PE & Yoga - I (Audit)", "credits": 1, "teacher": "SUSHANT", "pe": True},
    ],
    "MIT3": [
        *theory_lab("BIGIOT", "Big Data & IoT", 3, "PAPRI"),
        {"code": "TESTQA", "name": "Software Testing & QA", "credits": 4, "teacher": "MAITRI"},
        *theory_lab("ADVJAVA", "Advance JAVA Programming", 3, "CHIRAG"),
        *theory_lab("ELEC5", "Cyber Security & Digital Forensics", 3, "SHREYA", elective=True),
        *theory_lab("ELEC6", "Blockchain Technology", 3, "SUNIL", elective=True),
        {"code": "SOL3", "name": "Science of Living III", "credits": 2, "teacher": "SUBARNA", "sol": True},
        {"code": "PE3", "name": "PE & Yoga - III (Audit)", "credits": 1, "teacher": "SUSHANT", "pe": True},
        # Internship (4 credits): outside the timetable - no subject, no slots
    ],
    "MAI1": [
        {"code": "AIML", "name": "AI & Machine Learning", "credits": 4, "teacher": "ANJANA"},
        *theory_lab("PYTHON", "Python Programming", 3, "CHIRAG"),
        {"code": "IOT", "name": "Internet of Things for Industry 4.0", "credits": 3, "teacher": "PAPRI"},
        {"code": "IOT-LAB", "name": "IoT LAB", "credits": 1, "teacher": "PAPRI", "lab": True},
        {"code": "RTOS", "name": "Real Time Operating System", "credits": 4, "teacher": "JUHI"},
        *theory_lab("ELEC1", "Data Mining", 3, "DHAVAL", elective=True),
        {"code": "SOL1", "name": "Science of Living I", "credits": 2, "teacher": "JUHIKADEVI", "sol": True},
        {"code": "PE1", "name": "PE, Sports & Yoga - I (Audit)", "credits": 1, "teacher": "SUSHANT", "pe": True},
    ],
    "MAI3": [
        {"code": "RL", "name": "Reinforcement Learning", "credits": 4, "teacher": "DHAVAL"},
        *theory_lab("CLOUD", "Cloud AI and MLOps", 3, "MAITRI"),
        *theory_lab("AGENTIC", "Agentic AI & Autonomous Systems", 3, "SUNIL"),
        {"code": "GENAI", "name": "Generative AI & Foundation Models", "credits": 4, "teacher": "FALGUNI"},
        *theory_lab("ELEC3", "Blockchain Technology", 3, "SUNIL", elective=True),
        {"code": "SOL3", "name": "Science of Living III", "credits": 2, "teacher": "SUBARNA", "sol": True},
        {"code": "PE3", "name": "PE & Yoga - III (Audit)", "credits": 1, "teacher": "SUSHANT", "pe": True},
        # Internship (4 credits): outside the timetable - no subject, no slots
    ],
}
# credits of the seed file that are deliberately NOT subjects (OJT / Internship: no weekly lectures)
NOT_SEEDED_CREDITS = {"BSC7": 6, "MIT3": 4, "MAI3": 4}
SEED_CREDITS = {"BSC1": 23, "BSC3": 23, "BSC5": 24, "BSC7": 22, "MIT1": 23, "MIT3": 27, "MAI1": 23, "MAI3": 27}

# ---------------------------------------------------------------------------------------------------- fixed slots (Section 5)
# (what, kind, day, start period, covered classes)   Mon=1 ... ; every one is a double block
SLOTS = [
    ("SOL I (B.Sc.)", "SOL", 1, 5, ["BSC1"]),
    ("PE I (B.Sc.)", "PE", 2, 5, ["BSC1"]),
    # PE Monday: B.Sc. Sem 3 PE and the M.Sc. PE are BOTH Monday P5+P6 with the same teacher -> ONE combined session (seed note)
    ("PE Monday (combined)", "PE", 1, 5, ["BSC3", "MIT1", "MIT3", "MAI1", "MAI3"]),
    ("SOL I (M.Sc.)", "SOL", 3, 5, ["MIT1", "MAI1"]),
    # SOL III: B.Sc. Sem 3 and the M.Sc. SOL III are BOTH Thursday P3+P4 with Ms. Subarna Ghosh -> one teacher cannot be in two
    # places, so this is ONE combined session of the three classes (decision of this seed run - see the seed report)
    ("SOL III (B.Sc. + M.Sc.)", "SOL", 4, 3, ["BSC3", "MIT3", "MAI3"]),
]


# ---------------------------------------------------------------------------------------------------- in-process API client
class SeedError(RuntimeError):
    pass


class Api:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.c = client

    async def call(self, method: str, path: str, ok: tuple[int, ...] = (200, 201), **kw) -> Any:
        r = await self.c.request(method, path, **kw)
        if r.status_code not in ok:
            try:
                msg = r.json().get("message", r.text)
            except Exception:  # noqa: BLE001
                msg = r.text
            raise SeedError(f"{method} {path} -> {r.status_code}: {msg}")
        return r.json() if r.content else None

    get = lambda self, p, **kw: self.call("GET", p, **kw)  # noqa: E731
    post = lambda self, p, body=None, **kw: self.call("POST", p, json=body, **kw)  # noqa: E731
    put = lambda self, p, body=None, **kw: self.call("PUT", p, json=body, **kw)  # noqa: E731
    patch = lambda self, p, body=None, **kw: self.call("PATCH", p, json=body, **kw)  # noqa: E731


class Who:
    """Which account the fake sign-in returns (the real admin; the verify step also looks at the screens of the HOD)."""
    user: Any = None


async def load_user(email: str | None = None, role: str | None = None):
    row = await db.fetchrow(
        """
        select p.id, p.email, p.full_name, p.role, p.school_id, s.name as school_name, p.specialization, p.phone, p.photo_url, p.is_visiting, p.is_active
        from public.profiles p left join public.schools s on s.id = p.school_id
        where ($1::text is null or p.email = $1) and ($2::text is null or p.role = $2) order by p.created_at limit 1
        """,
        email, role,
    )
    if row is None:
        raise SeedError(f"No {role or ''} account {email or ''} found.")
    now = datetime.now(timezone.utc)
    return deps.CurrentUser(
        id=str(row["id"]), email=row["email"], full_name=row["full_name"], role=row["role"],
        school_id=str(row["school_id"]) if row["school_id"] else None, school_name=row["school_name"],
        specialization=row["specialization"], phone=row["phone"], photo_url=row["photo_url"], is_visiting=row["is_visiting"],
        is_active=row["is_active"], must_change_password=False, session_id="seed-run", remember_me=False,
        last_activity_at=now, absolute_expires_at=now + timedelta(days=1),
    )


async def _fake_authenticate(request, *, allow_pending_password: bool, touch: bool = True):  # noqa: ARG001
    return Who.user


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------------------------------- the seeding steps
class Seeder:
    def __init__(self, api: Api) -> None:
        self.api = api
        self.school_id = ""
        self.sem: dict[str, str] = {}             # class key -> semester id
        self.people: dict[str, str] = {}          # person key -> account id
        self.subjects: dict[tuple[str, str], str] = {}   # (class key, subject code) -> subject id
        self.created_accounts = 0

    # 1) timing --------------------------------------------------------------------------------------------------------
    async def timing(self) -> None:
        college = await self.api.get("/api/timing")
        c = college["college"]
        if c["days_per_week"] != 6 or c["college_start"] != "10:00" or c["college_end"] != "16:50" or c["lecture_minutes"] != 60 or c["break_minutes"] != 5:
            await self.api.put("/api/timing/college", {"college_start": "10:00", "college_end": "16:50", "lecture_minutes": 60, "break_minutes": 5, "days_per_week": 6})
            log("  college timing: 10:00-16:50, 60 min lectures, 5 min breaks, Monday-Saturday")
        await self.api.put("/api/timing/school", {"school_id": self.school_id, "lunch_start": "13:10", "lunch_end": "13:40"})
        log("  School of IT lunch: 13:10-13:40")

    # 2) term + dates ----------------------------------------------------------------------------------------------------
    async def term(self) -> None:
        res = await self.api.put("/api/terms/active", TERM)
        term_id = res["active"]["id"]
        await self.api.put(
            f"/api/terms/{term_id}/dates",
            {"school_id": self.school_id, "items": [{"semester_id": sid, "start_date": SEM_START, "end_date": SEM_END} for sid in self.sem.values()]},
        )
        log(f"  active term: Odd 2026, semester dates {SEM_START} to {SEM_END} for {len(self.sem)} classes")

    # 3) day-wise schedule -------------------------------------------------------------------------------------------------
    async def day_schedules(self) -> None:
        for key, rules in DAYS.items():
            days = []
            for d in range(1, 7):
                rule = rules.get(d, FULL)
                days.append({"day": d, "is_off": True} if rule == "OFF" else {"day": d, "end_time": rule})
            await self.api.put(f"/api/timing/semesters/{self.sem[key]}/days", {"days": days})
        log(f"  day-wise schedules set for {len(DAYS)} classes (Saturday OFF everywhere; Sem 7 Thu-Sat OFF)")

    # 4) teachers ----------------------------------------------------------------------------------------------------------
    async def teachers(self) -> None:
        have = {u["email"]: u for u in (await self.api.get(f"/api/users?school_id={self.school_id}&status=all"))["users"]}
        new_file = not ACCOUNTS_FILE.exists()
        legacy = have.get(LEGACY_EXTERNAL_EMAIL)
        wanted_ai = next(p for p in PEOPLE if p.key == LEGACY_EXTERNAL_KEY)
        if legacy is not None and f"{wanted_ai.email}@{EMAIL_DOMAIN}" not in have:
            await self.api.patch(f"/api/users/{legacy['id']}", {"full_name": wanted_ai.name, "email": f"{wanted_ai.email}@{EMAIL_DOMAIN}", "specialization": wanted_ai.specialization})
            have = {u["email"]: u for u in (await self.api.get(f"/api/users?school_id={self.school_id}&status=all"))["users"]}
            self._rename_in_accounts_file(legacy["full_name"], LEGACY_EXTERNAL_EMAIL, wanted_ai)
            log(f"  V4: the single 'External Faculty' account is now '{wanted_ai.name}' (same person row, same temporary password)")
        with ACCOUNTS_FILE.open("a", encoding="utf-8") as out:
            if new_file:
                out.write(
                    "TimeTablePro - accounts created by the seed run (School of IT)\n"
                    "KEEP PRIVATE. This file is in .gitignore and is left out of backups. Each person must choose a new password at the first login.\n"
                    "Name | Role | Email | Temporary password\n" + "-" * 90 + "\n"
                )
            for p in PEOPLE:
                email = f"{p.email}@{EMAIL_DOMAIN}"
                if email in have:
                    self.people[p.key] = have[email]["id"]
                    continue
                body: dict[str, Any] = {
                    "full_name": p.name, "email": email, "role": p.role, "school_id": self.school_id, "specialization": p.specialization,
                }
                if p.visiting_days:
                    body.update(is_visiting=True, visiting_days=p.visiting_days, max_weekly_hours=VISITING_MAX_HOURS)
                res = await self.api.post("/api/users", body)
                self.people[p.key] = res["user"]["id"]
                out.write(f"{p.name} | {p.role.upper()} | {email} | {res['temp_password']}\n")
                out.flush()
                self.created_accounts += 1
        log(f"  teacher accounts: {len(PEOPLE)} in total, {self.created_accounts} created now (passwords -> seed_accounts.txt)")

    @staticmethod
    def _rename_in_accounts_file(old_name: str, old_email: str, person: Person) -> None:
        """seed_accounts.txt: the renamed account keeps its temporary password, only name + email change (nothing is printed)."""
        if not ACCOUNTS_FILE.exists():
            return
        lines = ACCOUNTS_FILE.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            parts = [x.strip() for x in line.split("|")]
            if len(parts) == 4 and parts[0] == old_name and parts[2] == old_email:
                lines[i] = f"{person.name} | {parts[1]} | {person.email}@{EMAIL_DOMAIN} | {parts[3]}"
        ACCOUNTS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 5) subjects + elective groups ------------------------------------------------------------------------------------------
    async def subjects_and_groups(self) -> None:
        existing = (await self.api.get(f"/api/subjects?school_id={self.school_id}"))["subjects"]
        have = {(s["semester_id"], s["code"]): s["id"] for s in existing}
        groups = {(g["semester_id"], g["name"]): g["id"] for g in (await self.api.get(f"/api/subjects/groups?school_id={self.school_id}"))["groups"]}
        created = renamed = 0
        for key, rows in SUBJECTS.items():
            sem_id = self.sem[key]
            for row in rows:
                code = f"{key}-{row['code']}"
                if (sem_id, code) in have:
                    self.subjects[(key, row["code"])] = have[(sem_id, code)]
                    continue
                legacy = have.get((sem_id, f"{key}-{row['legacy_code']}")) if row.get("legacy_code") else None
                gid = None
                if row.get("group"):
                    if (sem_id, row["group"]) not in groups:
                        groups[(sem_id, row["group"])] = (await self.api.post("/api/subjects/groups", {"semester_id": sem_id, "name": row["group"]}))["id"]
                    gid = groups[(sem_id, row["group"])]
                if legacy is not None:  # V3 had ONE shared lab "(both tracks)": it becomes the AI-track lab (renamed, not orphaned)
                    await self.api.patch(f"/api/subjects/{legacy}", {"name": row["name"], "code": code, "is_elective": True, "elective_group_id": gid})
                    have[(sem_id, code)] = legacy
                    self.subjects[(key, row["code"])] = legacy
                    renamed += 1
                    continue
                credits = row["credits"]
                special = "SOL" if row.get("sol") else "PE" if row.get("pe") else None
                body = {
                    "semester_id": sem_id, "name": row["name"], "code": code, "credits": credits,
                    # the seed file gives no semester hours: 15 hours per credit (UGC), a lab block = 30, PE / SOL = 2 lectures x 15 weeks
                    "semester_hours": 30 if (row.get("lab") or special) else credits * 15,
                    "is_lab": bool(row.get("lab")), "is_elective": bool(row.get("elective")), "special_kind": special,
                    "elective_group_id": gid,
                }
                res = await self.api.post("/api/subjects", body)
                self.subjects[(key, row["code"])] = res["id"]
                created += 1
        log(f"  subjects: {sum(len(v) for v in SUBJECTS.values())} in 8 classes ({created} created now, {renamed} shared labs split per track), elective groups: {len(groups)}")

    # 6) what each teacher can teach + assignments ---------------------------------------------------------------------------
    async def assignments(self) -> None:
        can: dict[str, set[str]] = {p.key: set() for p in PEOPLE}
        for key, rows in SUBJECTS.items():
            for row in rows:
                for t in (row.get("teacher"), row.get("co_teacher")):
                    if t:
                        can[t].add(self.subjects[(key, row["code"])])
        # 1) everybody may teach what they are going to be assigned (existing entries stay for now: assigned subjects are protected)
        for p in PEOPLE:
            now = set((await self.api.get(f"/api/users/{self.people[p.key]}")).get("subject_ids", []))
            if not can[p.key] <= now:
                await self.api.patch(f"/api/users/{self.people[p.key]}", {"subject_ids": sorted(now | can[p.key])})
        # 2) assign / move
        current = {s["id"]: s for s in (await self.api.get(f"/api/assignments?school_id={self.school_id}"))["subjects"]}
        done = skipped = 0
        for key, rows in SUBJECTS.items():
            for row in rows:
                if not row.get("teacher"):
                    continue
                sid = self.subjects[(key, row["code"])]
                t, co = self.people[row["teacher"]], self.people[row["co_teacher"]] if row.get("co_teacher") else None
                if current.get(sid, {}).get("teacher_id") == t and current.get(sid, {}).get("co_teacher_id") == co:
                    skipped += 1
                    continue
                await self.api.put("/api/assignments", {"subject_id": sid, "teacher_id": t, "co_teacher_id": co})
                done += 1
        # 3) now every list is trimmed to exactly what the person teaches
        for p in PEOPLE:
            now = set((await self.api.get(f"/api/users/{self.people[p.key]}")).get("subject_ids", []))
            if now != can[p.key]:
                await self.api.patch(f"/api/users/{self.people[p.key]}", {"subject_ids": sorted(can[p.key])})
        log(f"  'subjects they can teach' = exactly the assigned subjects; assignments: {done} made / moved now, {skipped} already there (AEC 3 of B.Sc. Sem 3 stays unassigned on purpose)")

    # 7) no PE / SOL -----------------------------------------------------------------------------------------------------------
    async def no_pe_sol(self) -> None:
        for key in NO_PE_SOL:
            await self.api.put(f"/api/fixed-slots/classes/{self.sem[key]}/no-pe-sol", {"no_pe_sol": True})
        log(f"  marked 'no PE / SOL': {', '.join(CLASSES[k][0] + ' Sem ' + str(CLASSES[k][1]) for k in NO_PE_SOL)}")

    # 8) fixed slots -----------------------------------------------------------------------------------------------------------
    async def fixed_slots(self) -> None:
        listing = (await self.api.get(f"/api/fixed-slots?school_id={self.school_id}"))["slots"]
        have = {(s["kind"], s["day"], s["start_period"], tuple(sorted(s["semester_ids"]))) for s in listing}
        made = 0
        for what, kind, day, start, classes in SLOTS:
            ids = [self.sem[k] for k in classes]
            if (kind, day, start, tuple(sorted(ids))) in have:
                continue
            await self.api.post("/api/fixed-slots", {"semester_ids": ids, "day": day, "start_period": start, "kind": kind})
            made += 1
            log(f"    locked: {what}")
        log(f"  fixed slots: {len(SLOTS)} sessions ({made} created now)")

    async def run(self) -> None:
        tree = await self.api.get("/api/structure/tree")
        school = next((s for s in tree["schools"] if s["name"] == "School of IT"), None)
        if school is None:
            raise SeedError("School of IT does not exist.")
        self.school_id = school["id"]
        for key, (course, number) in CLASSES.items():
            c = next((c for c in school["courses"] if c["name"] == course), None)
            sm = next((s for s in (c["semesters"] if c else []) if s["number"] == number), None)
            if sm is None:
                raise SeedError(f"{course} Sem {number} does not exist.")
            self.sem[key] = sm["id"]
        log("1) timing"); await self.timing()
        log("2) active term"); await self.term()
        log("3) day-wise schedules"); await self.day_schedules()
        log("4) teachers"); await self.teachers()
        log("5) subjects"); await self.subjects_and_groups()
        log("6) assignments"); await self.assignments()
        log("7) no PE / SOL"); await self.no_pe_sol()
        log("8) fixed slots"); await self.fixed_slots()


# ---------------------------------------------------------------------------------------------------- verification
class Report:
    def __init__(self) -> None:
        self.bad = 0
        self.notes = 0

    def check(self, ok: bool, msg: str, extra: str = "") -> None:
        if not ok:
            self.bad += 1
        log(("  PASS  " if ok else "  FAIL  ") + msg + (f"   {extra}" if extra else ""))

    def info(self, msg: str, extra: str = "") -> None:
        """A difference from the seed FILE that the college made on purpose later (never a failure)."""
        self.notes += 1
        log("  INFO  " + msg + (f"   {extra}" if extra else ""))


async def verify(api: Api) -> int:
    """READ-ONLY check of the real School of IT data.

    The college keeps editing its data after the seed (subjects, weekly lectures, day-wise schedules, the working week ...), so
    this does NOT compare every value with the seed file any more. It FAILS only on structural problems - something missing,
    broken or clashing - and lists the differences from the seed file as INFO lines.
    """
    rep = Report()
    tree = await api.get("/api/structure/tree")
    school = next((s for s in tree["schools"] if s["name"] == "School of IT"), None)
    rep.check(school is not None, "School of IT exists")
    if school is None:
        return rep.bad
    sid = school["id"]
    sem: dict[str, str] = {}
    for key, (course, number) in CLASSES.items():
        c = next((c for c in school["courses"] if c["name"] == course), None)
        found = next((s["id"] for s in (c or {}).get("semesters", []) if s["number"] == number), None)
        if found:
            sem[key] = found
    rep.check(len(sem) == len(CLASSES), f"all {len(CLASSES)} seeded classes still exist", f"found {len(sem)}")
    label_of = {sem[k]: f"{CLASSES[k][0]} - Sem {CLASSES[k][1]}" for k in sem}

    log("\n== active term + dates ==")
    term = await api.get("/api/terms")
    active = term.get("active") or next((t for t in term.get("terms", []) if t.get("is_active")), None)
    rep.check(bool(active), "an active term is selected", str(active and active.get("name")))
    if not active:
        return rep.bad
    if not (active["kind"] == "odd" and active["year"] == 2026):
        rep.info("the active term is not the seeded Odd 2026", active["name"])
    dates = await api.get(f"/api/terms/{active['id']}/dates?school_id={sid}")
    rows = dates.get("classes") or dates.get("items") or dates.get("dates") or []
    dated = [r for r in rows if r.get("start_date") and r.get("end_date") and r["start_date"] < r["end_date"]]
    rep.check(len(rows) > 0 and len(dated) == len(rows), f"every running class has semester dates (start before end): {len(dated)} of {len(rows)}")
    if [r for r in rows if (r.get("start_date"), r.get("end_date")) != (SEM_START, SEM_END)]:
        rep.info("some semester dates differ from the seed file", f"seed: {SEM_START} to {SEM_END}")

    log("\n== timing ==")
    timing = await api.get(f"/api/timing?school_id={sid}")
    c = timing["college"]
    rep.check(c["college_start"] < c["college_end"] and c["lecture_minutes"] > 0 and 5 <= c["days_per_week"] <= 6,
              f"college hours {c['college_start']}-{c['college_end']}, {c['lecture_minutes']} min lectures, {c['days_per_week']}-day week")
    if (c["college_start"], c["college_end"], c["lecture_minutes"], c["break_minutes"], c["days_per_week"]) != ("10:00", "16:50", 60, 5, 6):
        rep.info("college timing differs from the seed file (10:00-16:50, 60/5 min, Monday-Saturday)",
                 f"now {c['college_start']}-{c['college_end']}, {c['lecture_minutes']}/{c['break_minutes']} min, {c['days_per_week']} days")
    classes_t = {x["semester_id"]: x for x in timing["classes"]}
    for key, sem_id in sem.items():
        days = {d["day"]: d for d in classes_t.get(sem_id, {}).get("days", [])}
        sane = bool(days) and all(d["is_off"] or d["end_time"] is None or c["college_start"] < d["end_time"] <= c["college_end"] for d in days.values())
        rep.check(sane, f"{label_of[sem_id]}: day-wise schedule is complete and inside college hours",
                  " ".join(f"{d}:{'OFF' if days[d]['is_off'] else (days[d]['end_time'] or 'full')}" for d in sorted(days)))
        want = DAYS.get(key, {})
        differs = any((want.get(d) == "OFF") != days[d]["is_off"] or (want.get(d) not in (None, "OFF") and days[d]["end_time"] != want.get(d)) for d in days)
        if differs:
            rep.info(f"{label_of[sem_id]}: day-wise schedule changed since the seed")
    rep.check(not timing.get("warnings"), "no timing warnings", str(timing.get("warnings")))

    log("\n== teachers ==")
    users = (await api.get(f"/api/users?school_id={sid}&status=all"))["users"]
    by_email = {u["email"]: u for u in users}
    emails = [u["email"].lower() for u in users]
    rep.check(len(emails) == len(set(emails)), "every account has its own email address")
    hods = [u for u in users if u["role"] == "hod" and u["is_active"]]
    rep.check(len(hods) == 1, "the school has exactly one active HOD", ", ".join(u["full_name"] for u in hods))
    missing = [p.name for p in PEOPLE if f"{p.email}@{EMAIL_DOMAIN}" not in by_email]
    rep.check(not missing, f"all {len(PEOPLE)} seeded people still have an account", str(missing))
    if len(users) != len(PEOPLE):
        rep.info(f"{len(users)} accounts now (seed file: {len(PEOPLE)})")
    visiting = [u for u in users if u["is_visiting"]]
    rep.check(all(u["visiting_days"] and u.get("max_weekly_hours") for u in visiting), f"every visiting teacher has visiting days + a weekly-hours limit ({len(visiting)})")
    changed = [p.name for p in PEOPLE if (u := by_email.get(f"{p.email}@{EMAIL_DOMAIN}")) and (u["full_name"] != p.name or u["specialization"] != p.specialization)]
    if changed:
        rep.info(f"{len(changed)} teacher(s) edited their name / specialization since the seed")

    log("\n== subjects, labs, groups ==")
    subs = (await api.get(f"/api/subjects?school_id={sid}"))["subjects"]
    codes = [s["code"] for s in subs]
    rep.check(len(codes) == len(set(codes)), f"every subject code is unique ({len(subs)} subjects)")
    for key, sem_id in sem.items():
        mine = [s for s in subs if s["semester_id"] == sem_id]
        rep.check(len(mine) > 0, f"{label_of[sem_id]}: {len(mine)} subjects ({sum(1 for s in mine if s['is_lab'])} labs)")
        if len(mine) != len(SUBJECTS[key]):
            rep.info(f"{label_of[sem_id]}: {len(mine)} subjects now (seed file: {len(SUBJECTS[key])})")
    rep.check(all(s["credits"] >= 1 and s["semester_hours"] > 0 for s in subs), "every subject has credits and semester hours")
    groups = (await api.get(f"/api/subjects/groups?school_id={sid}"))["groups"]
    rep.check(all(len(g["members"]) >= 2 for g in groups), f"every elective group has 2 or more parallel options ({len(groups)} groups)")
    warned = [g["name"] for g in groups if g["warnings"]]
    if warned:
        rep.info("elective groups with a credit warning (the HOD decides)", ", ".join(warned))
    real_ids = {u["id"] for u in users if u["is_active"]}
    rep.check(all(s["teacher_id"] in real_ids for s in subs if s["teacher_id"]) and all(s["co_teacher_id"] in real_ids for s in subs if s["co_teacher_id"]),
              "no subject points at a missing / deactivated teacher")
    unassigned = [s["code"] for s in subs if not s["teacher_id"] and not s["special_kind"]]
    if unassigned:
        rep.info(f"{len(unassigned)} subject(s) without a teacher", ", ".join(unassigned))
    no_pe = {sem.get(k) for k in NO_PE_SOL}
    rep.check(not any(s["special_kind"] for s in subs if s["semester_id"] in no_pe), "classes marked 'no PE / SOL' have no PE / SOL subjects")

    log("\n== fixed slots ==")
    fs = await api.get(f"/api/fixed-slots?school_id={sid}")
    slots = fs["slots"]
    rep.check(len(slots) > 0, f"{len(slots)} locked sessions")
    if len(slots) != len(SLOTS):
        rep.info(f"{len(slots)} locked sessions now (seed file: {len(SLOTS)})")
    rep.check(all(s["length"] == 2 for s in slots if s["kind"] in ("PE", "SOL")), "every PE / SOL session is a 2-period double block")
    rep.check(not any(s["problems"] for s in slots), "no locked slot reports a problem (no clash, all inside the day-wise schedules)")
    busy: dict[tuple[str, int, int], set[str]] = {}
    for sl in slots:
        for t in (sl["teacher_id"], sl.get("co_teacher_id")):
            for n in range(sl["start_period"], sl["start_period"] + sl["length"]):
                if t:
                    busy.setdefault((t, sl["day"], n), set()).add(sl["id"])
    rep.check(all(len(v) == 1 for v in busy.values()), "no teacher is in two different locked sessions at once")
    occupied: dict[tuple[str, int, int], int] = {}
    for sl in slots:
        for sm_id in sl["semester_ids"]:
            for n in range(sl["start_period"], sl["start_period"] + sl["length"]):
                occupied[(sm_id, sl["day"], n)] = occupied.get((sm_id, sl["day"], n), 0) + 1
    rep.check(all(v == 1 for v in occupied.values()), "no class is in two locked sessions at once")
    cls = {x["semester_id"]: x for x in fs["classes"]}
    for k, sem_id in sem.items():
        x = cls.get(sem_id)
        if x is None:
            continue
        if x["no_pe_sol"]:
            rep.check(not x["has_pe"] and not x["has_sol"], f"{label_of[sem_id]}: marked 'no PE / SOL' and has none")
        else:
            rep.check(x["has_pe"] and x["has_sol"], f"{label_of[sem_id]}: PE fixed, SOL fixed")

    log("\n== the published timetable has no clash ==")
    from app.services import publishing as pub

    pub_rows = await db.fetch(
        "select id, semester_id, kind, subject_id, fixed_kind, label, day, start_period, length, group_key, teacher_id, co_teacher_id, combined_with "
        "from public.published_entries where school_id = $1 and term_id = $2", uuid.UUID(sid), uuid.UUID(active["id"]))
    prow = [pub.row_from_record(r) for r in pub_rows]
    t_busy: dict[tuple, set] = {}
    c_busy: dict[tuple, set] = {}
    for r in prow:
        for n in range(r.start, r.start + r.length):
            for t in r.teachers:
                t_busy.setdefault((t, r.day, n), set()).add(r.key)
            c_busy.setdefault((r.semester_id, r.day, n), set()).add(r.key)
    t_clash = [k for k, v in t_busy.items() if len(v) > 1]
    c_clash = [k for k, v in c_busy.items() if len(v) > 1]
    if prow:
        published_classes = len({r.semester_id for r in prow})
        rep.check(not t_clash, f"published timetable ({published_classes} classes, {len(prow)} rows): no teacher is in two places at once", str(t_clash[:3]))
        rep.check(not c_clash, "published timetable: no class has two lectures at once (parallel electives count once)", str(c_clash[:3]))
    else:
        rep.info("no timetable is published yet")

    log("\n== visiting days are a hard rule ==")
    for key in ("ANJANA", "FALGUNI"):
        person = next(p for p in PEOPLE if p.key == key)
        u = by_email.get(f"{person.email}@{EMAIL_DOMAIN}")
        if u is None or not u["is_visiting"] or "MIT1" not in sem:
            rep.info(f"{person.name}: not a visiting teacher any more (skipped)")
            continue
        not_days = [d for d in range(1, c["days_per_week"] + 1) if d not in u["visiting_days"]]
        for day in not_days[:3]:
            r = await api.call("POST", "/api/fixed-slots/check", ok=(200, 422), json={"semester_ids": [sem["MIT1"]], "day": day, "start_period": 1, "kind": "OTHER", "length": 1, "label": "Probe", "teacher_id": u["id"]})
            bad = r.get("problems") or ([r["message"]] if r.get("message") else [])
            rep.check(r.get("ok") is False and any("visiting faculty" in m for m in bad), f"{u['full_name']} refused on {day_name(day)} (not a visiting day)")
    after = (await api.get(f"/api/fixed-slots?school_id={sid}"))["slots"]
    rep.check(len(after) == len(slots), "the probes saved nothing")

    log("\n== dashboards ==")
    admin_user = Who.user
    admin_ov = await api.get("/api/hod/overview")
    rep.check(admin_ov["active_term"]["name"] == active["name"], f"admin dashboard: active term {active['name']}")
    Who.user = await load_user(role="hod")
    try:
        hod_ov = await api.get("/api/hod/overview")
        st = hod_ov["setup"]
        rep.check(hod_ov["school"]["name"] == "School of IT" and st["classes"] == len(rows), "HOD dashboard: School of IT, every running class counted",
                  str({k: st[k] for k in ("classes", "classes_with_pe", "classes_with_sol", "no_pe_sol_classes")}))
        rep.check(len((await api.get("/api/fixed-slots"))["slots"]) == len(slots), "the HOD sees all locked sessions of his school")
        teacher = next((u for u in users if u["role"] == "teacher" and u["is_active"]), None)
        if teacher:
            Who.user = await load_user(email=teacher["email"])
            t_ov = await api.get("/api/teacher/overview")
            rep.check(t_ov.get("active_term", {}).get("name") == active["name"], "teacher home: active term shown")
    except SeedError as exc:
        rep.check(False, "dashboards load", str(exc))
    finally:
        Who.user = admin_user

    tail = f"   ({rep.notes} INFO line(s) = changes made after the seed, not errors)" if rep.notes else ""
    log(f"\nVERIFY: {'ALL PASS' if rep.bad == 0 else str(rep.bad) + ' CHECK(S) FAILED'}{tail}")
    return rep.bad


def day_name(d: int) -> str:
    return ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d]


# ---------------------------------------------------------------------------------------------------- main
async def main() -> int:
    parser = argparse.ArgumentParser(description="Seed (or verify) the real School of IT data")
    parser.add_argument("--verify", action="store_true", help="only check the data against the seed file")
    args = parser.parse_args()
    settings = get_settings()
    async with app.router.lifespan_context(app):
        Who.user = await load_user(email=settings.seed_admin_email, role="admin")
        deps.authenticate = _fake_authenticate  # every route now sees the real admin (no password involved)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost", headers={"Origin": "http://localhost:5173"}, timeout=180) as client:
            api = Api(client)
            try:
                if args.verify:
                    return 1 if await verify(api) else 0
                await Seeder(api).run()
            except SeedError as exc:
                log(f"\nSTOPPED: {exc}")
                return 1
    log("\nSeeding finished. Run  python -m scripts.seed_college --verify  to check it.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
