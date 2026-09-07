from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        value = default
    return min(max(value, minimum), maximum)


def _url(name: str, default: str = "") -> str:
    value = (os.getenv(name, default) or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{name} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{name} contains unsupported URL components")
    return value


@dataclass(frozen=True)
class WorkerSettings:
    worker_id: str
    token_file: Path
    control_url: str
    listen_host: str
    listen_port: int
    public_base_url: str
    state_dir: Path
    cpu_millis: int
    memory_bytes: int
    gpu_slots: int
    concurrency: int

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        worker_id = os.getenv("KW_WORKER_ID", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", worker_id):
            raise ValueError("KW_WORKER_ID is invalid")
        token_file = Path(os.getenv("KW_TOKEN_FILE", "").strip())
        if not str(token_file):
            raise ValueError("KW_TOKEN_FILE is required")
        state_dir = Path(
            os.getenv("KW_STATE_DIR", "/var/lib/gaoji-worker").strip()
        )
        return cls(
            worker_id=worker_id,
            token_file=token_file,
            control_url=_url("KW_CONTROL_URL"),
            listen_host=os.getenv("KW_LISTEN_HOST", "127.0.0.1").strip() or "127.0.0.1",
            listen_port=_int("KW_LISTEN_PORT", 8092, 1, 65535),
            public_base_url=_url("KW_PUBLIC_BASE_URL"),
            state_dir=state_dir,
            cpu_millis=_int("KW_CPU_MILLIS", 2000, 100, 128_000),
            memory_bytes=_int("KW_MEMORY_BYTES", 2 * 1024**3, 64 * 1024**2, 128 * 1024**3),
            gpu_slots=_int("KW_GPU_SLOTS", 0, 0, 16),
            concurrency=_int("KW_CONCURRENCY", 2, 1, 16),
        )
