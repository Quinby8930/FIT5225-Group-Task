"""DynamoDB implementation of :class:`FileRepository` — the cloud backend.

This mirrors `SQLiteRepository` method-for-method so the query API never needs
to know which backend is running. `boto3` is imported lazily so the module can
be imported (and every other test can run) without AWS dependencies installed.

Table design (documented in `infrastructure/dynamodb.yaml`):

    Primary key: file_id (String, partition key)

Attributes map 1:1 to :class:`FileRecord`:
    user_id (S), file_type (S), object_key (S), thumbnail_key (S),
    filename (S), content_type (S), size_bytes (N), tags (M: species -> N),
    detections (L), model_version (S), checksum (S), status (S),
    error_code (S), message (S), processing_sequencer (S),
    lease_expires_at (S, ISO-8601), upload_time (S, ISO-8601).

Tag and checksum lookups use `Scan` + `FilterExpression` and are finalised in
the service layer, identical to the local path. For production scale this would
use a GSI, but Scan is correct and simple for this assignment's data size.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from app.repository.base import DuplicateError, FileRepository
from app.schemas import FileRecord


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _scan_all(table, **kwargs) -> list[dict]:
    items: list[dict] = []
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs = {**kwargs, "ExclusiveStartKey": last_key}


def _query_all(table, **kwargs) -> list[dict]:
    items: list[dict] = []
    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs = {**kwargs, "ExclusiveStartKey": last_key}


def _float_to_decimal(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_float_to_decimal(item) for item in value]
    if isinstance(value, dict):
        return {key: _float_to_decimal(item) for key, item in value.items()}
    return value


def _decimal_to_float(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_decimal_to_float(item) for item in value]
    if isinstance(value, dict):
        return {key: _decimal_to_float(item) for key, item in value.items()}
    return value


class DynamoDBRepository(FileRepository):
    def __init__(self, table_name: str, region: str = "ap-southeast-2") -> None:
        import boto3  # lazy import: only needed when actually deployed to AWS

        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    @staticmethod
    def _to_item(record: FileRecord) -> dict:
        item: dict = {
            "file_id": record.file_id,
            "user_id": record.user_id,
            "file_type": record.file_type,
            "object_key": record.object_key,
            "thumbnail_key": record.thumbnail_key,
            "filename": record.filename,
            "content_type": record.content_type,
            "size_bytes": record.size_bytes,
            "tags": record.tags,
            "detections": record.detections,
            "model_version": record.model_version,
            "checksum": record.checksum,
            "status": record.status,
            "error_code": record.error_code,
            "message": record.message,
            "processing_sequencer": record.processing_sequencer,
            "lease_expires_at": (
                record.lease_expires_at.isoformat() if record.lease_expires_at else None
            ),
            "upload_time": record.upload_time.isoformat(),
        }
        return _float_to_decimal({k: v for k, v in item.items() if v is not None})

    @staticmethod
    def _from_item(item: dict) -> FileRecord:
        return FileRecord(
            file_id=item["file_id"],
            user_id=item["user_id"],
            file_type=item["file_type"],
            object_key=item["object_key"],
            thumbnail_key=item.get("thumbnail_key"),
            filename=item.get("filename") or "",
            content_type=item.get("content_type") or "",
            size_bytes=int(item.get("size_bytes") or 0),
            tags=item.get("tags") or {},
            detections=_decimal_to_float(item.get("detections") or []),
            model_version=item.get("model_version") or "",
            checksum=item["checksum"],
            status=item.get("status") or "completed",
            error_code=item.get("error_code"),
            message=item.get("message"),
            processing_sequencer=item.get("processing_sequencer"),
            lease_expires_at=_dt(item.get("lease_expires_at")),
            upload_time=_dt(item["upload_time"]),
        )

    def add(self, record: FileRecord) -> None:
        import botocore.exceptions

        try:
            self._table.put_item(
                Item=self._to_item(record),
                ConditionExpression="attribute_not_exists(file_id)",
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            existing = self.find_by_user_checksum(
                record.user_id, record.checksum
            ) or self.get(record.file_id)
            raise DuplicateError(existing.file_id if existing else None) from exc

    def all(self) -> list[FileRecord]:
        return [self._from_item(item) for item in _scan_all(self._table)]

    def get(self, file_id: str) -> Optional[FileRecord]:
        response = self._table.get_item(Key={"file_id": file_id})
        item = response.get("Item")
        return self._from_item(item) if item else None

    def by_thumbnail_key(self, key: str) -> Optional[FileRecord]:
        items = _scan_all(
            self._table,
            FilterExpression="thumbnail_key = :k",
            ExpressionAttributeValues={":k": key},
        )
        return self._from_item(items[0]) if items else None

    def by_keys(self, keys: list[str]) -> list[FileRecord]:
        # DynamoDB has no OR/IN across two attributes; filter over the scan.
        wanted = set(keys)
        return [
            record
            for record in self.all()
            if record.object_key in wanted or record.thumbnail_key in wanted
        ]

    def find_by_user_checksum(
        self, user_id: str, checksum: str
    ) -> Optional[FileRecord]:
        items = _scan_all(
            self._table,
            FilterExpression="user_id = :u AND checksum = :c",
            ExpressionAttributeValues={":u": user_id, ":c": checksum},
        )
        return self._from_item(items[0]) if items else None

    def update_tags(self, file_id: str, tags: dict[str, int]) -> None:
        self._table.update_item(
            Key={"file_id": file_id},
            UpdateExpression="SET tags = :t",
            ExpressionAttributeValues={":t": _float_to_decimal(tags)},
        )

    def mark_processing(
        self, file_id: str, sequencer: str, lease_expires_at: datetime
    ) -> None:
        self._table.update_item(
            Key={"file_id": file_id},
            UpdateExpression=(
                "SET #s = :s, processing_sequencer = :q, lease_expires_at = :l "
                "REMOVE error_code, message"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "processing",
                ":q": sequencer,
                ":l": lease_expires_at.isoformat(),
            },
        )

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
        values: dict = {
            ":s": "completed",
            ":o": original_key,
            ":ft": file_type,
            ":t": tags,
            ":d": _float_to_decimal(detections),
            ":m": model_version,
        }
        set_expr = (
            "SET #s = :s, object_key = :o, file_type = :ft, tags = :t, "
            "detections = :d, model_version = :m "
        )
        if thumbnail_key is not None:
            set_expr += ", thumbnail_key = :th"
            values[":th"] = thumbnail_key
        self._table.update_item(
            Key={"file_id": file_id},
            UpdateExpression=(
                set_expr
                + "REMOVE processing_sequencer, lease_expires_at, error_code, message"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues=values,
        )

    def mark_failed(self, file_id: str, error_code: str, message: str) -> None:
        self._table.update_item(
            Key={"file_id": file_id},
            UpdateExpression=(
                "SET #s = :s, error_code = :e, message = :m "
                "REMOVE processing_sequencer, lease_expires_at"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "failed",
                ":e": error_code,
                ":m": message,
            },
        )

    def delete_by_ids(self, file_ids: list[str]) -> int:
        removed = 0
        for fid in file_ids:
            self._table.delete_item(Key={"file_id": fid})
            removed += 1
        return removed
