from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx


class FleetControlError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class FleetControlClient:
    def __init__(
        self,
        base_url: str,
        token_file: str | Path,
        *,
        timeout_seconds: float = 12.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized = base_url.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Fleet control URL must be absolute HTTP(S)")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Fleet control URL contains unsupported components")
        self.base_url = normalized
        self.token_file = Path(token_file)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(min(max(float(timeout_seconds), 1.0), 30.0)),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _token(self) -> str:
        try:
            raw = self.token_file.read_bytes()
        except OSError as exc:
            raise FleetControlError(
                "credential_unavailable", "Fleet control credential is unavailable"
            ) from exc
        token = raw.rstrip(b"\r\n")
        if (
            len(raw) >= 515
            or not 32 <= len(token) <= 512
            or any(byte < 33 or byte > 126 for byte in token)
        ):
            raise FleetControlError(
                "credential_invalid", "Fleet control credential is invalid"
            )
        return token.decode("ascii")

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            async with self._client.stream(
                "GET",
                f"{self.base_url}{path}",
                headers={
                    "Authorization": f"Bearer {self._token()}",
                    "Accept": "application/json",
                },
            ) as response:
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise FleetControlError(
                            "response_too_large",
                            "Fleet control response exceeds 2 MiB",
                        )
                    chunks.append(chunk)
                if response.status_code == 401:
                    raise FleetControlError(
                        "unauthorized", "Fleet control rejected the credential"
                    )
                if response.status_code == 403:
                    raise FleetControlError("forbidden", "Fleet access was denied")
                if response.status_code == 404:
                    raise FleetControlError(
                        "unsupported", "Fleet capability is unavailable"
                    )
                if response.status_code in {408, 429}:
                    raise FleetControlError(
                        "upstream_busy",
                        "Fleet control is temporarily busy",
                        retryable=True,
                    )
                if response.status_code >= 500:
                    raise FleetControlError(
                        "upstream_error",
                        f"Fleet control returned HTTP {response.status_code}",
                        retryable=True,
                    )
                if response.status_code >= 400:
                    raise FleetControlError(
                        "invalid_request",
                        f"Fleet control returned HTTP {response.status_code}",
                    )
                raw = b"".join(chunks)
        except FleetControlError:
            raise
        except httpx.TimeoutException as exc:
            raise FleetControlError(
                "timeout", "Fleet control request timed out", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise FleetControlError(
                "transport_unavailable",
                "Fleet control is unavailable",
                retryable=True,
            ) from exc
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FleetControlError(
                "invalid_response", "Fleet control returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise FleetControlError(
                "invalid_response", "Fleet control returned an invalid object"
            )
        return payload

    async def fleet(self) -> dict[str, Any]:
        return await self._get("/v1/fleet")

    async def capabilities(self) -> dict[str, Any]:
        return await self._get("/v1/capabilities")

    async def backends(self) -> dict[str, Any]:
        return await self._get("/v1/backends")

    async def observations(self, *, limit: int = 50) -> dict[str, Any]:
        return await self._get(f"/v1/observations?limit={min(max(limit, 1), 200)}")

    async def host(self, host_id: str) -> dict[str, Any]:
        return await self._get(f"/v1/hosts/{quote(host_id, safe='')}")

    async def unit(self, host_id: str, unit: str) -> dict[str, Any]:
        return await self._get(
            f"/v1/hosts/{quote(host_id, safe='')}/units/{quote(unit, safe='')}"
        )

    async def logs(
        self,
        host_id: str,
        unit: str,
        *,
        lines: int = 50,
        since_seconds: int = 3600,
    ) -> dict[str, Any]:
        return await self._get(
            f"/v1/hosts/{quote(host_id, safe='')}/units/"
            f"{quote(unit, safe='')}/logs?lines={min(max(lines, 1), 200)}"
            f"&since_seconds={min(max(since_seconds, 1), 86400)}"
        )

    async def alerts(self) -> dict[str, Any]:
        return await self._get("/v1/alerts")
