"""Abstract repository interface.

This is the contract every backend must honour. The query API only talks to
this interface, so swapping SQLite (local) for DynamoDB (cloud) is a drop-in
change driven by `config.settings.repository_backend`.

Storage locations are S3 **keys** (``object_key`` / ``thumbnail_key``), not
URLs, matching Member B's contract in `docs/member-b/api-contracts.md`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from app.schemas import FileRecord


class DuplicateError(Exception):
    """Raised when `add` collides with an existing `file_id` or `(user_id, checksum)`."""

    def __init__(self, existing_file_id: str | None = None) -> None:
        super().__init__("duplicate file")
        self.existing_file_id = existing_file_id


class RepositoryIntegrityError(RuntimeError):
    """Raised when persisted uniqueness metadata contradicts file records."""


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
    def begin_delete(self, file_id: str, user_id: str) -> bool:
        """Atomically mark an owned completed/deleting record as deleting."""

    @abstractmethod
    def restore_completed(self, file_id: str, user_id: str) -> bool:
        """Restore an owned deleting record after storage deletion fails."""

    @abstractmethod
    def delete_by_ids(self, file_ids: list[str]) -> int:
        """Delete records by id. Returns the number of rows removed."""
