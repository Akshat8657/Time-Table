"""Password rules for NEW passwords (login itself never enforces these)."""
from __future__ import annotations

import re
import secrets

# no 0/O/1/l/I - easy to read out loud and to type
_TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def generate_temp_password() -> str:
    """Like  Kp7m-Xw3q-Rt9v  (12 random characters, at least one capital, small letter and number)."""
    while True:
        raw = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(12))
        if any(c.isupper() for c in raw) and any(c.islower() for c in raw) and any(c.isdigit() for c in raw):
            return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


_COMMON = {
    "password", "password1", "password12", "password123", "passw0rd", "12345678", "123456789", "1234567890",
    "qwerty123", "qwertyui", "iloveyou", "admin123", "admin1234", "welcome1", "welcome123", "letmein123",
    "timetable", "timetable1", "timetable123", "timetablepro", "college123", "teacher123", "abcd1234",
}


def validate_new_password(new: str, current: str, email: str) -> str | None:
    """Returns a friendly error message, or None when the password is acceptable."""
    if len(new) < 8:
        return "Use at least 8 characters."
    if len(new.encode("utf-8")) > 72:
        return "Please use at most 72 characters."
    if not re.search(r"[A-Za-z]", new):
        return "Include at least one letter."
    if not re.search(r"\d", new):
        return "Include at least one number."
    if new == current:
        return "Your new password must be different from your current one."
    lowered = new.lower()
    local = email.split("@", 1)[0].lower()
    if lowered in _COMMON or (len(local) >= 4 and local in lowered):
        return "That password is too easy to guess. Try something less common."
    return None
