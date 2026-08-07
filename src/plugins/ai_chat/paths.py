from __future__ import annotations

import os
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[2]


def _directory_from_env(name: str, default: Path) -> Path:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser() if value else default


STATE_DIR = _directory_from_env("AI_STATE_DIR", PACKAGE_DIR / "assets")
CACHE_DIR = _directory_from_env("AI_CACHE_DIR", PROJECT_ROOT / ".cache")
