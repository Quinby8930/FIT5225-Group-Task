"""Pydantic schemas — the contract between Member C (ML), Member B (storage)
and Member D (database). These freeze the shape of a stored file record and of
every query/response so other members can develop in parallel.

Field names follow Member B's API contract (``docs/member-b/api-contracts.md``):
storage locations are S3 **keys** (``object_key`` / ``thumbnail_key``), not URLs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, StrictStr, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


FileStatus = Literal[
    "pending_upload", "processing", "completed", "failed", "deleting"
]


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
    processing_lease_token: Optional[str] = None
    deletion_attempt_token: Optional[str] = None
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

    keys: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    tags: list[str]
    operation: Literal[0, 1]  # 1 = add, 0 = remove


class DeleteRequest(BaseModel):
    """Bulk delete files (matched by object key)."""

    keys: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class QueryResultItem(BaseModel):
    """Safe archive metadata used by the authenticated query client."""

    file_id: str
    file_type: Literal["image", "video"]
    display_key: str
    original_key: str
    thumbnail_key: Optional[str]
    can_preview: bool
    can_manage: bool
    tags: dict[str, int] = Field(default_factory=dict)
    detections: list[dict] = Field(default_factory=list)
    model_version: str = ""


class QueryResponse(BaseModel):
    """Query result: the list of display keys + count.

    For images we return the thumbnail key; for videos we return the original
    key, per the assignment specification.
    """

    results: list[str]
    count: int
    items: list[QueryResultItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Subscription & notification (Member D owns data, trigger, and SNS publisher;
# Member E owns the frontend/in-app UX on top of these endpoints)
# ---------------------------------------------------------------------------
class SubscribeRequest(BaseModel):
    """Subscribe (or unsubscribe) a user to a species tag.

    `species` is the team short name (see `app/species.py`), e.g. ``"wombat"``.
    When a newly processed file is completed with that species in its tags, the
    user receives a notification.
    """

    model_config = {"extra": "forbid"}

    species: str


class SubscriptionListResponse(BaseModel):
    """A user's current subscriptions."""

    species: list[str]
    count: int


class Notification(BaseModel):
    """One notification produced by the trigger when a completed file's tags
    match a subscription. Member D persists this inbox row and publishes it to
    SNS; Member E presents it through the frontend/in-app experience."""

    notification_id: str
    user_id: str
    file_id: str
    species: str
    object_key: str
    created_at: datetime = Field(default_factory=utcnow)


class NotificationListResponse(BaseModel):
    """A user's notifications, newest first."""

    notifications: list[Notification]
    count: int


# ---------------------------------------------------------------------------
# Internal metadata requests (Member B -> Member D, see api-contracts.md)
# ---------------------------------------------------------------------------
class AssetAuthorizationRequest(BaseModel):
    """Batch authorization request for canonical completed archive keys."""

    model_config = {"extra": "forbid"}
    keys: list[StrictStr] = Field(min_length=1, max_length=100)

    @field_validator("keys")
    @classmethod
    def keys_fit_s3_byte_limit(cls, keys: list[str]) -> list[str]:
        if any(len(key.encode("utf-8")) > 1024 for key in keys):
            raise ValueError("each key must be at most 1024 UTF-8 bytes")
        return keys

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
    lease_token: Optional[StrictStr] = Field(
        default=None, min_length=32, max_length=256
    )
    status: Literal["completed"] = "completed"


class FailedRequest(BaseModel):
    """Record a bounded processing failure (idempotent)."""

    user_id: str
    error_code: str
    message: str = ""
    lease_token: Optional[StrictStr] = Field(
        default=None, min_length=32, max_length=256
    )
    status: Literal["failed"] = "failed"
