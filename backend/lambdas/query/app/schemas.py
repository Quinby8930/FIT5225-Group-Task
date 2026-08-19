"""Pydantic schemas — the contract between Member C (ML), Member B (storage)
and Member D (database). These freeze the shape of a stored file record and of
every query/response so other members can develop in parallel.

Field names follow Member B's API contract (``docs/member-b/api-contracts.md``):
storage locations are S3 **keys** (``object_key`` / ``thumbnail_key``), not URLs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


FileStatus = Literal["pending_upload", "processing", "completed", "failed"]


class FileRecord(BaseModel):
    """One media file stored in the database.

    `tags` maps a species *short name* to the number of that species detected,
    e.g. ``{"dingo": 2, "wombat": 1}``. Member C's SpeciesNet outputs scientific
    names (``Canis_familiaris``); those are converted to the team short name via
    ``app/species.py`` before this record is written.
    """

    model_config = {"protected_namespaces": ()}

    file_id: str
    user_id: str
    file_type: Literal["image", "video"]
    object_key: str
    thumbnail_key: Optional[str] = None  # images only
    filename: str = ""
    content_type: str = ""
    size_bytes: int = 0
    tags: dict[str, int] = Field(default_factory=dict)
    detections: list[dict] = Field(default_factory=list)
    model_version: str = ""
    checksum: str
    status: FileStatus = "completed"
    error_code: Optional[str] = None
    message: Optional[str] = None
    processing_sequencer: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    upload_time: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Public query/edit requests (Member E -> Member D)
# ---------------------------------------------------------------------------
class TagQueryRequest(BaseModel):
    """Find files by tags with minimum counts (logical AND)."""

    tags: dict[str, int]


class SpeciesQueryRequest(BaseModel):
    """Find files containing at least one individual of a species."""

    species: str


class TagEditRequest(BaseModel):
    """Bulk add/remove tags on a list of files (matched by object key)."""

    keys: list[str]
    tags: list[str]
    operation: Literal[0, 1]  # 1 = add, 0 = remove


class DeleteRequest(BaseModel):
    """Bulk delete files (matched by object key)."""

    keys: list[str]


class QueryResponse(BaseModel):
    """Query result: the list of display keys + count.

    For images we return the thumbnail key; for videos we return the original
    key, per the assignment specification.
    """

    results: list[str]
    count: int


# ---------------------------------------------------------------------------
# Internal metadata requests (Member B -> Member D, see api-contracts.md)
# ---------------------------------------------------------------------------
class ReserveRequest(BaseModel):
    """Reserve a unique (user_id, checksum) upload before S3 pre-signing."""

    file_id: str
    user_id: str
    checksum: str
    filename: str
    file_type: Literal["image", "video"]
    content_type: str
    size_bytes: int
    object_key: str
    status: Literal["pending_upload"] = "pending_upload"


class ProcessingRequest(BaseModel):
    """Acquire the processing lease for one file."""

    user_id: str
    object_key: str
    sequencer: str


class CompleteRequest(BaseModel):
    """Record a completed processing run (idempotent)."""

    model_config = {"protected_namespaces": ()}

    user_id: str
    file_type: Literal["image", "video"]
    original_key: str
    thumbnail_key: Optional[str] = None  # null for video
    tags: dict[str, int] = Field(default_factory=dict)
    detections: list[dict] = Field(default_factory=list)
    model_version: str = ""
    status: Literal["completed"] = "completed"


class FailedRequest(BaseModel):
    """Record a bounded processing failure (idempotent)."""

    user_id: str
    error_code: str
    message: str = ""
    status: Literal["failed"] = "failed"
