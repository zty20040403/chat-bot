from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class DeploymentControlClient:
    def __init__(
        self,
        base_url: str,
        token_file: Path,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token_file = token_file
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            transport=transport,
            trust_env=False,
        )

    def _headers(self) -> dict[str, str]:
        token = self.token_file.read_text(encoding="ascii").strip()
        if not 32 <= len(token) <= 512 or any(ch.isspace() for ch in token):
            raise ValueError("invalid deployer credential")
        return {"Authorization": f"Bearer {token}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self.client.request(
            method,
            self.base_url + path,
            headers=self._headers(),
            json=json,
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("deployment control returned a non-object response")
        return value

    async def claim(self) -> dict[str, Any] | None:
        return (await self._request("POST", "/v1/deployer/claim")).get("deployment")

    async def renew(self, deployment_id: str, fence: int) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/deployer/deployments/{deployment_id}/renew",
            json={"fence": int(fence)},
        )

    async def update_target(
        self,
        deployment_id: str,
        host_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/deployer/deployments/{deployment_id}/targets/{host_id}",
            json=payload,
        )

    async def complete(
        self,
        deployment_id: str,
        fence: int,
        *,
        ok: bool,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/v1/deployer/deployments/{deployment_id}/complete",
            json={"fence": int(fence), "ok": bool(ok), "result": result},
        )

    async def close(self) -> None:
        await self.client.aclose()

