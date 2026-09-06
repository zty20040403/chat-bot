from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _targets(raw: str) -> tuple[dict[str, object], ...]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("KD_TARGETS_JSON must be valid JSON") from exc
    if not isinstance(value, list) or not value:
        raise ValueError("KD_TARGETS_JSON must contain at least one target")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Every deployer target must be an object")
        host_id = str(item.get("host_id") or "").strip()
        flake_host = str(item.get("flake_host") or host_id).strip()
        ssh_target = str(item.get("ssh_target") or "").strip()
        units = item.get("verification_units", [])
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", host_id)
            or host_id in seen
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", flake_host)
            or not re.fullmatch(
                r"(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9][A-Za-z0-9.-]{0,199}",
                ssh_target,
            )
            or not isinstance(units, list)
            or not all(
                isinstance(unit, str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,119}\.service", unit)
                for unit in units
            )
        ):
            raise ValueError("Invalid deployer target")
        seen.add(host_id)
        result.append(
            {
                "host_id": host_id,
                "flake_host": flake_host,
                "ssh_target": ssh_target,
                "verification_units": list(dict.fromkeys(units)),
                "use_remote_sudo": bool(item.get("use_remote_sudo", False)),
                "resource_version": int(item.get("resource_version") or 1),
            }
        )
        if int(result[-1]["resource_version"]) < 1:
            raise ValueError("Invalid deployer target resource_version")
    return tuple(result)


@dataclass(frozen=True)
class DeployerSettings:
    deployer_id: str
    token_file: Path
    control_url: str
    state_dir: Path
    repository_id: str
    repository_url: str
    default_branch: str
    targets: tuple[dict[str, object], ...]
    ssh_identity_file: Path
    ssh_known_hosts_file: Path
    poll_seconds: int
    command_timeout_seconds: int
    executor_host_id: str
    allow_self_deployment: bool

    @classmethod
    def from_env(cls) -> "DeployerSettings":
        control_url = os.getenv("KD_CONTROL_URL", "").strip().rstrip("/")
        repository_url = os.getenv("KD_REPOSITORY_URL", "").strip()
        parsed_control = urlsplit(control_url)
        parsed_repo = urlsplit(repository_url)
        if parsed_control.scheme not in {"http", "https"} or not parsed_control.hostname:
            raise ValueError("KD_CONTROL_URL must be an absolute HTTP(S) URL")
        if (
            parsed_repo.scheme not in {"https", "ssh"}
            and not repository_url.startswith("git@")
        ):
            raise ValueError("KD_REPOSITORY_URL must use HTTPS or SSH")
        if (
            parsed_repo.username
            or parsed_repo.password
            or parsed_repo.query
            or parsed_repo.fragment
        ):
            raise ValueError("KD_REPOSITORY_URL must not contain credentials")
        token_file_raw = os.getenv("KD_TOKEN_FILE", "").strip()
        identity_file_raw = os.getenv("KD_SSH_IDENTITY_FILE", "").strip()
        known_hosts_file_raw = os.getenv("KD_SSH_KNOWN_HOSTS_FILE", "").strip()
        settings = cls(
            deployer_id=os.getenv("KD_DEPLOYER_ID", "").strip(),
            token_file=Path(token_file_raw),
            control_url=control_url,
            state_dir=Path(
                os.getenv("KD_STATE_DIR", "/var/lib/kennethbot-cluster-deployer")
            ),
            repository_id=os.getenv("KD_REPOSITORY_ID", "").strip(),
            repository_url=repository_url,
            default_branch=os.getenv("KD_DEFAULT_BRANCH", "main").strip() or "main",
            targets=_targets(os.getenv("KD_TARGETS_JSON", "")),
            ssh_identity_file=Path(identity_file_raw),
            ssh_known_hosts_file=Path(known_hosts_file_raw),
            poll_seconds=min(max(int(os.getenv("KD_POLL_SECONDS", "5")), 1), 60),
            command_timeout_seconds=min(
                max(int(os.getenv("KD_COMMAND_TIMEOUT_SECONDS", "1800")), 60),
                7200,
            ),
            executor_host_id=os.getenv("KD_EXECUTOR_HOST_ID", "").strip(),
            allow_self_deployment=os.getenv(
                "KD_ALLOW_SELF_DEPLOYMENT", "false"
            ).strip().lower() in {"1", "true", "yes", "on"},
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.deployer_id):
            raise ValueError("KD_DEPLOYER_ID is invalid")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.repository_id):
            raise ValueError("KD_REPOSITORY_ID is invalid")
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.executor_host_id
        ):
            raise ValueError("KD_EXECUTOR_HOST_ID is invalid")
        if str(self.token_file) in {"", "."}:
            raise ValueError("KD_TOKEN_FILE is required")
        if str(self.ssh_identity_file) in {"", "."}:
            raise ValueError("KD_SSH_IDENTITY_FILE is required")
        if str(self.ssh_known_hosts_file) in {"", "."}:
            raise ValueError("KD_SSH_KNOWN_HOSTS_FILE is required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,119}", self.default_branch):
            raise ValueError("KD_DEFAULT_BRANCH is invalid")
