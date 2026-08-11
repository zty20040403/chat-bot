from __future__ import annotations

from src.bot_storage import StateSource, open_json_state


class ModelPreferenceStore:
    def __init__(self, state_path: StateSource) -> None:
        self._state = open_json_state(state_path, "model_preferences")
        self._models = self._load()

    def get(self, conversation_id: str, default: str) -> str:
        return self._models.get(conversation_id, default)

    def get_explicit(self, conversation_id: str) -> str | None:
        return self._models.get(conversation_id)

    def items(self) -> list[tuple[str, str]]:
        return sorted(self._models.items())

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
        data = self._state.load()
        if not isinstance(data, dict):
            return {}
        return {
            str(key): value
            for key, value in data.items()
            if isinstance(value, str) and value.strip()
        }

    def _save(self) -> None:
        self._state.save(self._models)
