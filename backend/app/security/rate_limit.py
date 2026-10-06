"""Login rate limiting (in memory - perfect for one backend process on localhost)."""
from __future__ import annotations

import math
import time
from collections import defaultdict, deque

from ..config import Settings
from ..errors import ApiError


class SlidingWindow:
    """Counts events per key inside a moving time window."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, window: float) -> deque[float] | None:
        q = self._events.get(key)
        if q is None:
            return None
        cutoff = time.monotonic() - window
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            self._events.pop(key, None)
            return None
        return q

    def count(self, key: str, window: float) -> int:
        q = self._prune(key, window)
        return len(q) if q else 0

    def add(self, key: str, window: float) -> None:
        if len(self._events) > 20000:  # safety valve against memory growth
            self._events.clear()
        self._prune(key, window)
        self._events[key].append(time.monotonic())

    def clear(self, key: str) -> None:
        self._events.pop(key, None)

    def seconds_until_below(self, key: str, window: float, limit: int) -> float:
        """How long until fewer than `limit` events remain in the window."""
        q = self._prune(key, window)
        if not q or len(q) < limit:
            return 0.0
        # the (len-limit+1)-th oldest event must expire
        idx = len(q) - limit
        return max(0.0, q[idx] + window - time.monotonic())


def _too_many(seconds: float) -> ApiError:
    secs = max(1, math.ceil(seconds))
    minutes = max(1, math.ceil(secs / 60))
    unit = "minute" if minutes == 1 else "minutes"
    return ApiError(
        429,
        "TOO_MANY_ATTEMPTS",
        f"Too many unsuccessful attempts. Please try again in {minutes} {unit}.",
        headers={"Retry-After": str(secs)},
        extra={"retry_after_seconds": secs},
    )


class AttemptLimiter:
    """Blocks repeated failures per account (email / user id) and per computer (IP)."""

    def __init__(self, settings: Settings, *, prefix: str) -> None:
        self._window = settings.login_window_seconds
        self._max_subject = settings.login_max_failed_attempts
        self._max_ip = settings.login_ip_max_attempts
        self._prefix = prefix
        self._hits = SlidingWindow()

    def _k_subject(self, subject: str) -> str:
        return f"{self._prefix}:s:{subject}"

    def _k_ip(self, ip: str) -> str:
        return f"{self._prefix}:ip:{ip}"

    def check(self, ip: str, subject: str) -> None:
        wait = max(
            self._hits.seconds_until_below(self._k_subject(subject), self._window, self._max_subject),
            self._hits.seconds_until_below(self._k_ip(ip), self._window, self._max_ip),
        )
        if wait > 0:
            raise _too_many(wait)

    def failure(self, ip: str, subject: str) -> None:
        self._hits.add(self._k_subject(subject), self._window)
        self._hits.add(self._k_ip(ip), self._window)

    def success(self, subject: str) -> None:
        self._hits.clear(self._k_subject(subject))
