from __future__ import annotations

import hmac
from pathlib import Path
from typing import Mapping


class CredentialFileAuthenticator:
    """Bind one bearer credential to one configured internal identity."""

    def __init__(self, credentials: Mapping[str, str | Path]) -> None:
        self.credentials = {key: Path(value) for key, value in credentials.items()}

    @staticmethod
    def _read(path: Path) -> bytes:
        raw = path.read_bytes()
        token = raw.rstrip(b"\r\n")
        if (
            len(raw) >= 515
            or not 32 <= len(token) <= 512
            or any(ch < 33 or ch > 126 for ch in token)
        ):
            raise ValueError("invalid internal credential")
        return token

    def authenticate(self, authorization: str) -> str | None:
        scheme, _, supplied = authorization.partition(" ")
        if scheme.lower() != "bearer" or not supplied:
            return None
        encoded = supplied.encode("ascii", errors="ignore")
        for identity, path in self.credentials.items():
            try:
                expected = self._read(path)
            except (OSError, ValueError):
                continue
            if hmac.compare_digest(encoded, expected):
                return identity
        return None
