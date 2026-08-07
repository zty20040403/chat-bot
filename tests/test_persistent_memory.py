from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.plugins.ai_chat.memory import ConversationMemory, GroupContextMemory


class PersistentMemoryTests(unittest.TestCase):
    def test_conversation_history_survives_reload_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversations.json"
            memory = ConversationMemory(2, path)
            memory.append_turn("group:1:user:2", "第一问", "第一答")
            memory.append_turn("group:1:user:2", "第二问", "第二答")

            reloaded = ConversationMemory(2, path)
            self.assertEqual(
                reloaded.get("group:1:user:2"),
                [
                    {"role": "user", "content": "第一问"},
                    {"role": "assistant", "content": "第一答"},
                    {"role": "user", "content": "第二问"},
                    {"role": "assistant", "content": "第二答"},
                ],
            )

            self.assertTrue(reloaded.clear("group:1:user:2"))
            self.assertEqual(ConversationMemory(2, path).get("group:1:user:2"), [])

    def test_conversation_history_keeps_only_configured_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversations.json"
            memory = ConversationMemory(2, path)
            for index in range(3):
                memory.append_turn("private:1", f"问{index}", f"答{index}")

            contents = [
                message["content"]
                for message in ConversationMemory(2, path).get("private:1")
            ]
            self.assertEqual(contents, ["问1", "答1", "问2", "答2"])

    def test_group_context_survives_reload_with_message_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            context = GroupContextMemory(3, 1000, path)
            context.append(
                10,
                "小明（QQ 2）",
                "测试消息",
                timestamp=1_700_000_000,
                message_id=99,
            )

            rendered = GroupContextMemory(3, 1000, path).render(10)
            self.assertIn("#99", rendered)
            self.assertIn("小明（QQ 2）: 测试消息", rendered)

            self.assertEqual(context.clear(10), 1)
            self.assertEqual(GroupContextMemory(3, 1000, path).render(10), "")

    def test_corrupt_state_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(ConversationMemory(2, path).get("anything"), [])
            self.assertEqual(GroupContextMemory(2, 1000, path).render(1), "")


if __name__ == "__main__":
    unittest.main()
