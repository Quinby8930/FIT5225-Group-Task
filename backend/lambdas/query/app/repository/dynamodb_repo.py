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
import hashlib
from typing import Optional

from app.repository.base import DuplicateError, FileRepository, RepositoryIntegrityError
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
    def __init__(
        self,
        table_name: str,
        region: str = "ap-southeast-2",
        reservations_table: str = "PacificBioArchiveUploadReservations",
    ) -> None:
        import boto3  # lazy import: only needed when actually deployed to AWS

        resource = boto3.resource("dynamodb", region_name=region)
        self._table = resource.Table(table_name)
        self._reservations = resource.Table(reservations_table)
        self._reservations_table_name = reservations_table
        self._client = resource.meta.client

    @staticmethod
    def _reservation_key(user_id: str, checksum: str) -> str:
        value = f"{user_id}\0{checksum}".encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _serialize_item(item: dict) -> dict:
        def value(item_value):
            if item_value is None:
                return {"NULL": True}
            if isinstance(item_value, bool):
                return {"BOOL": item_value}
            if isinstance(item_value, str):
                return {"S": item_value}
            if isinstance(item_value, (int, Decimal)):
                return {"N": str(item_value)}
            if isinstance(item_value, list):
                return {"L": [value(child) for child in item_value]}
            if isinstance(item_value, dict):
                return {
                    "M": {key: value(child) for key, child in item_value.items()}
                }
            raise TypeError(f"unsupported DynamoDB value: {type(item_value).__name__}")

        return {key: value(item_value) for key, item_value in item.items()}

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

    def reserve(self, record: FileRecord) -> tuple[FileRecord, bool]:
        import botocore.exceptions

        existing = self.find_by_user_checksum(record.user_id, record.checksum)
        if existing is not None:
            return existing, False
        reservation = {
            "reservation_key": self._reservation_key(record.user_id, record.checksum),
            "file_id": record.file_id,
            "user_id": record.user_id,
            "checksum": record.checksum,
        }
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._reservations_table_name,
                            "Item": self._serialize_item(reservation),
                            "ConditionExpression": "attribute_not_exists(reservation_key)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table.name,
                            "Item": self._serialize_item(self._to_item(record)),
                            "ConditionExpression": "attribute_not_exists(file_id)",
                        }
                    },
                ]
            )
            return record, True
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise
            existing = self.find_by_user_checksum(record.user_id, record.checksum)
            if existing is not None:
                return existing, False
            file_collision = self.get(record.file_id)
            if file_collision is not None:
                raise DuplicateError(file_collision.file_id) from exc
            raise

    def reuse_upload(self, file_id: str) -> Optional[FileRecord]:
        import botocore.exceptions

        try:
            response = self._table.update_item(
                Key={"file_id": file_id},
                UpdateExpression=(
                    "SET #s = :pending REMOVE error_code, message, "
                    "processing_sequencer, lease_expires_at"
                ),
                ConditionExpression="#s IN (:pending, :failed)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":pending": "pending_upload",
                    ":failed": "failed",
                },
                ReturnValues="ALL_NEW",
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return None
            raise
        attributes = response.get("Attributes")
        return self._from_item(attributes) if attributes else None

    def all(self) -> list[FileRecord]:
        return [self._from_item(item) for item in _scan_all(self._table)]

    def get(self, file_id: str) -> Optional[FileRecord]:
        response = self._table.get_item(Key={"file_id": file_id}, ConsistentRead=True)
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
        reservation_key = self._reservation_key(user_id, checksum)
        if hasattr(self, "_reservations"):
            response = self._reservations.get_item(
                Key={"reservation_key": reservation_key},
                ConsistentRead=True,
            )
            reservation = response.get("Item")
            if reservation:
                return self._record_for_claim(reservation, user_id, checksum)
        items = _scan_all(
            self._table,
            FilterExpression="user_id = :u AND checksum = :c",
            ExpressionAttributeValues={":u": user_id, ":c": checksum},
            ConsistentRead=True,
        )
        if len(items) > 1:
            raise RepositoryIntegrityError(
                "multiple legacy files share one user/checksum; migration required"
            )
        if not items:
            return None
        legacy = self._from_item(items[0])
        if not hasattr(self, "_reservations"):
            return legacy
        claim = {
            "reservation_key": reservation_key,
            "file_id": legacy.file_id,
            "user_id": user_id,
            "checksum": checksum,
        }
        import botocore.exceptions

        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "ConditionCheck": {
                            "TableName": self._table.name,
                            "Key": self._serialize_item({"file_id": legacy.file_id}),
                            "ConditionExpression": (
                                "attribute_exists(file_id) AND user_id = :user_id "
                                "AND checksum = :checksum"
                            ),
                            "ExpressionAttributeValues": self._serialize_item(
                                {":user_id": user_id, ":checksum": checksum}
                            ),
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._reservations_table_name,
                            "Item": self._serialize_item(claim),
                            "ConditionExpression": (
                                "attribute_not_exists(reservation_key)"
                            ),
                        }
                    },
                ]
            )
            return legacy
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise
            winner = self._reservations.get_item(
                Key={"reservation_key": reservation_key}, ConsistentRead=True
            ).get("Item")
            if winner is not None:
                return self._record_for_claim(winner, user_id, checksum)
            current = self._table.get_item(
                Key={"file_id": legacy.file_id}, ConsistentRead=True
            ).get("Item")
            if (
                current is None
                or current.get("user_id") != user_id
                or current.get("checksum") != checksum
            ):
                return None
            raise

    def _record_for_claim(
        self, claim: dict, expected_user_id: str, expected_checksum: str
    ) -> FileRecord:
        if (
            claim.get("user_id") != expected_user_id
            or claim.get("checksum") != expected_checksum
            or not isinstance(claim.get("file_id"), str)
        ):
            raise RepositoryIntegrityError("reservation claim metadata is inconsistent")
        file_response = self._table.get_item(
            Key={"file_id": claim["file_id"]}, ConsistentRead=True
        )
        item = file_response.get("Item")
        if item is None:
            raise RepositoryIntegrityError("reservation claim points to a missing file")
        record = self._from_item(item)
        if record.user_id != expected_user_id or record.checksum != expected_checksum:
            raise RepositoryIntegrityError("reservation claim points to a different file")
        return record

    def update_tags(self, file_id: str, tags: dict[str, int]) -> None:
        self._table.update_item(
            Key={"file_id": file_id},
            UpdateExpression="SET #tags = :t",
            ConditionExpression="attribute_exists(file_id)",
            ExpressionAttributeNames={"#tags": "tags"},
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

    def try_acquire_processing(
        self,
        file_id: str,
        sequencer: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> str:
        import botocore.exceptions

        try:
            self._table.update_item(
                Key={"file_id": file_id},
                UpdateExpression=(
                    "SET #s = :processing, processing_sequencer = :q, "
                    "lease_expires_at = :lease REMOVE error_code, message"
                ),
                ConditionExpression=(
                    "attribute_exists(file_id) AND #s <> :completed AND "
                    "(#s <> :processing OR attribute_not_exists(lease_expires_at) OR "
                    "lease_expires_at <= :now)"
                ),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":processing": "processing",
                    ":completed": "completed",
                    ":q": sequencer,
                    ":lease": lease_expires_at.isoformat(),
                    ":now": now.isoformat(),
                },
                ReturnValues="ALL_NEW",
            )
            return "acquired"
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
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
    ) -> None:
        values: dict = {
            ":s": "completed",
            ":o": original_key,
            ":ft": file_type,
            ":t": tags,
            ":d": _float_to_decimal(detections),
            ":m": model_version,
            ":completed": "completed",
        }
        set_parts = [
            "#s = :s",
            "object_key = :o",
            "file_type = :ft",
            "#tags = :t",
            "detections = :d",
            "model_version = :m",
        ]
        if thumbnail_key is not None:
            set_parts.append("thumbnail_key = :th")
            values[":th"] = thumbnail_key
        import botocore.exceptions

        try:
            self._table.update_item(
                Key={"file_id": file_id},
                UpdateExpression=(
                    f"SET {', '.join(set_parts)} "
                    "REMOVE processing_sequencer, lease_expires_at, error_code, message"
                ),
                ConditionExpression=(
                    "attribute_exists(file_id) AND #s <> :completed"
                ),
                ExpressionAttributeNames={"#s": "status", "#tags": "tags"},
                ExpressionAttributeValues=values,
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            current = self.get(file_id)
            if current is not None and current.status == "completed":
                return
            raise

    def mark_failed(self, file_id: str, error_code: str, message: str) -> None:
        import botocore.exceptions

        try:
            self._table.update_item(
                Key={"file_id": file_id},
                UpdateExpression=(
                    "SET #s = :s, error_code = :e, message = :m "
                    "REMOVE processing_sequencer, lease_expires_at"
                ),
                ConditionExpression=(
                    "attribute_exists(file_id) AND #s <> :completed"
                ),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": "failed",
                    ":e": error_code,
                    ":m": message,
                    ":completed": "completed",
                },
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            current = self.get(file_id)
            if current is None or current.status == "completed":
                return
            raise

    def delete_by_ids(self, file_ids: list[str]) -> int:
        import botocore.exceptions

        removed = 0
        for fid in file_ids:
            record = self.get(fid)
            if record is None:
                continue
            try:
                self._client.transact_write_items(
                    TransactItems=[
                        {
                            "Delete": {
                                "TableName": self._table.name,
                                "Key": self._serialize_item({"file_id": fid}),
                                "ConditionExpression": (
                                    "attribute_not_exists(file_id) OR "
                                    "(user_id = :user_id AND checksum = :checksum)"
                                ),
                                "ExpressionAttributeValues": self._serialize_item(
                                    {
                                        ":user_id": record.user_id,
                                        ":checksum": record.checksum,
                                    }
                                ),
                            }
                        },
                        {
                            "Delete": {
                                "TableName": self._reservations_table_name,
                                "Key": self._serialize_item(
                                    {
                                        "reservation_key": self._reservation_key(
                                            record.user_id, record.checksum
                                        )
                                    }
                                ),
                                "ConditionExpression": (
                                    "attribute_not_exists(reservation_key) OR "
                                    "file_id = :file_id"
                                ),
                                "ExpressionAttributeValues": self._serialize_item(
                                    {":file_id": fid}
                                ),
                            }
                        },
                    ]
                )
            except botocore.exceptions.ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                    raise
                if self.get(fid) is not None:
                    raise
            removed += 1
        return removed
