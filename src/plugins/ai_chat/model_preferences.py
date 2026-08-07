from __future__ import annotations

import json
from pathlib import Path


class ModelPreferenceStore:
    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._models = self._load()

    def get(self, conversation_id: str, default: str) -> str:
        return self._models.get(conversation_id, default)

    def get_explicit(self, conversation_id: str) -> str | None:
        return self._models.get(conversation_id)

    def set(self, conversation_id: str, model: str) -> None:
        self._models[conversation_id] = model
        self._save()

    def clear(self, conversation_id: str) -> bool:
        removed = self._models.pop(conversation_id, None)
        if removed is None:
            return False
        self._save()
        return True

    def _load(self) -> dict[str, str]:
        if not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(key): value
            for key, value in data.items()
            if isinstance(value, str) and value.strip()
        }

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self._state_path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(self._models, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(self._state_path)
        except OSError:
            return
