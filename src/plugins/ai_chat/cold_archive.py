from __future__ import annotations

import asyncio
import gzip
import hashlib
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from src.bot_storage import DatabaseError, PostgresDatabase


EMPTY_MESSAGE_BODY_JSON = '{"v":1,"nodes":[]}'


class ColdArchiveError(RuntimeError):
    pass


class ColdArchiveService:
    """Automatically tier immutable payloads from h610 to the tank archive."""

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        media_root: Path,
        archive_root: Path,
        media_retention_days: int = 30,
        delivery_retention_days: int = 7,
        delivery_min_bytes: int = 1024 * 1024,
        interval_seconds: int = 60,
        batch_size: int = 20,
        warning: Callable[[str], object] | None = None,
    ) -> None:
        self.database = database
        self.media_root = media_root.expanduser().resolve()
        self.archive_root = archive_root.expanduser().resolve()
        self.media_retention_seconds = max(int(media_retention_days), 1) * 86400
        self.delivery_retention_seconds = (
            max(int(delivery_retention_days), 1) * 86400
        )
        self.delivery_min_bytes = max(int(delivery_min_bytes), 4096)
        self.interval_seconds = max(int(interval_seconds), 15)
        self.batch_size = min(max(int(batch_size), 1), 200)
        self.warning = warning
        self._closed = False
        self._wake = asyncio.Event()
        self.last_error = ""
        self.last_run_at = 0

    async def close(self) -> None:
        self._closed = True
        self._wake.set()

    async def run_forever(self) -> None:
        while not self._closed:
            try:
                await asyncio.to_thread(self.run_once)
                self.last_error = ""
            except (ColdArchiveError, DatabaseError, OSError) as exc:
                detail = str(exc)[:1000]
                if detail != self.last_error and self.warning is not None:
                    self.warning(f"Cold archive is temporarily unavailable: {detail}")
                self.last_error = detail
            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self.interval_seconds,
                )
            except TimeoutError:
                pass

    def run_once(self, *, now: int | None = None) -> dict[str, int]:
        timestamp = int(time.time() if now is None else now)
        self._ensure_archive_root()
        result = {
            "media_archived": self._archive_media(timestamp),
            "deliveries_archived": self._archive_deliveries(timestamp),
            "media_evicted": self._evict_media(timestamp),
        }
        self.last_run_at = timestamp
        return result

    def _archive_media(self, now: int) -> int:
        connection = self.database.store_connection()
        try:
            rows = connection.execute(
                """
                SELECT media_id, sha256, byte_size, storage_path
                FROM media_blobs
                WHERE (archived_at IS NULL OR archive_path = '')
                  AND local_deleted_at IS NULL
                  AND status = 'ready'
                ORDER BY media_id
                LIMIT ?
                """,
                (self.batch_size,),
            ).fetchall()
        finally:
            connection.close()

        archived: list[tuple[str, int, int]] = []
        missing: list[int] = []
        for row in rows:
            source = safe_relative_path(self.media_root, str(row["storage_path"]))
            if not source.is_file():
                missing.append(int(row["media_id"]))
                continue
            sha256 = str(row["sha256"])
            relative = Path("media") / str(row["storage_path"])
            destination = safe_relative_path(self.archive_root, str(relative))
            copy_verified_file(
                source,
                destination,
                expected_sha256=sha256,
                expected_size=int(row["byte_size"]),
            )
            archived.append((str(relative), now, int(row["media_id"])))

        if not archived and not missing:
            return 0
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            for archive_path, archived_at, media_id in archived:
                cursor.execute(
                    """
                    UPDATE media_blobs
                    SET archive_path = ?, archived_at = ?,
                        last_accessed_at = COALESCE(last_accessed_at, last_seen_at)
                    WHERE media_id = ?
                      AND (archived_at IS NULL OR archive_path = '')
                    """,
                    (archive_path, archived_at, media_id),
                )
            for media_id in missing:
                cursor.execute(
                    """
                    UPDATE media_blobs SET status = 'missing'
                    WHERE media_id = ? AND status = 'ready'
                      AND (archived_at IS NULL OR archive_path = '')
                    """,
                    (media_id,),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return len(archived)

    def _archive_deliveries(self, now: int) -> int:
        cutoff = now - self.delivery_retention_seconds
        connection = self.database.store_connection()
        try:
            rows = connection.execute(
                """
                SELECT delivery_id, body_json
                FROM deliveries
                WHERE body_archived_at IS NULL
                  AND status IN ('committed', 'cancelled')
                  AND updated_at <= ?
                  AND octet_length(body_json) >= ?
                ORDER BY delivery_id
                LIMIT ?
                """,
                (cutoff, self.delivery_min_bytes, self.batch_size),
            ).fetchall()
        finally:
            connection.close()

        archived: list[tuple[str, str, int, int, int]] = []
        for row in rows:
            delivery_id = int(row["delivery_id"])
            body = str(row["body_json"]).encode("utf-8")
            sha256 = hashlib.sha256(body).hexdigest()
            relative = Path("deliveries") / sha256[:2] / (
                f"delivery-{delivery_id}-{sha256}.json.gz"
            )
            destination = safe_relative_path(self.archive_root, str(relative))
            write_verified_gzip(body, destination, expected_sha256=sha256)
            archived.append((str(relative), sha256, len(body), now, delivery_id))

        if not archived:
            return 0
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            for archive_path, sha256, size, archived_at, delivery_id in archived:
                cursor.execute(
                    """
                    UPDATE deliveries
                    SET body_archive_path = ?, body_archive_sha256 = ?,
                        body_size_bytes = ?, body_archived_at = ?, body_json = ?
                    WHERE delivery_id = ? AND body_archived_at IS NULL
                      AND status IN ('committed', 'cancelled')
                    """,
                    (
                        archive_path,
                        sha256,
                        size,
                        archived_at,
                        EMPTY_MESSAGE_BODY_JSON,
                        delivery_id,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return len(archived)

    def _evict_media(self, now: int) -> int:
        cutoff = now - self.media_retention_seconds
        connection = self.database.store_connection()
        try:
            rows = connection.execute(
                """
                SELECT blob.media_id, blob.sha256, blob.byte_size,
                       blob.storage_path, blob.archive_path
                FROM media_blobs AS blob
                WHERE blob.archived_at IS NOT NULL
                  AND blob.archive_path <> ''
                  AND blob.local_deleted_at IS NULL
                  AND GREATEST(
                      blob.last_seen_at,
                      COALESCE(blob.last_accessed_at, blob.last_seen_at)
                  ) <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM sticker_library AS sticker
                      WHERE sticker.media_id = blob.media_id
                        AND sticker.enabled = 1 AND sticker.banned = 0
                  )
                ORDER BY blob.media_id
                LIMIT ?
                """,
                (cutoff, self.batch_size),
            ).fetchall()
        finally:
            connection.close()

        evicted: list[tuple[int, int]] = []
        for row in rows:
            archive = safe_relative_path(
                self.archive_root,
                str(row["archive_path"]),
            )
            verify_file(
                archive,
                expected_sha256=str(row["sha256"]),
                expected_size=int(row["byte_size"]),
            )
            local = safe_relative_path(self.media_root, str(row["storage_path"]))
            if local.exists():
                local.unlink()
            evicted.append((now, int(row["media_id"])))

        if not evicted:
            return 0
        connection = self.database.store_connection()
        cursor = connection.cursor()
        try:
            for deleted_at, media_id in evicted:
                cursor.execute(
                    """
                    UPDATE media_blobs SET local_deleted_at = ?
                    WHERE media_id = ? AND local_deleted_at IS NULL
                    """,
                    (deleted_at, media_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return len(evicted)

    def _ensure_archive_root(self) -> None:
        self.archive_root.mkdir(parents=True, exist_ok=True)
        if not os.access(self.archive_root, os.R_OK | os.W_OK | os.X_OK):
            raise ColdArchiveError("archive root is not readable and writable")


def safe_relative_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ColdArchiveError("archive path escaped its configured root")
    return candidate


def copy_verified_file(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_size: int,
) -> None:
    if destination.is_file():
        verify_file(
            destination,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open("rb") as input_file, temporary.open("wb") as output_file:
            while chunk := input_file.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                output_file.write(chunk)
            output_file.flush()
            os.fsync(output_file.fileno())
        if digest.hexdigest() != expected_sha256 or size != expected_size:
            raise ColdArchiveError("source changed while it was being archived")
        os.replace(temporary, destination)
        verify_file(
            destination,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )
    finally:
        temporary.unlink(missing_ok=True)


def verify_file(
    path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
) -> None:
    if not path.is_file() or path.stat().st_size != expected_size:
        raise ColdArchiveError("archived file size does not match")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ColdArchiveError("archived file checksum does not match")


def write_verified_gzip(
    content: bytes,
    destination: Path,
    *,
    expected_sha256: str,
) -> None:
    if destination.is_file():
        verify_gzip(destination, expected_sha256=expected_sha256)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                compressed.write(content)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, destination)
        verify_gzip(destination, expected_sha256=expected_sha256)
    finally:
        temporary.unlink(missing_ok=True)


def verify_gzip(path: Path, *, expected_sha256: str) -> None:
    if not path.is_file():
        raise ColdArchiveError("archived delivery body is missing")
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ColdArchiveError("archived delivery checksum does not match")
