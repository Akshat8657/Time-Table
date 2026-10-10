"""Settings. Everything comes from the project-level .env file - never from code."""
from __future__ import annotations

import re
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT_DIR / ".env"

# A very common copy-paste mistake: the dashboard page address instead of the Project URL.
_DASHBOARD_URL = re.compile(r"^https?://supabase\.com/dashboard/project/([a-z0-9]{20})(?:/.*)?$", re.I)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE), env_file_encoding="utf-8-sig", extra="ignore", case_sensitive=False
    )

    app_env: str = "development"

    # --- Supabase ---
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    supabase_db_url: str

    # --- first admin account ---
    seed_admin_email: str = ""
    seed_admin_name: str = "College Admin"

    # --- login sessions ---
    session_idle_timeout_hours: float = 8.0
    session_absolute_max_hours: float = 24.0
    remember_me_days: float = 30.0
    remember_me_absolute_max_days: float = 90.0

    # --- security / server ---
    frontend_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    allowed_hosts: str = "localhost,127.0.0.1"
    cookie_secure: bool = False
    login_max_failed_attempts: int = 5
    login_window_minutes: float = 15.0
    login_ip_max_attempts: int = 30
    enable_api_docs: bool = False
    # how long (seconds) a checked login session is remembered in memory. 0 = check the database on every request.
    # Logging out / deactivating / password reset always clear it at once; the delay only matters for edits made
    # directly in the database.
    session_cache_seconds: float = 5.0
    # The Supabase session pooler accepts only 15 connections IN TOTAL (this app + scripts + tests together).
    db_pool_min: int = 3
    db_pool_max: int = 6

    # --- Web Push (browser notifications). Empty = Web Push is off; the in-app bell still works. Made by scripts/gen_vapid.py ---
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@timetablepro.example"

    # --- Substitutions: how long (minutes) an auto popup-offer round waits before it alerts the HOD to assign manually.
    # Each offer also always expires at the lecture's start time, whichever comes first.
    substitution_offer_minutes: int = 10

    # --- AI assistant (Phase 7). A FREE Gemini key from Google AI Studio. Empty = the assistant says "AI abhi available nahi hai"
    # and the whole app keeps working through the normal buttons. The key is only ever read from .env - never printed, never logged.
    gemini_api_key: str = ""
    # Models are tried in this order; when one is out of free quota (or busy) the next one is used. Google retires old model
    # names from time to time, so the list can be changed in .env without touching the code.
    gemini_models: str = "gemini-3.5-flash-lite,gemini-flash-lite-latest,gemini-3.1-flash-lite"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_timeout_seconds: float = 20.0
    # 1.0 = normal. Only the automatic tests make this tiny so a pretend 'quota over' answer does not rest a model for a whole minute.
    gemini_cooldown_scale: float = 1.0
    # a person may send this many chat messages per minute (protects the free quota)
    assistant_messages_per_minute: int = 12

    # ------------------------------------------------------------------ validators
    @field_validator("supabase_url", mode="before")
    @classmethod
    def _clean_url(cls, v: str) -> str:
        v = (v or "").strip().strip('"').strip("'")
        m = _DASHBOARD_URL.match(v)
        if m:  # friendly auto-fix of the copy-paste mistake above
            v = f"https://{m.group(1).lower()}.supabase.co"
        v = v.rstrip("/")
        if not re.match(r"^https?://[^\s/]+$", v):
            raise ValueError("SUPABASE_URL should look like https://xxxxxxxx.supabase.co")
        return v

    @field_validator("gemini_api_key", "gemini_models", "gemini_base_url", mode="before")
    @classmethod
    def _clean_text(cls, v: str) -> str:
        return (v or "").strip().strip('"').strip("'").strip()

    @field_validator("supabase_anon_key", "supabase_service_role_key", "supabase_db_url", mode="before")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = (v or "").strip().strip('"').strip("'")
        if not v:
            raise ValueError("value is empty")
        return v

    @field_validator(
        "session_idle_timeout_hours", "session_absolute_max_hours", "remember_me_days",
        "remember_me_absolute_max_days", "login_window_minutes",
    )
    @classmethod
    def _positive_float(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be greater than 0")
        return v

    @field_validator("login_max_failed_attempts", "login_ip_max_attempts")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be at least 1")
        return v

    # ------------------------------------------------------------------ derived values
    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_public_key.strip() and self.vapid_private_key.strip())

    @property
    def ai_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def ai_models(self) -> list[str]:
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]

    @property
    def is_dev(self) -> bool:
        return self.app_env.lower() != "production"

    @property
    def origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.frontend_origins.split(",") if o.strip()]

    @property
    def hosts(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def auth_base(self) -> str:
        return f"{self.supabase_url}/auth/v1"

    @property
    def idle_timeout(self) -> timedelta:
        return timedelta(hours=self.session_idle_timeout_hours)

    @property
    def absolute_max(self) -> timedelta:
        return timedelta(hours=self.session_absolute_max_hours)

    @property
    def remember_idle(self) -> timedelta:
        return timedelta(days=self.remember_me_days)

    @property
    def remember_absolute_max(self) -> timedelta:
        return timedelta(days=self.remember_me_absolute_max_days)

    @property
    def login_window_seconds(self) -> float:
        return self.login_window_minutes * 60.0

    @property
    def db_params(self) -> dict:
        return parse_db_url(self.supabase_db_url)


def parse_db_url(url: str) -> dict:
    """Tolerant parser for postgresql://user:password@host:port/dbname.

    Splits on the LAST '@' so odd characters inside the password cannot break it,
    and needs no URL-encoding from the user.
    """
    rest = re.sub(r"^postgres(?:ql)?://", "", url.strip())
    at = rest.rfind("@")
    if at < 0:
        raise ValueError("SUPABASE_DB_URL is not a valid connection string")
    cred, hostpart = rest[:at], rest[at + 1:]
    user, _, password = cred.partition(":")
    hostport, _, dbname = hostpart.partition("/")
    dbname = dbname.split("?")[0] or "postgres"
    host, _, port = hostport.partition(":")
    if not host or not user:
        raise ValueError("SUPABASE_DB_URL is not a valid connection string")
    return {
        "host": host,
        "port": int(port) if port.isdigit() else 5432,
        "user": user,
        "password": password,
        "database": dbname,
    }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def explain_config_error(exc: Exception) -> str:
    """Simple Hinglish message for a non-coder when .env is wrong."""
    lines = [
        "",
        "=" * 64,
        " .env FILE ME PROBLEM HAI  (D:\\Timetable Management\\.env)",
        "=" * 64,
    ]
    errors = getattr(exc, "errors", None)
    if callable(errors):
        for e in errors():
            name = str(e.get("loc", ("?",))[0]).upper()
            lines.append(f"  - {name}: khali ya galat hai")
    else:
        lines.append(f"  - {exc}")
    lines += [
        "",
        "  Kya karna hai: .env file Notepad me kholo, upar wali line ki value theek karo,",
        "  Ctrl+S dabake save karo, phir start.bat dobara chalao.",
        "=" * 64,
        "",
    ]
    return "\n".join(lines)
