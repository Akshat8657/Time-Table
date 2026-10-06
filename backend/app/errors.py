"""One error type for the whole API, so the frontend always gets {code, message}."""
from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
        clear_session_cookie: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}
        self.extra = extra or {}
        self.clear_session_cookie = clear_session_cookie


def forbidden(message: str = "You don't have permission to do that.") -> ApiError:
    return ApiError(403, "FORBIDDEN", message)
