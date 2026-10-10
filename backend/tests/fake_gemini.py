"""A tiny local stand-in for Google's Gemini API (same URL, same JSON shape) so the assistant tests are deterministic and never
spend the real free quota.  The API server under test is started with GEMINI_BASE_URL pointing here (see conftest.server).

    fake.script("period 3", "free_teachers", period=3, when="today")     # a message containing that text -> that function call
    fake.fail(429)                                                       # the next request gets that HTTP status
    fake.requests                                                        # every request body the server received
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

FAKE_KEY = "fake-test-key-not-a-real-key"


class FakeGemini:
    def __init__(self) -> None:
        self.rules: list[tuple[str, dict[str, Any]]] = []
        self.requests: list[dict[str, Any]] = []
        self.models_called: list[str] = []
        self.bad_headers = 0
        self._fail: list[tuple[int, str]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self.port = 0

    # ------------------------------------------------------------------ script
    def script(self, contains: str, name: str, **args: Any) -> None:
        with self._lock:
            self.rules.insert(0, (contains.lower(), {"name": name, "args": args}))

    def fail(self, status: int, body: str = "", times: int = 1) -> None:
        with self._lock:
            self._fail += [(status, body)] * times

    def reset(self) -> None:
        with self._lock:
            self.rules.clear()
            self.requests.clear()
            self.models_called.clear()
            self._fail.clear()
            self.bad_headers = 0

    # ------------------------------------------------------------------ server
    def start(self) -> int:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = {}
                status, payload = fake._answer(self.path, self.headers.get("x-goog-api-key", ""), body)
                data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    # ------------------------------------------------------------------ the pretend model
    def _answer(self, path: str, key: str, body: dict[str, Any]) -> tuple[int, Any]:
        with self._lock:
            self.requests.append(body)
            self.models_called.append(path.split("/models/")[-1].split(":")[0])
            if key != FAKE_KEY:
                self.bad_headers += 1
                return 403, {"error": {"status": "PERMISSION_DENIED", "message": "API key not valid"}}
            if self._fail:
                status, text = self._fail.pop(0)
                return status, text or {"error": {"status": "RESOURCE_EXHAUSTED" if status == 429 else "UNAVAILABLE", "message": "scripted failure"}}
            turns = body.get("contents") or []
            last = ""
            if turns:
                last = "".join(p.get("text", "") for p in (turns[-1].get("parts") or [])).lower()
            for needle, call in self.rules:
                if needle in last:
                    return 200, {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": call}]}}]}
            return 200, {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "unsupported", "args": {}}}]}}]}

    # ------------------------------------------------------------------ helpers for the tests
    def last_user_text(self) -> str:
        turns = (self.requests[-1].get("contents") or []) if self.requests else []
        return "".join(p.get("text", "") for p in (turns[-1].get("parts") or [])) if turns else ""
