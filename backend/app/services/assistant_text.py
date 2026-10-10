"""Plain-Python helpers of the AI assistant: reading a date, a teacher's name, a class name, a period - from words typed in
English or Hinglish.  No database, no network (so every rule here can be tested without either).

WHY this is not left to the AI model: a language model is good at understanding a sentence but poor at date arithmetic and at
knowing which of our teachers "Chirag sir" is.  The model only picks the action and copies the WORDS the person typed;
the dates and the names are resolved here, against live data, with the person's own permissions.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher, get_close_matches
from typing import Any, Sequence

# ---------------------------------------------------------------------------------------------------- dates
WEEKDAYS: dict[str, int] = {}
for _num, _words in {
    1: "monday mon somvar somwar somvaar somwaar soma",
    2: "tuesday tue tues mangalvar mangalwar mangal mangalvaar",
    3: "wednesday wed budhvar budhwar budh budhvaar",
    4: "thursday thu thur thurs guruvar guruwar brihaspativar brihaspatiwar virvar veervar guruvaar",
    5: "friday fri shukravar shukrawar shukra shukravaar",
    6: "saturday sat shanivar shaniwar shani shanivaar",
    7: "sunday sun ravivar raviwar ravi itvar itwar ravivaar",
}.items():
    for _w in _words.split():
        WEEKDAYS[_w] = _num

RELATIVE_DAYS: dict[str, int] = {
    "today": 0, "aaj": 0, "tomorrow": 1, "tmrw": 1, "tmr": 1, "kal": 1, "next day": 1, "day after tomorrow": 2,
    "day_after_tomorrow": 2, "parso": 2, "parson": 2, "parsoon": 2, "yesterday": -1, "beeta kal": -1, "kal beeta": -1,
}
WEEK_WORDS = {"week", "this week", "this_week", "hafta", "hafte", "poora hafta", "weekly", "is hafte", "this-week"}
NEXT_WEEK_WORDS = {"next week", "next_week", "agle hafte", "agla hafta", "next-week"}
NOW_WORDS = {"now", "abhi", "right now", "currently", "is waqt", "filhal"}

LOWEST, HIGHEST = date(2000, 1, 1), date(2100, 12, 31)


class Unclear(Exception):
    """The words could not be understood (the message is shown to the person)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class When:
    kind: str                 # 'date' | 'week' | 'now' | 'none'
    day: date | None = None   # for 'date' (the day), for 'week' (any day of that week)
    word: str = ""            # what the person said (to echo back)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s\-/]", " ", (text or "").strip().lower())).strip()


def resolve_when(raw: Any, today: date, *, default: str = "today") -> When:
    """'today', 'kal', 'monday', 'somvar', '2026-10-12', '12/10/2026', 'now', 'week', 'next week' ->  a When.
    A weekday name means its NEXT occurrence (today counts) - 'Monday absent' said on a Wednesday is next Monday."""
    text = _clean(str(raw)) if raw not in (None, "") else ""
    if not text:
        text = default
    if text in NOW_WORDS:
        return When("now", today, text)
    if text in WEEK_WORDS:
        return When("week", today, text)
    if text in NEXT_WEEK_WORDS:
        return When("week", today + timedelta(days=7), text)
    if text in RELATIVE_DAYS:
        return When("date", today + timedelta(days=RELATIVE_DAYS[text]), text)
    # "next monday", "agle somvar", "this friday" - the weekday word decides
    words = [w for w in re.split(r"[\s_\-]+", text) if w]
    for w in words:
        if w in WEEKDAYS:
            wd = WEEKDAYS[w]
            ahead = (wd - today.isoweekday()) % 7
            if ("next" in words or "agle" in words or "agla" in words) and ahead == 0:
                ahead = 7
            return When("date", today + timedelta(days=ahead), text)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", text)
        if not m:
            raise Unclear(f"I could not understand the day '{str(raw)[:40]}'. Try 'today', 'tomorrow', a weekday like 'Friday' or a date like 2026-10-12.")
        d, mo, y = (int(x) for x in m.groups())
    try:
        day = date(y, mo, d)
    except ValueError:
        raise Unclear(f"{str(raw)[:40]} is not a real date.") from None
    if not LOWEST <= day <= HIGHEST:
        raise Unclear("Please give a date between the years 2000 and 2100.")
    return When("date", day, text)


def nice_date(d: date) -> str:
    return d.strftime("%a %d %b %Y")


def monday_of(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1)


