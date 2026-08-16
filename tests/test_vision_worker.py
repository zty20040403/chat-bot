from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from src.plugins.ai_chat.delivery import DeliveryStore
from src.plugins.ai_chat.vision_worker import (
    VisionJob,
    VisionJobError,
    VisionResult,
    VisionWorker,
)


class SQLiteDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path

    def store_connection(self):
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection


class VisionWorkerParsingTests(unittest.TestCase):
    def test_parses_transient_summary(self) -> None:
        result = VisionWorker.parse_result(
            json.dumps(
                {
                    "summary": "雨夜街边等车的人",
                    "description": "一个人撑伞站在有霓虹灯的街边。",
                    "text": "公交站",
                    "observations": ["路面有积水", "画面偏暗"],
                    "safety": "safe",
                },
                ensure_ascii=False,
            ),
            mode="summary",
        )

        self.assertEqual(result.summary, "雨夜街边等车的人")
        self.assertEqual(result.observations, ("路面有积水", "画面偏暗"))
        self.assertEqual(result.mode, "summary")

    def test_detail_result_is_not_a_media_handle(self) -> None:
        result = VisionWorker.parse_result(
            '{"summary":"代码报错截图",'
            '"description":"终端显示连接超时。",'
            '"text":"Connection timed out",'
            '"observations":[],"safety":"review"}',
            mode="detail",
        )

        self.assertEqual(result.mode, "detail")
        self.assertNotIn("media", result.as_dict())

    def test_invalid_response_is_rejected(self) -> None:
        with self.assertRaises(VisionJobError):
            VisionWorker.parse_result("not-json", mode="summary")

    def test_only_qq_hosts_are_accepted(self) -> None:
        self.assertTrue(
            VisionWorker._supported_source("https://multimedia.nt.qq.com.cn/x.png")
        )
        self.assertFalse(
            VisionWorker._supported_source("http://127.0.0.1/private.png")
        )

    def test_completed_auto_job_is_handed_to_durable_outbox_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = SQLiteDatabase(root / "vision.sqlite3")
            connection = database.store_connection()
            connection.execute(
                """
                CREATE TABLE vision_jobs (
                    vision_job_id INTEGER PRIMARY KEY,
                    scope_key TEXT NOT NULL,
                    native_message_id TEXT NOT NULL,
                    target_platform TEXT NOT NULL,
                    target_kind TEXT NOT NULL,
                    target_native_conversation_id TEXT NOT NULL,
                    reply_to_native_message_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    auto_deliver BOOLEAN NOT NULL,
                    status TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    delivery_id INTEGER,
                    delivery_enqueued_at INTEGER,
                    updated_at INTEGER NOT NULL,
                    expires_at INTEGER
                )
                """
            )
            connection.execute(
                """
                INSERT INTO vision_jobs VALUES (
                    7, 'onebot-v11:group:123', '99', 'onebot-v11',
                    'group', '123', '99', ?, TRUE, 'succeeded',
                    '', NULL, NULL, 1, NULL
                )
                """,
                (
                    json.dumps(
                        {
                            "summary": "终端报错截图",
                            "description": "终端显示连接超时。",
                            "text": "timeout",
                            "observations": [],
                            "safety": "safe",
                            "mode": "summary",
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            connection.close()
            deliveries = DeliveryStore(root / "deliveries.sqlite3")
            self.addCleanup(deliveries.close)
            worker = VisionWorker.__new__(VisionWorker)
            worker.database = database
            worker.delivery_store = deliveries

            self.assertEqual(worker.flush_completed_deliveries(), 1)
            self.assertEqual(worker.flush_completed_deliveries(), 0)
            stored = deliveries.recent_summaries(limit=5)
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].reply_to_native_message_id, "99")


class VisionWorkerCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_analysis_uses_one_model_call_then_cache(self) -> None:
        worker = VisionWorker.__new__(VisionWorker)
        worker.cache_seconds = 600
        worker.cache_entries = 32
        worker._analysis_cache = {}
        worker._analysis_inflight = {}
        worker._cache_lock = asyncio.Lock()
        worker._cache_hits = 0
        worker._cache_misses = 0
        worker._deduplicated_requests = 0
        result = VisionResult(
            summary="终端报错截图",
            description="终端显示连接超时。",
            extracted_text="timeout",
            observations=(),
            safety="safe",
            mode="summary",
        )

        async def analyze(*_args):
            await asyncio.sleep(0.01)
            return result

        worker._request_analysis = AsyncMock(side_effect=analyze)
        job = VisionJob(1, "99", 0, "https://gchat.qpic.cn/a", "summary", "", 1)
        first, second = await asyncio.gather(
            worker._analyze_cached("same", job, b"image", "image/png"),
            worker._analyze_cached("same", job, b"image", "image/png"),
        )
        await asyncio.sleep(0)
        third = await worker._analyze_cached("same", job, b"image", "image/png")

        self.assertEqual((first, second, third), (result, result, result))
        self.assertEqual(worker._request_analysis.await_count, 1)
        self.assertEqual(worker._deduplicated_requests, 1)
        self.assertEqual(worker._cache_hits, 1)

    async def test_source_resolver_returns_fresh_qq_url(self) -> None:
        worker = VisionWorker.__new__(VisionWorker)
        worker._source_resolver = AsyncMock(
            return_value="https://multimedia.nt.qq.com.cn/fresh.png"
        )
        job = VisionJob(1, "99", 2, "https://gchat.qpic.cn/old", "detail", "", 1)

        refreshed = await worker._refresh_source(job)

        self.assertEqual(
            refreshed,
            "https://multimedia.nt.qq.com.cn/fresh.png",
        )
        worker._source_resolver.assert_awaited_once_with("99", 2)


if __name__ == "__main__":
    unittest.main()
