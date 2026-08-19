"""SQLite implementation of :class:`FileRepository` — the local, no-cloud backend.

The `tags` map and `detections` list are stored as JSON string columns. SQL
cannot natively do "map contains key X with value >= N" across multiple keys, so
the AND filtering is done in the service layer (see `app/services/query_service.py`).
This is deliberately the *same* architecture as DynamoDB, where tag queries are a
`Scan` + in-memory filter — so behaviour is identical between local and cloud.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.repository.base import DuplicateError, FileRepository
from app.schemas import FileRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    file_id              TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL,
    file_type            TEXT NOT NULL,
    object_key           TEXT NOT NULL,
    thumbnail_key        TEXT,
    filename             TEXT NOT NULL DEFAULT '',
    content_type         TEXT NOT NULL DEFAULT '',
    size_bytes           INTEGER NOT NULL DEFAULT 0,
    tags_json            TEXT NOT NULL DEFAULT '{}',
    detections_json      TEXT NOT NULL DEFAULT '[]',
    model_version        TEXT NOT NULL DEFAULT '',
    checksum             TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'completed',
    error_code           TEXT,
    message              TEXT,
    processing_sequencer TEXT,
    lease_expires_at     TEXT,
    upload_time          TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_checksum ON files(user_id, checksum);
CREATE INDEX IF NOT EXISTS idx_thumbnail ON files(thumbnail_key);
CREATE INDEX IF NOT EXISTS idx_object ON files(object_key);
"""

_COLUMNS = (
    "file_id", "user_id", "file_type", "object_key", "thumbnail_key",
    "filename", "content_type", "size_bytes", "tags_json", "detections_json",
    "model_version", "checksum", "status", "error_code", "message",
    "processing_sequencer", "lease_expires_at", "upload_time",
)


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _serialise(record: FileRecord) -> tuple:
    return (
        record.file_id,
        record.user_id,
        record.file_type,
        record.object_key,
        record.thumbnail_key,
        record.filename,
        record.content_type,
        record.size_bytes,
        json.dumps(record.tags, sort_keys=True),
        json.dumps(record.detections),
        record.model_version,
        record.checksum,
        record.status,
        record.error_code,
        record.message,
        record.processing_sequencer,
        record.lease_expires_at.isoformat() if record.lease_expires_at else None,
        record.upload_time.isoformat(),
    )


def _deserialise(row: sqlite3.Row) -> FileRecord:
    return FileRecord(
        file_id=row["file_id"],
        user_id=row["user_id"],
        file_type=row["file_type"],
        object_key=row["object_key"],
        thumbnail_key=row["thumbnail_key"],
        filename=row["filename"],
        content_type=row["content_type"],
        size_bytes=row["size_bytes"],
        tags=json.loads(row["tags_json"]),
        detections=json.loads(row["detections_json"]),
        model_version=row["model_version"],
        checksum=row["checksum"],
        status=row["status"],
        error_code=row["error_code"],
        message=row["message"],
        processing_sequencer=row["processing_sequencer"],
        lease_expires_at=_dt(row["lease_expires_at"]),
        upload_time=_dt(row["upload_time"]),
    )


class SQLiteRepository(FileRepository):
    def __init__(self, db_path: str = "data/pacific_bioarchive.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- writes -----------------------------------------------------------
    def add(self, record: FileRecord) -> None:
        try:
            self._conn.execute(
                "INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                _serialise(record),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            existing = self.find_by_user_checksum(
                record.user_id, record.checksum
            ) or self.get(record.file_id)
            raise DuplicateError(existing.file_id if existing else None) from exc

    def update_tags(self, file_id: str, tags: dict[str, int]) -> None:
        self._conn.execute(
            "UPDATE files SET tags_json=? WHERE file_id=?",
            (json.dumps(tags, sort_keys=True), file_id),
        )
        self._conn.commit()

    def mark_processing(
        self, file_id: str, sequencer: str, lease_expires_at: datetime
    ) -> None:
        self._conn.execute(
            "UPDATE files SET status='processing', processing_sequencer=?, "
            "lease_expires_at=?, error_code=NULL, message=NULL WHERE file_id=?",
            (sequencer, lease_expires_at.isoformat(), file_id),
        )
        self._conn.commit()

    def mark_completed(
        self,
        file_id: str,
        original_key: str,
        thumbnail_key: Optional[str],
        file_type: str,
        tags: dict[str, int],
        detections: list[dict],
        model_version: str,
    ) -> None:
        self._conn.execute(
            "UPDATE files SET status='completed', object_key=?, thumbnail_key=?, "
            "file_type=?, tags_json=?, detections_json=?, model_version=?, "
            "processing_sequencer=NULL, lease_expires_at=NULL, error_code=NULL, "
            "message=NULL WHERE file_id=?",
            (
                original_key,
                thumbnail_key,
                file_type,
                json.dumps(tags, sort_keys=True),
                json.dumps(detections),
                model_version,
                file_id,
            ),
        )
        self._conn.commit()

    def mark_failed(self, file_id: str, error_code: str, message: str) -> None:
        self._conn.execute(
            "UPDATE files SET status='failed', error_code=?, message=?, "
            "processing_sequencer=NULL, lease_expires_at=NULL WHERE file_id=?",
            (error_code, message, file_id),
        )
        self._conn.commit()

    def delete_by_ids(self, file_ids: list[str]) -> int:
        if not file_ids:
            return 0
        cur = self._conn.execute(
            f"DELETE FROM files WHERE file_id IN ({','.join('?' * len(file_ids))})",
            file_ids,
        )
        self._conn.commit()
        return cur.rowcount

    # -- reads ------------------------------------------------------------
    def all(self) -> list[FileRecord]:
        rows = self._conn.execute("SELECT * FROM files").fetchall()
        return [_deserialise(r) for r in rows]

    def get(self, file_id: str) -> Optional[FileRecord]:
        row = self._conn.execute(
            "SELECT * FROM files WHERE file_id=?", (file_id,)
        ).fetchone()
        return _deserialise(row) if row else None

    def by_thumbnail_key(self, key: str) -> Optional[FileRecord]:
        row = self._conn.execute(
            "SELECT * FROM files WHERE thumbnail_key=?", (key,)
        ).fetchone()
        return _deserialise(row) if row else None

    def by_keys(self, keys: list[str]) -> list[FileRecord]:
        if not keys:
            return []
        marks = ",".join("?" * len(keys))
        rows = self._conn.execute(
            f"SELECT * FROM files WHERE object_key IN ({marks}) OR thumbnail_key IN ({marks})",
            (*keys, *keys),
        ).fetchall()
        return [_deserialise(r) for r in rows]

    def find_by_user_checksum(
        self, user_id: str, checksum: str
    ) -> Optional[FileRecord]:
        row = self._conn.execute(
            "SELECT * FROM files WHERE user_id=? AND checksum=?",
            (user_id, checksum),
        ).fetchone()
        return _deserialise(row) if row else None
