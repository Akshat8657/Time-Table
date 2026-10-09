"""Makes the Web Push (VAPID) keys and stores them in the project's .env file.

Usage (from the backend folder):   python -m scripts.gen_vapid
  * does nothing if the keys are already there (an existing key is NEVER replaced - subscribers would stop working)
  * never prints the keys
"""
from __future__ import annotations

import base64
import re
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.config import ENV_FILE

SUBJECT = "mailto:admin@timetablepro.example"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def has_key(text: str, name: str) -> bool:
    return re.search(rf"^\s*{name}\s*=\s*\S+", text, re.M | re.I) is not None


def main() -> int:
    if not ENV_FILE.exists():
        print(".env file nahi mili.")
        return 1
    raw = ENV_FILE.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8-sig")
    if has_key(text, "VAPID_PRIVATE_KEY") and has_key(text, "VAPID_PUBLIC_KEY"):
        print("VAPID keys pehle se hain - kuch nahi badla.")
        return 0
    key = ec.generate_private_key(ec.SECP256R1())
    private = _b64(key.private_numbers().private_value.to_bytes(32, "big"))
    public = _b64(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    block = [
        "",
        "# ---------- WEB PUSH (browser notifications) - auto-generated, kisi ko mat dena ----------",
        f"VAPID_PUBLIC_KEY={public}",
        f"VAPID_PRIVATE_KEY={private}",
        f"VAPID_SUBJECT={SUBJECT}",
        "",
    ]
    sep = "\r\n" if crlf else "\n"
    if not text.endswith(("\n", "\r\n")):
        text += sep
    ENV_FILE.write_bytes((text + sep.join(block)).encode("utf-8"))
    print("VAPID keys bana ke .env me save kar diye (screen par dikhaye nahi).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
