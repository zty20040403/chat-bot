from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import nonebot

nonebot.init()

from src.plugins.ai_chat.media_library import (
    MediaLibrary,
    MediaLibraryError,
    MediaRecord,
)


class MediaLibraryParsingTests(unittest.TestCase):
    def test_parses_structured_vision_response(self) -> None:
        result = MediaLibrary._parse_analysis(
            json.dumps(
                {
                    "summary": "卷毛小狗悠闲躺窝",
                    "description": "棕色卷毛小狗四脚朝天地躺在宠物窝中。",
                    "text": "",
                    "emotion": ["放松", "可爱"],
                    "usage": ["卖萌", "表达惬意"],
                    "is_sticker": False,
                    "contains_person": False,
                    "contains_private_info": False,
                    "safety": "safe",
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(result["summary"], "卷毛小狗悠闲躺窝")
        self.assertEqual(result["emotion"], ["放松", "可爱"])
        self.assertEqual(result["safety"], "safe")

    def test_invalid_or_incomplete_response_is_rejected(self) -> None:
        with self.assertRaises(MediaLibraryError):
            MediaLibrary._parse_analysis("not-json")
        with self.assertRaises(MediaLibraryError):
            MediaLibrary._parse_analysis('{"summary":"只有标题"}')

    def test_unknown_safety_is_downgraded_to_review(self) -> None:
        result = MediaLibrary._parse_analysis(
            '{"summary":"测试图片标签","description":"一张测试图片。",'
            '"safety":"maybe"}'
        )
        self.assertEqual(result["safety"], "review")

    def test_storage_path_cannot_escape_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / "outside-media-test.jpg"
            outside.write_bytes(b"test")
            library = object.__new__(MediaLibrary)
            library.root = root.resolve()
            try:
                with self.assertRaises(MediaLibraryError):
                    library._resolve_storage_path(str(outside))
            finally:
                outside.unlink(missing_ok=True)

    def test_mime_sniffing_accepts_known_images_only(self) -> None:
        self.assertEqual(MediaLibrary._sniff_mime(b"\xff\xd8\xffx"), "image/jpeg")
        self.assertEqual(
            MediaLibrary._sniff_mime(b"\x89PNG\r\n\x1a\n"),
            "image/png",
        )
        self.assertEqual(
            MediaLibrary._sniff_mime(b"plain text"),
            "application/octet-stream",
        )

    def test_only_qq_image_hosts_are_downloadable(self) -> None:
        self.assertTrue(
            MediaLibrary._supported_source("https://multimedia.nt.qq.com.cn/a.jpg")
        )
        self.assertTrue(
            MediaLibrary._supported_source("https://gchat.qpic.cn/a.jpg")
        )
        self.assertFalse(
            MediaLibrary._supported_source("http://127.0.0.1/private")
        )
        self.assertFalse(
            MediaLibrary._supported_source("https://qq.com.example.test/a.jpg")
        )

    def test_catgirl_alias_ranks_cat_ears_above_unrelated_cute_sticker(self) -> None:
        def record(media_id: int, summary: str) -> MediaRecord:
            return MediaRecord(
                media_id=media_id,
                handle=f"media#{media_id}",
                summary=summary,
                description="",
                extracted_text="",
                emotions=(),
                usage=(),
                is_sticker=True,
                safety="safe",
                storage_path=Path("unused"),
                mime_type="image/png",
            )

        terms = MediaLibrary._sticker_query_terms("发个猫娘卖萌表情")
        catgirl = MediaLibrary._sticker_relevance(
            record(1, "可爱猫耳精灵少女卖萌"),
            terms,
        )
        rabbit = MediaLibrary._sticker_relevance(
            record(2, "粉色兔耳少女闭眼卖萌"),
            terms,
        )
        required = MediaLibrary._sticker_required_alias_groups(
            "发个猫娘卖萌表情"
        )

        self.assertGreater(catgirl, rabbit)
        self.assertTrue(
            MediaLibrary._record_matches_sticker_groups(
                record(1, "可爱猫耳精灵少女卖萌"),
                required,
            )
        )
        self.assertFalse(
            MediaLibrary._record_matches_sticker_groups(
                record(2, "粉色兔耳少女闭眼卖萌"),
                required,
            )
        )

    def test_generic_sticker_request_is_treated_as_random(self) -> None:
        self.assertEqual(
            MediaLibrary._sticker_query_terms("给我发个表情包"),
            (),
        )


if __name__ == "__main__":
    unittest.main()
