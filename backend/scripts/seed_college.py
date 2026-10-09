"""Seed the REAL School of IT data (COLLEGE_SEED_DATA.md V3 - FINAL) through the backend API.

  python -m scripts.seed_college            seeds everything (safe to run again: it only creates what is missing)
  python -m scripts.seed_college --verify   checks the result against the seed file and prints a report (changes nothing)

How: the FastAPI app is called IN-PROCESS (no server needed), as the real admin account (no password needed), so every
validation rule and every audit-log entry runs exactly as if the admin had clicked the buttons.
Teacher accounts get a random temporary password; each is written to  seed_accounts.txt  in the project root
(git-ignored, excluded from backups, NEVER printed). Order = Section 9 of the seed file.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
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
    Person("EXTERNAL", "External Faculty", "external.faculty", "B.Sc. specialization tracks (AI&ML / Cyber)"),
    # visiting faculty: Monday + Tuesday + Wednesday only (hard rule). The seed file gives no weekly-hours limit and the
    # account form requires one -> 18 = three days x six periods, i.e. no limit beyond the three allowed days.
    Person("ANJANA", "Anjana Prajapati", "anjana.prajapati", "AI & Machine Learning, Data Structures & Algorithms", visiting_days=[1, 2, 3]),
    Person("FALGUNI", "Falguni Thakker", "falguni.thakker", "Generative AI, Data Visualization, Digital Image Processing", visiting_days=[1, 2, 3]),
]
VISITING_MAX_HOURS = 18

# ---------------------------------------------------------------------------------------------------- subjects (Sections 6-8)
# (code, name, credits, teacher key | None, flags)   flags: lab | elective | sol | pe ; group = track group name
def theory_lab(code: str, name: str, credits: int, teacher: str, **kw) -> list[dict]:
    """A '3 theory + 1 lab block' subject = a theory subject plus its LAB subject (1 credit, one double block a week)."""
    return [
        {"code": code, "name": name, "credits": credits, "teacher": teacher, **kw},
        {"code": f"{code}-LAB", "name": f"{name} LAB", "credits": 1, "teacher": teacher, "lab": True},
    ]


def track(label: str, a: tuple[str, str, str], b: tuple[str, str, str], credits: int = 3) -> list[dict]:
    """Track majors: AI-track and Cyber-track subjects run in the SAME parallel slots (one elective group) + ONE lab for both."""
    group = f"{label} - Track Majors"
    return [
        {"code": f"{label.replace(' ', '')}-{a[0]}", "name": a[1], "credits": credits, "teacher": "EXTERNAL", "elective": True, "group": group},
        {"code": f"{label.replace(' ', '')}-{b[0]}", "name": b[1], "credits": credits, "teacher": "EXTERNAL", "elective": True, "group": group},
        {"code": f"{label.replace(' ', '')}-LAB", "name": f"{label} LAB (both tracks)", "credits": 1, "teacher": "EXTERNAL", "lab": True},
    ]


SUBJECTS: dict[str, list[dict]] = {
    "BSC1": [
        *track("Major 1", ("AIML", "Intro to AI & ML with Python", ""), ("INFOSEC", "Information Security Fundamentals", "")),
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
        *track("Major 5", ("DL", "Deep Learning", ""), ("ITADS", "IT Application & Data Security", "")),
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
        *track("Major 11", ("PRAD", "Pattern Recognition & Anomaly Detection / LLM Dev & Deployment", ""), ("DF", "Digital Forensics", "")),
        *theory_lab("MAJ12", "Blockchain Technology", 3, "SUNIL"),
        *theory_lab("MAJ13", "Full Stack Development", 3, "CHIRAG"),
        {"code": "MIN4", "name": "Data Governance & Privacy Engineering", "credits": 4, "teacher": "SHREYA"},
        *theory_lab("MIN5", "Introduction to DevOps", 3, "DHAVAL"),
        {"code": "SEC5", "name": "UI/UX Design", "credits": 1, "teacher": "MAITRI"},
        {"code": "SEC5-LAB", "name": "UI/UX Design LAB", "credits": 1, "teacher": "MAITRI", "lab": True},
        {"code": "VAC5", "name": "Foundation of Indian Culture - I", "credits": 2, "teacher": "NIMESH"},
    ],
    "BSC7": [
        *track("Major 17", ("PRAD", "Pattern Recognition & Anomaly Detection", ""), ("CWAS", "Cryptography & Web App Security", "")),
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

    # 5) subjects + elective groups ------------------------------------------------------------------------------------------
    async def subjects_and_groups(self) -> None:
        existing = (await self.api.get(f"/api/subjects?school_id={self.school_id}"))["subjects"]
        have = {(s["semester_id"], s["code"]): s["id"] for s in existing}
        groups = {(g["semester_id"], g["name"]): g["id"] for g in (await self.api.get(f"/api/subjects/groups?school_id={self.school_id}"))["groups"]}
        created = 0
        for key, rows in SUBJECTS.items():
            sem_id = self.sem[key]
            for row in rows:
                code = f"{key}-{row['code']}"
                if (sem_id, code) in have:
                    self.subjects[(key, row["code"])] = have[(sem_id, code)]
                    continue
                gid = None
                if row.get("group"):
                    if (sem_id, row["group"]) not in groups:
                        groups[(sem_id, row["group"])] = (await self.api.post("/api/subjects/groups", {"semester_id": sem_id, "name": row["group"]}))["id"]
                    gid = groups[(sem_id, row["group"])]
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
        log(f"  subjects: {sum(len(v) for v in SUBJECTS.values())} in 8 classes ({created} created now), elective groups: {len(groups)}")

    # 6) what each teacher can teach + assignments ---------------------------------------------------------------------------
    async def assignments(self) -> None:
        can: dict[str, set[str]] = {p.key: set() for p in PEOPLE}
        for key, rows in SUBJECTS.items():
            for row in rows:
                for t in (row.get("teacher"), row.get("co_teacher")):
                    if t:
                        can[t].add(self.subjects[(key, row["code"])])
        for p in PEOPLE:
            await self.api.patch(f"/api/users/{self.people[p.key]}", {"subject_ids": sorted(can[p.key])})
        log("  'subjects they can teach' filled for every teacher")
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
        log(f"  assignments: {done} made now, {skipped} already there (AEC 3 of B.Sc. Sem 3 stays unassigned on purpose)")

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

    def check(self, ok: bool, msg: str, extra: str = "") -> None:
        if not ok:
            self.bad += 1
        log(("  PASS  " if ok else "  FAIL  ") + msg + (f"   {extra}" if extra else ""))


async def verify(api: Api) -> int:
    rep = Report()
    tree = await api.get("/api/structure/tree")
    school = next(s for s in tree["schools"] if s["name"] == "School of IT")
    sid = school["id"]
    sem = {}
    for key, (course, number) in CLASSES.items():
        c = next(c for c in school["courses"] if c["name"] == course)
        sem[key] = next(s["id"] for s in c["semesters"] if s["number"] == number)

    log("\n== active term + dates ==")
    term = (await api.get("/api/terms"))
    active = term.get("active") or next((t for t in term.get("terms", []) if t.get("is_active")), None)
    rep.check(bool(active) and active["kind"] == "odd" and active["year"] == 2026, "active term is Odd 2026", str(active and active.get("name")))
    dates = (await api.get(f"/api/terms/{active['id']}/dates?school_id={sid}"))
    rows = dates.get("classes") or dates.get("items") or dates.get("dates") or []
    dated = [r for r in rows if r.get("start_date") == SEM_START and r.get("end_date") == SEM_END]
    rep.check(len(rows) == 8 and len(dated) == 8, f"semester dates {SEM_START} to {SEM_END} on all 8 running classes", f"({len(dated)} of {len(rows)})")

    log("\n== timing ==")
    timing = await api.get(f"/api/timing?school_id={sid}")
    c = timing["college"]
    rep.check((c["college_start"], c["college_end"], c["lecture_minutes"], c["break_minutes"], c["days_per_week"]) == ("10:00", "16:50", 60, 5, 6), "college 10:00-16:50, 60/5 min, Monday-Saturday")
    classes_t = {x["semester_id"]: x for x in timing["classes"]}
    for key, rules in DAYS.items():
        days = {d["day"]: d for d in classes_t[sem[key]]["days"]}
        ok = True
        for d in range(1, 7):
            want = rules.get(d)
            got = days[d]
            if want == "OFF":
                ok &= got["is_off"] is True
            elif want:
                ok &= (not got["is_off"]) and got["end_time"] == want
            else:
                ok &= (not got["is_off"]) and got["end_time"] in (None, "16:50")
        rep.check(ok, f"{CLASSES[key][0]} Sem {CLASSES[key][1]}: day-wise schedule", " ".join(f"{d}:{'OFF' if days[d]['is_off'] else (days[d]['end_time'] or 'full')}" for d in range(1, 7)))
    rep.check(not timing.get("warnings"), "no timing warnings", str(timing.get("warnings")))

    log("\n== teachers ==")
    users = (await api.get(f"/api/users?school_id={sid}&status=all"))["users"]
    by_email = {u["email"]: u for u in users}
    rep.check(len(users) == len(PEOPLE), f"{len(PEOPLE)} accounts (1 HOD + 13 regular + 2 visiting)", f"found {len(users)}")
    rep.check(not any("dixit" in u["full_name"].lower() or "dixit" in u["email"] for u in users), "NO account for Prof. Anurag Dixit")
    rep.check(all(u["email"].endswith("@school-it.test") for u in users), "all emails are dummy @school-it.test")
    hod = [u for u in users if u["role"] == "hod"]
    rep.check(len(hod) == 1 and hod[0]["full_name"] == "Dr. Papri Das", "Dr. Papri Das is the HOD of School of IT")
    for p in PEOPLE:
        u = by_email.get(f"{p.email}@{EMAIL_DOMAIN}")
        ok = u is not None and u["full_name"] == p.name and u["specialization"] == p.specialization and u["is_visiting"] == bool(p.visiting_days) and sorted(u["visiting_days"]) == p.visiting_days and u["must_change_password"] is True
        rep.check(ok, f"{p.name}: specialization{' + visiting days Mon-Wed' if p.visiting_days else ''}, must change password at first login")

    log("\n== subjects, credits, labs, groups ==")
    subs = (await api.get(f"/api/subjects?school_id={sid}"))["subjects"]
    for key, (course, number) in CLASSES.items():
        mine = [s for s in subs if s["semester_id"] == sem[key]]
        counted, seen_groups = 0, set()
        for s in mine:
            if s["elective_group_id"]:
                if s["elective_group_id"] in seen_groups:
                    continue
                seen_groups.add(s["elective_group_id"])
            counted += s["credits"]
        want = SEED_CREDITS[key] - NOT_SEEDED_CREDITS.get(key, 0)
        labs = sum(1 for s in mine if s["is_lab"])
        extra = f"+ {NOT_SEEDED_CREDITS[key]} credits of OJT/Internship not seeded = {counted + NOT_SEEDED_CREDITS[key]} (seed file: {SEED_CREDITS[key]})" if key in NOT_SEEDED_CREDITS else f"(seed file: {SEED_CREDITS[key]})"
        rep.check(len(mine) == len(SUBJECTS[key]) and counted == want, f"{course} Sem {number}: {len(mine)} subjects ({labs} labs), {counted} credits", extra)
    labs_bad = [s for s in subs if s["is_lab"] and (s["credits"] != 1 or s["semester_hours"] != 30)]
    rep.check(not labs_bad, "every lab is 1 credit (one double block a week)")
    groups = (await api.get(f"/api/subjects/groups?school_id={sid}"))["groups"]
    rep.check(len(groups) == 4 and all(len(g["members"]) == 2 and not g["warnings"] for g in groups), "4 B.Sc. track groups (AI&ML || Cyber), 2 parallel majors each, no credit warnings", str([(g["class_label"], g["name"]) for g in groups]))
    ext = [s for s in subs if s["is_elective"] and s["elective_group_id"]]
    rep.check(all(s["teacher_name"] == "External Faculty" for s in ext), "all track majors are taught by External Faculty")
    pesol = [s for s in subs if s["special_kind"]]
    rep.check(len(pesol) == 12 and all(s["credits"] in (1, 2) for s in pesol), "PE / SOL subjects: 6 PE + 6 SOL", f"found {len(pesol)}")
    rep.check(not any(s["special_kind"] for s in subs if s["semester_id"] in (sem["BSC5"], sem["BSC7"])), "B.Sc. Sem 5 and Sem 7 have no PE / SOL subjects")

    log("\n== assignments ==")
    unassigned = [s for s in subs if not s["teacher_id"]]
    rep.check(len(unassigned) == 1 and unassigned[0]["code"] == "BSC3-AEC3", "only AEC 3 (B.Sc. Sem 3) has no teacher", str([s["code"] for s in unassigned]))
    minor7 = next(s for s in subs if s["code"] == "BSC7-MIN7")
    rep.check(minor7["teacher_name"] == "Dr. Papri Das" and minor7["co_teacher_name"] == "Maitri Hingu", "Minor 7: Dr. Papri Das + co-teacher Maitri Hingu")
    expected_pairs = {f"{k}-{r['code']}": (r.get("teacher"), r.get("co_teacher")) for k, rows in SUBJECTS.items() for r in rows}
    name_of = {p.key: p.name for p in PEOPLE}
    wrong = [s["code"] for s in subs if (name_of.get(expected_pairs[s["code"]][0]), name_of.get(expected_pairs[s["code"]][1])) != (s["teacher_name"], s["co_teacher_name"])]
    rep.check(not wrong, f"every other assignment matches the seed file ({len(subs) - 1} of {len(subs)} checked)", str(wrong))

    log("\n== fixed slots ==")
    fs = await api.get(f"/api/fixed-slots?school_id={sid}")
    slots = fs["slots"]
    rep.check(len(slots) == len(SLOTS), f"{len(SLOTS)} locked sessions", f"found {len(slots)}")
    label_of = {sem[k]: f"{CLASSES[k][0]} - Sem {CLASSES[k][1]}" for k in sem}
    for what, kind, day, start, classes in SLOTS:
        s = next((x for x in slots if x["kind"] == kind and x["day"] == day and x["start_period"] == start), None)
        ok = s is not None and s["length"] == 2 and sorted(s["semester_ids"]) == sorted(sem[k] for k in classes) and s["combined"] == (len(classes) > 1) and not s["problems"]
        who = s["teacher_name"] if s else None
        rep.check(ok, f"{what}: {day_name(day)} P{start}-P{start + 1}, {'COMBINED ' if len(classes) > 1 else ''}{', '.join(CLASSES[k][0] + ' S' + str(CLASSES[k][1]) for k in classes)}", f"teacher: {who}")
    want_teacher = {"SOL I (B.Sc.)": "Dr. Dhaval Thaker", "PE I (B.Sc.)": "Mr. Sushant Singh", "PE Monday (combined)": "Mr. Sushant Singh",
                    "SOL I (M.Sc.)": "Ms. Juhikadevi Bhanjdeo", "SOL III (B.Sc. + M.Sc.)": "Ms. Subarna Ghosh"}
    for what, kind, day, start, classes in SLOTS:
        s = next(x for x in slots if x["kind"] == kind and x["day"] == day and x["start_period"] == start)
        rep.check(s["teacher_name"] == want_teacher[what], f"teacher of '{what}'", str(s["teacher_name"]))
    cls = {c["semester_id"]: c for c in fs["classes"]}
    for k in ("BSC1", "BSC3", "MIT1", "MIT3", "MAI1", "MAI3"):
        rep.check(cls[sem[k]]["has_pe"] and cls[sem[k]]["has_sol"], f"{label_of[sem[k]]}: PE fixed, SOL fixed")
    for k in NO_PE_SOL:
        rep.check(cls[sem[k]]["no_pe_sol"] and not cls[sem[k]]["has_pe"] and not cls[sem[k]]["has_sol"], f"{label_of[sem[k]]}: marked 'no PE / SOL' and has none")
    rep.check(not any(s["problems"] for s in slots), "no locked slot reports a problem (no clash, all inside the day-wise schedules)")
    # the double booking of a teacher in two different sessions at the same time must not exist
    busy: dict[tuple[str, int, int], list[str]] = {}
    for s in slots:
        for p in range(s["start_period"], s["start_period"] + s["length"]):
            busy.setdefault((s["teacher_id"], s["day"], p), []).append(s["id"])
    rep.check(all(len(v) == 1 for v in busy.values()), "no teacher is in two different locked sessions at once")
    # a class is never in two places either
    occupied: dict[tuple[str, int, int], int] = {}
    for s in slots:
        for sm_id in s["semester_ids"]:
            for p in range(s["start_period"], s["start_period"] + s["length"]):
                occupied[(sm_id, s["day"], p)] = occupied.get((sm_id, s["day"], p), 0) + 1
    rep.check(all(v == 1 for v in occupied.values()), "no class is in two places at once")

    log("\n== visiting days are a hard rule ==")
    for key in ("ANJANA", "FALGUNI"):
        tid = next(u["id"] for u in users if u["email"] == f"{next(p for p in PEOPLE if p.key == key).email}@{EMAIL_DOMAIN}")
        for day in (4, 5, 6):
            r = await api.call("POST", "/api/fixed-slots/check", ok=(200, 422), json={"semester_ids": [sem["MIT1"]], "day": day, "start_period": 1, "kind": "OTHER", "length": 1, "label": "Probe", "teacher_id": tid})
            bad = r.get("problems") or ([r["message"]] if r.get("message") else [])
            rep.check(r.get("ok") is False and any("visiting faculty" in p for p in bad), f"{name_of[key]} refused on {day_name(day)}")
        r = await api.call("POST", "/api/fixed-slots/check", ok=(200, 422), json={"semester_ids": [sem["MIT1"]], "day": 4, "start_period": 1, "kind": "OTHER", "length": 1, "label": "Probe", "teacher_id": tid})
    # the probes never save anything
    after = (await api.get(f"/api/fixed-slots?school_id={sid}"))["slots"]
    rep.check(len(after) == len(SLOTS), "the probes saved nothing")

    log("\n== dashboards / checklists ==")
    admin_user = Who.user
    admin_ov = await api.get("/api/hod/overview")
    rep.check(admin_ov["active_term"]["name"] == "Odd 2026", "admin dashboard: active term Odd 2026")
    Who.user = await load_user(role="hod")
    hod_ov = await api.get("/api/hod/overview")
    s = hod_ov["setup"]
    rep.check(hod_ov["school"]["name"] == "School of IT" and hod_ov["counts"]["teachers"] == len(PEOPLE) - 1, "HOD dashboard: School of IT", f"teachers counted: {hod_ov['counts']['teachers']}")
    rep.check(s["classes"] == 8 and s["no_pe_sol_classes"] == 2 and s["classes_with_pe"] == 6 and s["classes_with_sol"] == 6, "HOD checklist: 8 classes, PE 6/6, SOL 6/6 (2 classes without PE / SOL)", str({k: s[k] for k in ("classes", "classes_with_pe", "classes_with_sol", "no_pe_sol_classes")}))
    rep.check(s.get("dates_set") == 8, "HOD checklist: semester dates set for 8 classes", str(s.get("dates_set")))
    log("  HOD setup numbers: " + json.dumps(s, default=str))
    hod_slots = await api.get("/api/fixed-slots")
    rep.check(len(hod_slots["slots"]) == len(SLOTS), "the HOD sees all locked sessions of his school")
    # a teacher's home screen
    Who.user = await load_user(email=f"sushant.singh@{EMAIL_DOMAIN}")
    try:
        t_ov = await api.get("/api/teacher/overview")
        rep.check(t_ov.get("active_term", {}).get("name") == "Odd 2026", "teacher home: active term shown")
    except SeedError as exc:
        rep.check(False, "teacher home loads", str(exc))
    Who.user = admin_user

    log(f"\nVERIFY: {'ALL PASS' if rep.bad == 0 else str(rep.bad) + ' CHECK(S) FAILED'}")
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
