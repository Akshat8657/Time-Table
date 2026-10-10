"""Phase 8 production readiness: everything that differs between the laptop and the internet is a SETTING, and a production
server with laptop settings refuses to start."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings

BACKEND = Path(__file__).resolve().parents[1]


def make(**kw) -> Settings:
    base = dict(supabase_url="https://abcdefghijklmnopqrst.supabase.co", supabase_anon_key="a", supabase_service_role_key="b",
                supabase_db_url="postgresql://u:p@h:5432/d")
    base.update(kw)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_development_defaults_are_the_laptop_ones():
    s = make()
    assert not s.is_production and not s.secure_cookies and s.cookie_samesite == "lax" and not s.trust_proxy_headers
    assert s.max_request_bytes == 8 * 1024 * 1024 and s.read_cache_seconds == 30 and s.db_pool_max <= 10


def test_production_forces_secure_cookies_and_lists_unsafe_settings():
    s = make(app_env="production")
    assert s.is_production and s.secure_cookies                       # even though COOKIE_SECURE was not set
    problems = s.production_problems()
    assert any("localhost" in p for p in problems)                     # the default origins are the laptop ones
    good = make(app_env="production", frontend_origins="https://timetable.example.edu", allowed_hosts="api.example.edu")
    assert good.production_problems() == []
    assert any("http://" in p for p in make(app_env="production", frontend_origins="http://timetable.example.edu").production_problems())
    assert any("ENABLE_API_DOCS" in p for p in make(app_env="production", frontend_origins="https://t.example.edu", enable_api_docs=True).production_problems())


def test_samesite_is_checked_and_none_needs_https():
    assert make(cookie_samesite="NONE").cookie_samesite == "none" and make(cookie_samesite="none").secure_cookies
    with pytest.raises(Exception):
        make(cookie_samesite="sometimes")


def test_a_production_server_with_laptop_settings_does_not_start():
    env = {**os.environ, "APP_ENV": "production", "FRONTEND_ORIGINS": "http://localhost:5173"}
    r = subprocess.run([sys.executable, "-c", "import app.main"], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 1 and "APP_ENV=production" in r.stdout and "localhost" in r.stdout
    assert "eyJ" not in r.stdout and "sb_secret" not in r.stdout          # no secret is ever printed


def test_production_answers_carry_hsts():
    env = {**os.environ, "APP_ENV": "production", "FRONTEND_ORIGINS": "https://timetable.example.edu", "ALLOWED_HOSTS": "testserver,localhost"}
    code = (
        "import asyncio, httpx\n"
        "from app.main import app\n"
        "async def go():\n"
        "    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as c:\n"
        "        r = await c.get('/api/nope')\n"
        "        print('HSTS=' + r.headers.get('strict-transport-security', 'none'))\n"
        "asyncio.run(go())\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert "HSTS=max-age=31536000" in r.stdout, r.stdout + r.stderr


def test_every_setting_is_documented_in_the_sample_file():
    sample = (BACKEND.parent / ".env.example").read_text(encoding="utf-8")
    documented = {line.split("=", 1)[0].strip().lower() for line in sample.splitlines() if "=" in line and not line.lstrip().startswith("#")}
    missing = [name for name in Settings.model_fields if name not in documented]
    assert missing == [], missing
    for line in sample.splitlines():
        if "=" in line and not line.startswith("#"):
            key = line.split("=", 1)[0]
            if key.endswith(("KEY", "PASSWORD")) and "your-" not in line and line.split("=", 1)[1].strip():
                raise AssertionError(f"{key} has a value in the sample file")
