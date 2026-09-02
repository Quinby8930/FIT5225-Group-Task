"""Abstract repository interface.

This is the contract every backend must honour. The query API only talks to
this interface, so swapping SQLite (local) for DynamoDB (cloud) is a drop-in
change driven by `config.settings.repository_backend`.

Storage locations are S3 **keys** (``object_key`` / ``thumbnail_key``), not
URLs, matching Member B's contract in `docs/member-b/api-contracts.md`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from app.schemas import FileRecord


class DuplicateError(Exception):
    """Raised when `add` collides with an existing `file_id` or `(user_id, checksum)`."""

    def __init__(self, existing_file_id: str | None = None) -> None:
        super().__init__("duplicate file")
        self.existing_file_id = existing_file_id


class RepositoryIntegrityError(RuntimeError):
    """Raised when persisted uniqueness metadata contradicts file records."""


MAX_DUPLICATE_TAGS = 64
MAX_DUPLICATE_TAG_NAME_BYTES = 128
MAX_DUPLICATE_TAG_COUNT = 1_000_000


@dataclass(frozen=True)
class CompletedChecksumMatch:
    """Public-safe details for one stable completed checksum match."""

    file_id: str
    tags: dict[str, int]
    upload_time: datetime


def completed_checksum_match(
    file_id: object, tags: object, upload_time: object
) -> CompletedChecksumMatch:
    """Validate untrusted persisted duplicate details before returning them."""

    if (
        not isinstance(file_id, str)
        or file_id != file_id.strip()
        or not file_id
        or len(file_id) > 256
        or any(ord(character) < 0x20 for character in file_id)
    ):
        raise RepositoryIntegrityError("completed duplicate file id is invalid")
    if not isinstance(upload_time, datetime) or upload_time.utcoffset() is None:
        raise RepositoryIntegrityError("completed duplicate upload time is invalid")
    if not isinstance(tags, dict) or len(tags) > MAX_DUPLICATE_TAGS:
        raise RepositoryIntegrityError("completed duplicate tags are invalid")

    safe_tags: dict[str, int] = {}
    for raw_name, raw_count in tags.items():
        if not isinstance(raw_name, str):
            raise RepositoryIntegrityError("completed duplicate tag name is invalid")
        name = raw_name.strip()
        if (
            not name
            or len(name.encode("utf-8")) > MAX_DUPLICATE_TAG_NAME_BYTES
            or name in safe_tags
            or any(ord(character) < 0x20 for character in name)
        ):
            raise RepositoryIntegrityError("completed duplicate tag name is invalid")
        if isinstance(raw_count, bool):
            raise RepositoryIntegrityError("completed duplicate tag count is invalid")
        if isinstance(raw_count, Decimal):
            if not raw_count.is_finite() or raw_count != raw_count.to_integral_value():
                raise RepositoryIntegrityError("completed duplicate tag count is invalid")
            count = int(raw_count)
        elif type(raw_count) is int:
            count = raw_count
        else:
            raise RepositoryIntegrityError("completed duplicate tag count is invalid")
        if count <= 0 or count > MAX_DUPLICATE_TAG_COUNT:
            raise RepositoryIntegrityError("completed duplicate tag count is invalid")
        safe_tags[name] = count

    return CompletedChecksumMatch(
        file_id=file_id,
        tags=dict(sorted(safe_tags.items())),
        upload_time=upload_time,
    )


class FileRepository(ABC):
    @abstractmethod
    def reserve(self, record: FileRecord) -> tuple[FileRecord, bool]:
        """Atomically claim checksum and file id; return ``(record, created)``."""

    @abstractmethod
    def reuse_upload(self, file_id: str) -> Optional[FileRecord]:
        """Reset pending/failed upload state, or return ``None`` if no longer reusable."""

    @abstractmethod
    def add(self, record: FileRecord) -> None:
        """Insert a new file record. Raises :class:`DuplicateError` on collision."""

    @abstractmethod
    def all(self) -> list[FileRecord]:
        """Return every record. Mirrors a DynamoDB `Scan` — cheap for this scale."""

    @abstractmethod
    def get(self, file_id: str) -> Optional[FileRecord]:
        """Return one record by primary key, or ``None``."""

    @abstractmethod
    def by_thumbnail_key(self, key: str) -> Optional[FileRecord]:
        """Find the single record whose thumbnail key matches."""

    @abstractmethod
    def by_keys(self, keys: list[str]) -> list[FileRecord]:
        """Find records whose object or thumbnail key is in `keys`."""

    @abstractmethod
    def find_by_user_checksum(
        self, user_id: str, checksum: str
    ) -> Optional[FileRecord]:
        """Return the record already reserved for this ``(user_id, checksum)`` pair."""

    @abstractmethod
    def find_completed_by_checksum(
        self, checksum: str, *, user_id: Optional[str] = None
    ) -> Optional[CompletedChecksumMatch]:
        """Return the earliest safe completed match, with ``file_id`` tie-break."""

    @abstractmethod
    def update_tags(self, file_id: str, tags: dict[str, int]) -> None:
        """Replace the tag map of one record (used by bulk add/remove)."""

    @abstractmethod
    def mark_processing(
        self, file_id: str, sequencer: str, lease_expires_at: datetime
    ) -> None:
        """Set status to ``processing`` and record the lease/sequencer."""

    @abstractmethod
    def try_acquire_processing(
        self,
        file_id: str,
        sequencer: str,
        now: datetime,
        lease_expires_at: datetime,
        lease_token: str,
    ) -> str:
        """Atomically return ``acquired``, ``completed``, or ``lease_active``."""

    @abstractmethod
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
        """Set status to ``completed`` and store the processed result (idempotent)."""

    @abstractmethod
    def mark_failed(
        self,
        file_id: str,
        error_code: str,
        message: str,
        lease_token: Optional[str] = None,
    ) -> bool:
        """Set status to ``failed`` with a bounded diagnostic (idempotent)."""

    @abstractmethod
    def begin_delete(
        self, file_id: str, user_id: str, deletion_attempt_token: str
    ) -> bool:
        """Install a fresh fencing token on an owned completed/deleting record."""

    @abstractmethod
    def delete_by_ids(
        self,
        file_ids: list[str],
        *,
        user_id: Optional[str] = None,
        deletion_attempt_tokens: Optional[dict[str, str]] = None,
    ) -> int:
        """Delete records by id. Returns the number of rows removed."""
