from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from src.plugins.ai_chat.video_analysis import DeepVideoAnalyzer


class DeepVideoAnalyzerTests(unittest.IsolatedAsyncioTestCase):
    async def test_qq_video_uses_frames_transcript_and_vision(self) -> None:
        with TemporaryDirectory() as directory:
            model = Path(directory) / "whisper.bin"
            model.write_bytes(b"model")
            vision_worker = Mock()
            vision_worker.analyze_video_frames = AsyncMock(
                return_value={"summary": "测试视频", "key_points": ["重点"]}
            )
            with patch(
                "src.plugins.ai_chat.video_analysis.shutil.which",
                return_value="/bin/tool",
            ):
                analyzer = DeepVideoAnalyzer(
                    Mock(),
                    vision_worker,
                    whisper_model_path=str(model),
                )

            async def fake_download(_url: str, target: Path) -> None:
                target.write_bytes(b"video")

            analyzer._download_qq_media = fake_download  # type: ignore[method-assign]
            analyzer._probe_duration = AsyncMock(return_value=120)  # type: ignore[method-assign]
            analyzer._extract_frames = AsyncMock(  # type: ignore[method-assign]
                return_value=[(0, b"jpeg"), (60, b"jpeg2")]
            )
            analyzer._transcribe_audio = AsyncMock(  # type: ignore[method-assign]
                return_value="这是一段测试音轨"
            )

            result = await analyzer.analyze_qq_video(
                "https://multimedia.nt.qq.com.cn/test.mp4",
                question="评价内容",
            )

        self.assertEqual(result["source"], "qq_video")
        self.assertEqual(result["duration_seconds"], 120)
        self.assertEqual(result["frame_count"], 2)
        self.assertEqual(result["transcript"], "这是一段测试音轨")
        vision_worker.analyze_video_frames.assert_awaited_once()

    def test_qq_video_rejects_untrusted_host(self) -> None:
        self.assertFalse(
            DeepVideoAnalyzer._is_allowed_qq_media_url(
                "https://example.com/private.mp4"
            )
        )
        self.assertTrue(
            DeepVideoAnalyzer._is_allowed_qq_media_url(
                "https://multimedia.nt.qq.com.cn/video.mp4"
            )
        )
