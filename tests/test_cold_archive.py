from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.plugins.ai_chat.cold_archive import (
    ColdArchiveError,
    copy_verified_file,
    safe_relative_path,
    verify_gzip,
    write_verified_gzip,
)


class ColdArchiveFileTests(unittest.TestCase):
    def test_verified_copy_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            content = b"verified archive payload"
            source = root / "source.bin"
            destination = root / "archive" / "payload.bin"
            source.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()

            copy_verified_file(
                source,
                destination,
                expected_sha256=digest,
                expected_size=len(content),
            )
            copy_verified_file(
                source,
                destination,
                expected_sha256=digest,
                expected_size=len(content),
            )

            self.assertEqual(destination.read_bytes(), content)

    def test_bad_checksum_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            source.write_bytes(b"payload")
            with self.assertRaises(ColdArchiveError):
                copy_verified_file(
                    source,
                    root / "archive.bin",
                    expected_sha256="0" * 64,
                    expected_size=7,
                )

    def test_gzip_round_trip_is_verified(self) -> None:
        with TemporaryDirectory() as directory:
            content = b'{"v":1,"nodes":[]}' * 100
            digest = hashlib.sha256(content).hexdigest()
            destination = Path(directory) / "delivery.json.gz"

            write_verified_gzip(
                content,
                destination,
                expected_sha256=digest,
            )

            verify_gzip(destination, expected_sha256=digest)

    def test_relative_path_cannot_escape_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with self.assertRaises(ColdArchiveError):
                safe_relative_path(root, "../outside")


if __name__ == "__main__":
    unittest.main()
