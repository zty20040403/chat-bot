from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal


ConversationKind = Literal["group", "private"]


@dataclass(frozen=True)
class ConversationScope:
    platform: str
    kind: ConversationKind
    native_conversation_id: str
    actor_native_user_id: str = ""
    bot_native_user_id: str = ""

    def __post_init__(self) -> None:
        if not self.platform.strip():
            raise ValueError("platform must not be empty")
        if self.kind not in {"group", "private"}:
            raise ValueError("unsupported conversation kind")
        if not self.native_conversation_id.strip():
            raise ValueError("native_conversation_id must not be empty")

    @property
    def key(self) -> str:
        return (
            f"{self.platform}:{self.kind}:"
            f"{self.native_conversation_id}"
        )

    def with_actor(self, native_user_id: str | int) -> "ConversationScope":
        return replace(self, actor_native_user_id=str(native_user_id))
