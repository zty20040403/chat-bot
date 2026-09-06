from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class FleetStatus(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    FORBIDDEN = "forbidden"
    UNSUPPORTED = "unsupported"
    INVALID_REQUEST = "invalid_request"


@dataclass(frozen=True)
class FleetError:
    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class FleetQueryResult:
    operation: str
    status: FleetStatus
    source_backend: str
    received_at: int
    observed_at: int | None = None
    expires_at: int | None = None
    duration_ms: int | None = None
    data: Any = None
    error: FleetError | None = None
    cached: bool = False

    @property
    def ok(self) -> bool:
        return self.status in {FleetStatus.FRESH, FleetStatus.STALE}

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["ok"] = self.ok
        payload["error"] = asdict(self.error) if self.error is not None else None
        return payload
