from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.bot_storage.media_cleanup import LegacyMediaCleanup, MediaCleanupError


class SQLiteDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path

    def store_connection(self):
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection


class LegacyMediaCleanupTests(unittest.TestCase):
    def test_preview_token_and_apply_preserve_stickers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media_root = root / "media"
            ordinary_file = media_root / "blobs" / "ordinary.png"
            sticker_file = media_root / "blobs" / "sticker.png"
            ordinary_file.parent.mkdir(parents=True)
            ordinary_file.write_bytes(b"ordinary")
            sticker_file.write_bytes(b"sticker")
            database = SQLiteDatabase(root / "cleanup.sqlite3")
            self._create_schema(database)
            connection = database.store_connection()
            connection.execute(
                "INSERT INTO media_blobs VALUES (1, 'a', 8, 'blobs/ordinary.png', '', '')"
            )
            connection.execute(
                "INSERT INTO media_blobs VALUES (2, 'b', 7, 'blobs/sticker.png', '', '')"
            )
            connection.execute(
                "INSERT INTO message_media VALUES (1, 1, 'image')"
            )
            connection.execute(
                "INSERT INTO message_media VALUES (2, 2, 'sticker')"
            )
            connection.execute("INSERT INTO sticker_library VALUES (2)")
            connection.execute(
                "INSERT INTO semantic_documents VALUES ('media', 'media#1')"
            )
            connection.close()
            cleanup = LegacyMediaCleanup(
                database,  # type: ignore[arg-type]
                media_root=media_root,
            )

            preview = cleanup.preview()
            self.assertEqual(preview["candidate_count"], 1)
            self.assertEqual(preview["candidate_bytes"], 8)
            with self.assertRaises(MediaCleanupError):
                cleanup.apply("wrong")

            report = cleanup.apply(str(preview["confirmation_token"]))

            self.assertEqual(report["status"], "completed")
            self.assertEqual(report["deleted_count"], 1)
            self.assertFalse(ordinary_file.exists())
            self.assertTrue(sticker_file.exists())
            self.assertEqual(cleanup.preview()["candidate_count"], 0)

    def test_cleanup_paths_cannot_escape_root(self) -> None:
        root = Path("/tmp/media-cleanup-root").resolve()
        with self.assertRaises(MediaCleanupError):
            LegacyMediaCleanup._safe_path(root, "../outside")

    @staticmethod
    def _create_schema(database: SQLiteDatabase) -> None:
        connection = database.store_connection()
        connection.executescript(
            """
            CREATE TABLE media_blobs (
                media_id INTEGER PRIMARY KEY,
                sha256 TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                storage_path TEXT NOT NULL,
                prepared_path TEXT NOT NULL,
                archive_path TEXT NOT NULL
            );
            CREATE TABLE message_media (
                message_media_id INTEGER PRIMARY KEY,
                media_id INTEGER NOT NULL,
                media_kind TEXT NOT NULL
            );
            CREATE TABLE sticker_library (media_id INTEGER PRIMARY KEY);
            CREATE TABLE semantic_documents (
                source_type TEXT NOT NULL,
                source_handle TEXT NOT NULL
            );
            CREATE TABLE media_cleanup_runs (
                cleanup_id INTEGER PRIMARY KEY AUTOINCREMENT,
                cleanup_kind TEXT NOT NULL,
                confirmation_token TEXT NOT NULL,
                candidate_count INTEGER NOT NULL,
                candidate_bytes INTEGER NOT NULL,
                deleted_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                report_json TEXT NOT NULL,
                requested_at INTEGER NOT NULL,
                finished_at INTEGER
            );
            """
        )
        connection.close()


if __name__ == "__main__":
    unittest.main()
