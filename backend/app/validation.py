"""Input validation building blocks: every text, time, date, e-mail and phone that enters the API goes through these."""
from __future__ import annotations

import re
from datetime import date, time
from typing import Annotated, Any

from pydantic import BeforeValidator, StringConstraints

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: Any) -> Any:
    """Strips control characters and tidies spaces. (HTML is not a problem: the screens never render raw HTML.)"""
    if isinstance(value, str):
        value = _CONTROL.sub("", value)
        return re.sub(r"[ \t]+", " ", value).strip()
    return value


def text(min_length: int = 1, max_length: int = 120):
    return Annotated[str, BeforeValidator(clean_text), StringConstraints(min_length=min_length, max_length=max_length)]


def optional_text(max_length: int = 300):
    def _empty_to_none(v: Any) -> Any:
        v = clean_text(v)
        return None if v == "" else v

    # the length limit belongs to the text only - a missing value (null / empty) is simply "nothing"
    return Annotated[Annotated[str, StringConstraints(max_length=max_length)] | None, BeforeValidator(_empty_to_none)]


# ---------------------------------------------------------------------- time / date
def _parse_time(value: Any) -> time:
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, str):
        m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", value.strip())
        if m:
            hour, minute = int(m[1]), int(m[2])
            if hour < 24 and minute < 60:
                return time(hour, minute)
    raise ValueError("Please use a time like 13:30.")


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise ValueError("Please use a date like 2026-07-15.")


HHMM = Annotated[time, BeforeValidator(_parse_time)]
ISODate = Annotated[date, BeforeValidator(_parse_date)]


def hm(value: time | None) -> str | None:
    """time -> "13:30" for the browser."""
    return None if value is None else f"{value.hour:02d}:{value.minute:02d}"


# ---------------------------------------------------------------------- e-mail / phone
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
_PHONE = re.compile(r"^\+?[0-9][0-9 \-()]{5,18}[0-9]$")


def normalize_email(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Please enter a valid email address.")
    value = clean_text(value).lower()
    if len(value) > 254 or not _EMAIL.match(value):
        raise ValueError("Please enter a valid email address.")
    return value


def normalize_phone(value: Any) -> str | None:
    if value is None:
        return None
    value = clean_text(value)
    if value == "":
        return None
    if not _PHONE.match(value):
        raise ValueError("Please enter a valid phone number (digits, spaces, + - ( ) only).")
    return value


Email = Annotated[str, BeforeValidator(normalize_email)]
Phone = Annotated[str | None, BeforeValidator(normalize_phone)]

DAY_NAMES = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday"}
DAY_SHORT = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}
