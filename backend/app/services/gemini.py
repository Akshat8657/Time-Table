"""The ONE place that talks to Google Gemini (free tier, official REST API).

The AI is used for a single thing: to read a sentence (English / Hinglish) and PICK one function from a fixed list, copying the
words the person typed into the function's parameters.  It never sees the database, never writes SQL and never answers from
its own head - every answer is built by our own Python code from live data.

When the key is missing or the free quota is used up, `AiUnavailable` is raised and the assistant shows a friendly note - the
rest of the app is not affected in any way.  The key is sent in a header (never in a URL), and is never logged or returned.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import Settings

log = logging.getLogger("tt.ai")

QUOTA, BUSY, NETWORK, BAD_KEY, NO_KEY = "quota", "busy", "network", "bad_key", "no_key"

_RANK = {BAD_KEY: 4, QUOTA: 3, BUSY: 2, NETWORK: 1}


class AiUnavailable(Exception):
    """The AI cannot answer right now (reason: no_key / quota / busy / network / bad_key)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Decision:
    name: str                  # the chosen function ('' = the model returned no function at all)
    args: dict[str, Any]
    model: str


_client: httpx.AsyncClient | None = None
_cool: dict[str, tuple[float, str]] = {}      # model -> (monotonic time until it is skipped, why)
_key_bad_until = 0.0


def reset_state() -> None:
    """Forget every cool-down (used by the tests)."""
    global _key_bad_until
    _cool.clear()
    _key_bad_until = 0.0


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(limits=httpx.Limits(max_connections=6, max_keepalive_connections=2))
    return _client


def _ready_models(settings: Settings) -> list[str]:
    now = time.monotonic()
    return [m for m in settings.ai_models if _cool.get(m, (0.0, ""))[0] <= now]


def status(settings: Settings) -> dict[str, Any]:
    """What the screen needs to know: is the assistant usable right now, and if not, why (never anything secret)."""
    if not settings.ai_configured:
        return {"configured": False, "available": False, "reason": NO_KEY}
    if _key_bad_until > time.monotonic():
        return {"configured": True, "available": False, "reason": BAD_KEY}
    ready = _ready_models(settings)
    if ready:
        return {"configured": True, "available": True, "reason": None}
    reasons = [why for until, why in _cool.values() if until > time.monotonic()]
    worst = max(reasons, key=lambda r: _RANK.get(r, 0), default=BUSY)
    return {"configured": True, "available": False, "reason": worst}


def _retry_seconds(body: str) -> float | None:
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body)
    return float(m.group(1)) if m else None


def _cool_for(response_status: int, body: str, scale: float = 1.0) -> tuple[float, str]:
    """(seconds to leave this model alone, reason)."""
    if response_status == 429:
        if re.search(r"PerDay|per day|daily", body, re.I):
            return 30 * 60.0 * scale, QUOTA              # today's free allowance is gone: do not hammer it
        return min(max(_retry_seconds(body) or 60.0, 15.0), 600.0) * scale, QUOTA
    if response_status == 404:
        return 6 * 3600.0 * scale, BUSY                  # Google retired that model name
    if response_status in (401, 403) or (response_status == 400 and re.search(r"API key|API_KEY", body)):
        return 10 * 60.0 * scale, BAD_KEY
    return 20.0 * scale, BUSY


def _decision(data: dict[str, Any], model: str) -> Decision:
    for cand in data.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            fc = part.get("functionCall")
            if isinstance(fc, dict) and isinstance(fc.get("name"), str):
                args = fc.get("args")
                return Decision(fc["name"], args if isinstance(args, dict) else {}, model)
    return Decision("", {}, model)


async def decide(
    settings: Settings, *, system: str, turns: list[dict[str, Any]], declarations: list[dict[str, Any]],
) -> Decision:
    """Ask Gemini which function to call.  Raises AiUnavailable when it cannot be done (no key, free quota over, busy, offline)."""
    global _key_bad_until
    if not settings.ai_configured:
        raise AiUnavailable(NO_KEY)
    if _key_bad_until > time.monotonic():
        raise AiUnavailable(BAD_KEY)
    models = _ready_models(settings)
    if not models:
        raise AiUnavailable(status(settings)["reason"] or BUSY)
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": turns,
        "tools": [{"functionDeclarations": declarations}],
        "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},       # it MUST pick one of our functions - no free text
        "generationConfig": {"temperature": 0, "maxOutputTokens": 400},
    }
    headers = {"x-goog-api-key": settings.gemini_api_key, "Content-Type": "application/json"}
    worst = BUSY
    for model in models:
        url = f"{settings.gemini_base_url.rstrip('/')}/models/{model}:generateContent"
        try:
            resp = await _http().post(url, json=body, headers=headers, timeout=settings.gemini_timeout_seconds)
        except httpx.HTTPError as exc:
            log.warning("Gemini %s: network problem (%s)", model, type(exc).__name__)
            raise AiUnavailable(NETWORK) from None       # no point trying the next model when the internet is down
        if resp.status_code == 200:
            try:
                return _decision(resp.json(), model)
            except ValueError:
                log.warning("Gemini %s: unreadable answer", model)
                _cool[model] = (time.monotonic() + 20.0, BUSY)
                continue
        text = resp.text[:2000]
        seconds, why = _cool_for(resp.status_code, text, settings.gemini_cooldown_scale)
        _cool[model] = (time.monotonic() + seconds, why)
        if why == BAD_KEY:
            _key_bad_until = time.monotonic() + seconds
        log.warning("Gemini %s: HTTP %s (%s) - skipping this model for %d s", model, resp.status_code, why, seconds)
        if _RANK.get(why, 0) > _RANK.get(worst, 0):
            worst = why
        if why == BAD_KEY:
            break
    raise AiUnavailable(worst)
