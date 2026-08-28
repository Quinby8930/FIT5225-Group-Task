"""Pure query logic — the heart of Member D's deliverable.

These functions are deliberately free of any database or framework dependency:
they operate on plain `list[FileRecord]`, which makes them trivial to unit-test
and identical whether the records came from SQLite or DynamoDB.
"""

from __future__ import annotations

from app.schemas import FileRecord, QueryResultItem


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
        )
        for record in records
    ]