# ---------------------------------------------------------------------------------------------------- names
TITLES = frozenset(
    "dr doctor prof professor mr mrs ms miss sir saab sahab sahib madam mam maam ji sri shri smt sh teacher faculty hod "
    "sirji madamji bhai behen the".split()
)


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def name_tokens(text: str) -> list[str]:
    """'Dr. Chirag Mehta' -> ['chirag', 'mehta'];  'Sharma sir' -> ['sharma'];  titles and punctuation are dropped."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", _ascii(text or "").lower().replace("'", ""))
    return [t for t in cleaned.split() if t and t not in TITLES]


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    role: str = "teacher"
    school: str = ""
    school_id: str = ""
    active: bool = True
    tokens: tuple[str, ...] = field(default=(), compare=False)

    def with_tokens(self) -> "Person":
        return Person(self.id, self.name, self.role, self.school, self.school_id, self.active, tuple(name_tokens(self.name)))


def _token_score(q: str, t: str) -> float:
    if q == t:
        return 1.0
    if len(q) >= 3 and (t.startswith(q) or (q.startswith(t) and len(t) >= 4)):
        return 0.88
    if len(q) >= 4 and len(t) >= 4:
        ratio = SequenceMatcher(None, q, t).ratio()
        if ratio >= 0.8:
            return 0.8 * ratio
    return 0.0


@dataclass
class NameMatch:
    kind: str                          # 'one' | 'many' | 'none'
    people: list[Person] = field(default_factory=list)
    suggestions: list[Person] = field(default_factory=list)


def match_people(query: str, people: Sequence[Person], *, active_only: bool = False) -> NameMatch:
    """Which person did they mean?  Every typed word has to match a word of the name (exactly, as a start, or with a small
    typing mistake).  One clear winner -> 'one'.  Several equally good -> 'many' (the assistant then asks which one)."""
    pool = [p.with_tokens() for p in people if p.active or not active_only]
    qs = name_tokens(query)
    if not qs:
        return NameMatch("none")
    scored: list[tuple[float, Person]] = []
    for p in pool:
        parts = []
        for q in qs:
            best = max((_token_score(q, t) for t in p.tokens), default=0.0)
            if best <= 0.0:
                break
            parts.append(best)
        else:
            scored.append((sum(parts) / len(parts), p))
    if scored:
        scored.sort(key=lambda x: (-x[0], x[1].name.lower()))
        top = scored[0][0]
        best = [p for s, p in scored if s >= top - 0.04]
        if len(best) == 1:
            return NameMatch("one", best)
        return NameMatch("many", best[:8])
    # nothing matched every word: offer the closest names ("did you mean ...")
    flat = {" ".join(p.tokens): p for p in pool}
    out = [flat[c] for c in get_close_matches(" ".join(qs), list(flat), n=3, cutoff=0.55)]
    for q in qs:
        for t_close in get_close_matches(q, [t for p in pool for t in p.tokens], n=3, cutoff=0.7):
            out += [p for p in pool if t_close in p.tokens and p not in out]
    return NameMatch("none", suggestions=out[:3])


# ---------------------------------------------------------------------------------------------------- classes
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}


@dataclass(frozen=True)
class ClassRef:
    id: str
    label: str            # "B.Sc. IT Sem 3"
    course: str           # "B.Sc. IT"
    number: int
    school_id: str = ""
    school: str = ""


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _ascii(s or "").lower())


def split_class_words(text: str) -> tuple[str, int | None]:
    """'B.Sc. IT Sem 3' -> ('bscit', 3);  'msc ai 1st semester' -> ('mscai', 1);  'sem 5' -> ('', 5)."""
    t = _ascii(text or "").lower()
    number: int | None = None
    patterns = [
        r"\bsem(?:ester)?\.?\s*[-:]?\s*(\d{1,2}|[ivx]{1,4})\b",
        r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*sem(?:ester)?\b",
        r"\bs(\d{1,2})\b",
    ]
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            raw = m.group(1)
            number = int(raw) if raw.isdigit() else ROMAN.get(raw)
            t = (t[: m.start()] + " " + t[m.end():])
            break
    return _alnum(t), number


@dataclass
class ClassMatch:
    kind: str                              # 'one' | 'many' | 'none'
    classes: list[ClassRef] = field(default_factory=list)


def match_class(query: str, classes: Sequence[ClassRef]) -> ClassMatch:
    course_key, number = split_class_words(query)
    if not course_key and number is None:
        return ClassMatch("none")
    pool = list(classes)
    if number is not None:
        pool = [c for c in pool if c.number == number]
    if course_key:
        exact = [c for c in pool if _alnum(c.course) == course_key]
        if exact:
            pool = exact
        else:
            # a course name that is only PART of a real one ("IT" in "B.Sc. IT") narrows the list; there is no guessing from
            # spelling - the assistant offers the real classes to tap instead (a wrong class is worse than one more tap)
            pool = [c for c in pool if course_key in _alnum(c.course) or _alnum(c.course) in course_key]
    if not pool:
        return ClassMatch("none")
    if len(pool) == 1:
        return ClassMatch("one", pool)
    return ClassMatch("many", sorted(pool, key=lambda c: (c.school, c.label))[:8])


# ---------------------------------------------------------------------------------------------------- misc
def clip(value: Any, limit: int = 120) -> str:
    """Anything the model hands back is data: text only, no control characters, a sane length."""
    text = "" if value is None else str(value)
    text = "".join(ch for ch in text if ch == "\n" or ch >= " ")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def as_int(value: Any, low: int, high: int) -> int | None:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    return n if low <= n <= high else None


_HINGLISH = re.compile(
    r"\b(aaj|kal|parso|kon|kaun|koun|kaun\s*sa|kya|hai|hain|hu|hoon|hun|nahi|nahin|mera|meri|mere|mujhe|dikhao|batao|bata|do|karo|"
    r"ka|ki|ke|me|mein|se|par|pe|abhi|sir|madam|wale|wala|chutti|gaya|gayi|aaya|aayi|shukravar|somvar|mangalvar|budhvar|guruvar|shanivar)\b"
)


def looks_hinglish(text: str) -> bool:
    """A rough guess used only when the model does not say which language to answer in."""
    t = (text or "").lower()
    if re.search(r"[ऀ-ॿ]", t):
        return True
    return len(_HINGLISH.findall(t)) >= 2
