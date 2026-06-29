from __future__ import annotations

import json
import time
from pathlib import Path


class GroupUserProfileStore:
    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._profiles = self._load()

    def observe(
        self,
        group_id: int,
        user_id: int,
        nickname: str = "",
        card: str = "",
    ) -> None:
        group_key = str(group_id)
        user_key = str(user_id)
        nickname = " ".join(nickname.split())
        card = " ".join(card.split())
        now = int(time.time())

        group = self._profiles.setdefault(group_key, {})
        previous = group.get(user_key, {})
        should_save = (
            previous.get("nickname") != nickname
            or previous.get("card") != card
            or now - int(previous.get("last_seen", 0)) >= 300
        )
        if not should_save:
            return

        group[user_key] = {
            "nickname": nickname,
            "card": card,
            "last_seen": now,
        }
        self._save()

    def describe_user(self, group_id: int, user_id: int) -> str:
        profile = self._profiles.get(str(group_id), {}).get(str(user_id))
        if not isinstance(profile, dict):
            return f"QQ {user_id}"

        nickname = str(profile.get("nickname", "")).strip()
        card = str(profile.get("card", "")).strip()
        parts = [f"QQ {user_id}"]
        if card:
            parts.append(f"群名片“{card}”")
        if nickname and nickname != card:
            parts.append(f"QQ昵称“{nickname}”")
        return "，".join(parts)

    def render_group(self, group_id: int, max_users: int = 30) -> str:
        group = self._profiles.get(str(group_id), {})
        if not isinstance(group, dict):
            return ""

        recent_users = sorted(
            group.items(),
            key=lambda item: int(item[1].get("last_seen", 0))
            if isinstance(item[1], dict)
            else 0,
            reverse=True,
        )[:max_users]
        lines: list[str] = []
        for raw_user_id, profile in recent_users:
            if not isinstance(profile, dict):
                continue
            try:
                user_id = int(raw_user_id)
            except ValueError:
                continue
            lines.append(f"- {self.describe_user(group_id, user_id)}")
        return "\n".join(lines)

    def clear_group(self, group_id: int) -> int:
        removed = self._profiles.pop(str(group_id), {})
        self._save()
        return len(removed) if isinstance(removed, dict) else 0

    def _load(self) -> dict[str, dict[str, dict[str, object]]]:
        if not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}

        profiles: dict[str, dict[str, dict[str, object]]] = {}
        for raw_group_id, raw_group in data.items():
            if not isinstance(raw_group, dict):
                continue
            group: dict[str, dict[str, object]] = {}
            for raw_user_id, raw_profile in raw_group.items():
                if not isinstance(raw_profile, dict):
                    continue
                try:
                    group_id = str(int(raw_group_id))
                    user_id = str(int(raw_user_id))
                    last_seen = int(raw_profile.get("last_seen", 0))
                except (TypeError, ValueError):
                    continue
                group[user_id] = {
                    "nickname": str(raw_profile.get("nickname", "")),
                    "card": str(raw_profile.get("card", "")),
                    "last_seen": max(0, last_seen),
                }
                profiles[group_id] = group
        return profiles

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self._state_path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(self._profiles, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(self._state_path)
        except OSError:
            return
