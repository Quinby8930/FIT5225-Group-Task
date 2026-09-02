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

from app.repository.base import (
    CompletedChecksumMatch,
    DuplicateError,
    FileRepository,
    RepositoryIntegrityError,
    completed_checksum_match,
)
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
    processing_lease_token TEXT,
    deletion_attempt_token TEXT,
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
    "processing_sequencer", "processing_lease_token", "deletion_attempt_token",
    "lease_expires_at", "upload_time",
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
        record.processing_lease_token,
        record.deletion_attempt_token,
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
        processing_lease_token=row["processing_lease_token"],
        deletion_attempt_token=row["deletion_attempt_token"],
        lease_expires_at=_dt(row["lease_expires_at"]),
        upload_time=_dt(row["upload_time"]),
    )


class SQLiteRepository(FileRepository):
    def __init__(self, db_path: str = "data/pacific_bioarchive.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(files)")
        }
        if "processing_lease_token" not in columns:
            self._conn.execute(
                "ALTER TABLE files ADD COLUMN processing_lease_token TEXT"
            )
        if "deletion_attempt_token" not in columns:
            self._conn.execute(
                "ALTER TABLE files ADD COLUMN deletion_attempt_token TEXT"
            )
        self._conn.commit()

    # -- writes -----------------------------------------------------------
    def reserve(self, record: FileRecord) -> tuple[FileRecord, bool]:
        try:
            self.add(record)
            return record, True
        except DuplicateError:
            existing = self.find_by_user_checksum(
                record.user_id, record.checksum
            ) or self.get(record.file_id)
            if existing is None:
                raise
            return existing, False

    def reuse_upload(self, file_id: str) -> Optional[FileRecord]:
        cursor = self._conn.execute(
            "UPDATE files SET status='pending_upload', error_code=NULL, message=NULL, "
            "processing_sequencer=NULL, processing_lease_token=NULL, "
            "lease_expires_at=NULL "
            "WHERE file_id=? AND status IN ('pending_upload', 'failed')",
            (file_id,),
        )
        self._conn.commit()
        return self.get(file_id) if cursor.rowcount == 1 else None

    def add(self, record: FileRecord) -> None:
        try:
            self._conn.execute(
                f"INSERT INTO files ({','.join(_COLUMNS)}) "
                f"VALUES ({','.join('?' * len(_COLUMNS))})",
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

    def try_acquire_processing(
        self,
        file_id: str,
        sequencer: str,
        now: datetime,
        lease_expires_at: datetime,
        lease_token: str,
    ) -> str:
        cursor = self._conn.execute(
            "UPDATE files SET status='processing', processing_sequencer=?, "
            "processing_lease_token=?, lease_expires_at=?, error_code=NULL, message=NULL "
            "WHERE file_id=? AND status NOT IN ('completed', 'deleting') AND "
            "(status <> 'processing' OR lease_expires_at IS NULL OR lease_expires_at <= ?)",
            (
                sequencer,
                lease_token,
                lease_expires_at.isoformat(),
                file_id,
                now.isoformat(),
            ),
        )
        self._conn.commit()
        if cursor.rowcount == 1:
            return "acquired"
        record = self.get(file_id)
        return "completed" if record and record.status == "completed" else "lease_active"

    def mark_completed(
        self,
        file_id: str,
        original_key: str,
        thumbnail_key: Optional[str],
        file_type: str,
        tags: dict[str, int],
        detections: list[dict],
        model_version: str,
        lease_token: Optional[str] = None,
    ) -> bool:
        condition = "status='processing'"
        params: list = [
            original_key,
            thumbnail_key,
            file_type,
            json.dumps(tags, sort_keys=True),
            json.dumps(detections),
            model_version,
            file_id,
        ]
        if lease_token is not None:
            condition += " AND processing_lease_token=?"
            params.append(lease_token)
        cursor = self._conn.execute(
            "UPDATE files SET status='completed', object_key=?, thumbnail_key=?, "
            "file_type=?, tags_json=?, detections_json=?, model_version=?, "
            "processing_sequencer=NULL, processing_lease_token=NULL, "
            "lease_expires_at=NULL, error_code=NULL, message=NULL "
            f"WHERE file_id=? AND {condition}",
            params,
        )
        self._conn.commit()
        return cursor.rowcount == 1 or self.get(file_id) is not None and self.get(file_id).status == "completed"

    def mark_failed(
        self,
        file_id: str,
        error_code: str,
        message: str,
        lease_token: Optional[str] = None,
    ) -> bool:
        condition = "status='processing'"
        params: list = [error_code, message, file_id]
        if lease_token is not None:
            condition += " AND processing_lease_token=?"
            params.append(lease_token)
        cursor = self._conn.execute(
            "UPDATE files SET status='failed', error_code=?, message=?, "
            "processing_sequencer=NULL, processing_lease_token=NULL, "
            "lease_expires_at=NULL "
            f"WHERE file_id=? AND {condition}",
            params,
        )
        self._conn.commit()
        record = self.get(file_id)
        return cursor.rowcount == 1 or record is not None and record.status == "completed"

    def begin_delete(
        self, file_id: str, user_id: str, deletion_attempt_token: str
    ) -> bool:
        cursor = self._conn.execute(
            "UPDATE files SET status='deleting', deletion_attempt_token=? "
            "WHERE file_id=? AND user_id=? AND status IN ('completed', 'deleting')",
            (deletion_attempt_token, file_id, user_id),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def delete_by_ids(
        self,
        file_ids: list[str],
        *,
        user_id: Optional[str] = None,
        deletion_attempt_tokens: Optional[dict[str, str]] = None,
    ) -> int:
        if not file_ids:
            return 0
        if user_id is None or deletion_attempt_tokens is None:
            raise ValueError("deletion attempt fence is required")
        if any(file_id not in deletion_attempt_tokens for file_id in file_ids):
            raise ValueError("deletion attempt fence is required for every file")
        removed = 0
        for file_id in file_ids:
            cursor = self._conn.execute(
                "DELETE FROM files WHERE file_id=? AND user_id=? "
                "AND status='deleting' AND deletion_attempt_token=?",
                (file_id, user_id, deletion_attempt_tokens[file_id]),
            )
            removed += cursor.rowcount
        self._conn.commit()
        return removed

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

    def find_completed_by_checksum(
        self, checksum: str, *, user_id: Optional[str] = None
    ) -> Optional[CompletedChecksumMatch]:
        query = "SELECT file_id,tags_json,upload_time FROM files "
        query += "WHERE checksum=? AND status='completed'"
        params: tuple[str, ...] = (checksum,)
        if user_id is not None:
            query += " AND user_id=?"
            params = (checksum, user_id)
        rows = self._conn.execute(query, params).fetchall()
        matches: list[CompletedChecksumMatch] = []
        for row in rows:
            try:
                raw_tags = json.loads(row["tags_json"])
                upload_time = _dt(row["upload_time"])
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise RepositoryIntegrityError(
                    "completed duplicate metadata is invalid"
                ) from exc
            matches.append(
                completed_checksum_match(
                    row["file_id"], raw_tags, upload_time
                )
            )
        return min(
            matches,
            key=lambda match: (match.upload_time, match.file_id),
            default=None,
        )
