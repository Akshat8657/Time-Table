"""Makes the phase backup zip:   python tools/make_backup.py Phase0   ->   D:\\TimetableBackups\\Phase0.zip

Left OUT on purpose (secret, or can be rebuilt with npm install / pip install):
  .env and .env.* (except .env.example), node_modules, .venv / venv, caches, build output, logs, other zips.
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST_DIR = Path(r"D:\TimetableBackups")

SKIP_DIRS = {
    "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", ".vite", ".git", "logs",
}
SKIP_SUFFIXES = {".pyc", ".log", ".zip", ".tsbuildinfo"}


def skip_file(name: str) -> bool:
    if name == ".env.example":
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    return Path(name).suffix.lower() in SKIP_SUFFIXES


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].replace("_", "").isalnum():
        print("Usage: python tools/make_backup.py Phase0")
        return 1
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    target = DEST_DIR / f"{sys.argv[1]}.zip"

    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for folder, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if skip_file(name):
                    continue
                path = Path(folder) / name
                zf.write(path, path.relative_to(ROOT).as_posix())
                count += 1
    print(f"Backup ready: {target}  ({count} files, {target.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
