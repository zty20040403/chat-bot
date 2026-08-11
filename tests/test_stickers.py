from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nonebot

nonebot.init()

from src.plugins.ai_chat import stickers


class StickerInventoryTests(unittest.TestCase):
    def test_inventory_lists_local_and_learned_items_without_url_secrets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "learned.json"
            asset_dir = root / "assets"
            asset_dir.mkdir()
            (asset_dir / "local.png").write_bytes(b"png")
            state_path.write_text(
                json.dumps(
                    [
                        {"type": "face", "data": {"id": "14"}},
                        {
                            "type": "image",
                            "data": {
                                "url": (
                                    "https://example.test/sticker.gif"
                                    "?token=do-not-return"
                                )
                            },
                        },
                    ]
                ),
                encoding="utf-8",
            )
            original_state = stickers._learned_stickers_state
            stickers.configure_learned_sticker_state(state_path)
            self.addCleanup(
                setattr,
                stickers,
                "_learned_stickers_state",
                original_state,
            )

            with patch.object(stickers, "STICKER_DIR", asset_dir):
                inventory = stickers.sticker_inventory()

        self.assertEqual(
            inventory["counts"],
            {
                "total": 3,
                "learned_faces": 1,
                "learned_images": 1,
                "local_images": 1,
            },
        )
        serialized = json.dumps(inventory)
        self.assertNotIn("do-not-return", serialized)
        self.assertIn("https://example.test/sticker.gif", serialized)
        self.assertIn("local.png", serialized)


if __name__ == "__main__":
    unittest.main()
