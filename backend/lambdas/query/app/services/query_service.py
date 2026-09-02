"""Pure query logic — the heart of Member D's deliverable.

These functions are deliberately free of any database or framework dependency:
they operate on plain `list[FileRecord]`, which makes them trivial to unit-test
and identical whether the records came from SQLite or DynamoDB.
"""

from __future__ import annotations

from decimal import Decimal
import math

from app.schemas import FileRecord, QueryResultItem, UploadStatusResponse


MAX_PUBLIC_TAGS = 64
MAX_PUBLIC_DETECTIONS = 1000
MAX_PUBLIC_LABEL_BYTES = 128
MAX_PUBLIC_MODEL_VERSION_BYTES = 128
MAX_PUBLIC_FILENAME_BYTES = 255
MAX_PUBLIC_ERROR_CODE_BYTES = 128
MAX_PUBLIC_FAILURE_MESSAGE_BYTES = 240


def _fits_utf8(value: str, maximum_bytes: int) -> bool:
    try:
        return len(value.encode("utf-8")) <= maximum_bytes
    except UnicodeEncodeError:
        return False


def _safe_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    label = value.strip()
    if (
        not label
        or not _fits_utf8(label, MAX_PUBLIC_LABEL_BYTES)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in label)
    ):
        return None
    return label


def _safe_tags(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or len(value) > MAX_PUBLIC_TAGS:
        return {}
    tags: dict[str, int] = {}
    for raw_species, raw_count in value.items():
        species = _safe_label(raw_species)
        if species is None or species in tags or isinstance(raw_count, bool):
            continue
        if isinstance(raw_count, Decimal):
            if not raw_count.is_finite() or raw_count != raw_count.to_integral_value():
                continue
            count = int(raw_count)
        elif type(raw_count) is int:
            count = raw_count
        else:
            continue
        if count > 0:
            tags[species] = count
    return dict(sorted(tags.items()))


def _safe_detections(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > MAX_PUBLIC_DETECTIONS:
        return []
    detections: list[dict] = []
    for detection in value:
        if not isinstance(detection, dict):
            continue
        species = _safe_label(detection.get("species"))
        confidence = detection.get("confidence")
        if (
            species is None
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float, Decimal))
        ):
            continue
        score = float(confidence)
        if not math.isfinite(score) or score < 0 or score > 1:
            continue
        detections.append({"species": species, "confidence": score})
    return detections


def _safe_model_version(value: object) -> str:
    if not isinstance(value, str):
        return ""
    version = value.strip()
    if (
        not version
        or not _fits_utf8(version, MAX_PUBLIC_MODEL_VERSION_BYTES)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in version)
    ):
        return ""
    return version


def _safe_text(value: object, maximum_bytes: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if (
        not text
        or not _fits_utf8(text, maximum_bytes)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in text)
    ):
        return ""
    return text


def filter_by_min_counts(
    records: list[FileRecord], min_counts: dict[str, int]
) -> list[FileRecord]:
    """Return records satisfying *every* tag with its minimum count (logical AND).

    ``{"wombat": 2, "magpie": 1}`` keeps a record only if it has >=2 wombats AND
    >=1 magpie. This is the assignment's core requirement — the naive mistake is
    using OR here.
    """
    if not min_counts:
        return list(records)
    return [
        r
        for r in records
        if all(r.tags.get(species, 0) >= count for species, count in min_counts.items())
    ]


def filter_by_species(records: list[FileRecord], species: str) -> list[FileRecord]:
    """Return records with at least one individual of `species`."""
    return [r for r in records if r.tags.get(species, 0) >= 1]


def to_display_keys(records: list[FileRecord]) -> list[str]:
    """Map records to the S3 keys the client should show.

    Images -> thumbnail key (save bandwidth); videos -> original key (no
    thumbnail is generated for video). Falls back to the object key if a
    thumbnail is unexpectedly missing.
    """
    keys: list[str] = []
    for r in records:
        if r.file_type == "image":
            keys.append(r.thumbnail_key or r.object_key)
        else:
            keys.append(r.object_key)
    return keys


def completed_records(records: list[FileRecord]) -> list[FileRecord]:
    """Return only records whose media processing has completed."""
    return [record for record in records if record.status == "completed"]


def to_query_items(
    records: list[FileRecord], authenticated_user: str
) -> list[QueryResultItem]:
    """Project records into the public query contract without exposing owners."""
    return [
        QueryResultItem(
            file_id=record.file_id,
            file_type=record.file_type,
            display_key=(
                record.thumbnail_key or record.object_key
                if record.file_type == "image"
                else record.object_key
            ),
            original_key=record.object_key,
            thumbnail_key=record.thumbnail_key,
            can_preview=True,
            can_manage=record.user_id == authenticated_user,
            tags=_safe_tags(record.tags),
            detections=_safe_detections(record.detections),
            model_version=_safe_model_version(record.model_version),
        )
        for record in records
    ]


def to_upload_status(record: FileRecord) -> UploadStatusResponse:
    """Project one owner-authorized record into the upload progress contract."""
    completed = record.status == "completed"
    failed = record.status == "failed"
    return UploadStatusResponse(
        file_id=record.file_id,
        filename=_safe_text(record.filename, MAX_PUBLIC_FILENAME_BYTES),
        file_type=record.file_type,
        status=record.status,
        tags=_safe_tags(record.tags) if completed else {},
        detections=_safe_detections(record.detections) if completed else [],
        model_version=_safe_model_version(record.model_version) if completed else "",
        error_code=(
            _safe_text(record.error_code, MAX_PUBLIC_ERROR_CODE_BYTES) or None
            if failed
            else None
        ),
        message=(
            _safe_text(record.message, MAX_PUBLIC_FAILURE_MESSAGE_BYTES) or None
            if failed
            else None
        ),
        upload_time=record.upload_time,
    )
