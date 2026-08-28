"""Behavioral tests for Member D's DynamoDB serialization boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

from app.repository.dynamodb_repo import DynamoDBRepository
from app.repository.notification_repo import DynamoDBNotificationRepository
from app.schemas import FileRecord, Notification


def _item(file_id: str, object_key: str, **overrides) -> dict:
    item = {
        "file_id": file_id,
        "user_id": "u1",
        "file_type": "image",
        "object_key": object_key,
        "checksum": f"sha256:{file_id}",
        "upload_time": "2026-08-26T00:00:00+00:00",
        "tags": {"wombat": Decimal("2")},
        "detections": [],
    }
    item.update(overrides)
    return item


class _PagedTable:
    def __init__(self, *, scan_pages=None, query_pages=None):
        self.scan_pages = scan_pages or []
        self.query_pages = query_pages or []
        self.updated = None
        self.scan_calls = []
        self.get_calls = []
        self.put_calls = []

    @staticmethod
    def _page(pages, kwargs):
        index = 0 if "ExclusiveStartKey" not in kwargs else kwargs["ExclusiveStartKey"]["page"]
        return pages[index]

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        return self._page(self.scan_pages, kwargs)

    def query(self, **kwargs):
        return self._page(self.query_pages, kwargs)

    def update_item(self, **kwargs):
        self.updated = kwargs

    def get_item(self, **kwargs):
        self.get_calls.append(kwargs)
        return {}

    def put_item(self, **kwargs):
        self.put_calls.append(kwargs)


class _TransactionClient:
    def __init__(self):
        self.calls = []

    def transact_write_items(self, **kwargs):
        self.calls.append(kwargs)


class _CanceledTransactionClient(_TransactionClient):
    def transact_write_items(self, **kwargs):
        self.calls.append(kwargs)
        raise ClientError(
            {"Error": {"Code": "TransactionCanceledException"}},
            "TransactWriteItems",
        )


def _file_repo(table: _PagedTable) -> DynamoDBRepository:
    repository = object.__new__(DynamoDBRepository)
    repository._table = table
    return repository


def test_reserve_uses_one_transaction_for_checksum_claim_and_file_record():
    table = _PagedTable(scan_pages=[{"Items": []}])
    table.name = "files"
    repository = _file_repo(table)
    repository._reservations_table_name = "reservations"
    repository._client = _TransactionClient()
    record = FileRecord(
        file_id="f1",
        user_id="u1",
        file_type="image",
        object_key="originals/u1/f1.jpg",
        filename="f1.jpg",
        content_type="image/jpeg",
        size_bytes=12,
        checksum="sha256:shared",
        status="pending_upload",
    )

    reserved, created = repository.reserve(record)

    assert reserved == record
    assert created is True
    writes = repository._client.calls[0]["TransactItems"]
    assert len(writes) == 2
    assert writes[0]["Put"]["TableName"] == "reservations"
    assert writes[0]["Put"]["ConditionExpression"] == (
        "attribute_not_exists(reservation_key)"
    )
    assert writes[1]["Put"]["TableName"] == "files"
    assert writes[1]["Put"]["ConditionExpression"] == "attribute_not_exists(file_id)"


def test_reserve_preserves_deduplication_for_legacy_file_without_claim():
    table = _PagedTable(
        scan_pages=[
            {
                "Items": [
                    _item(
                        "legacy",
                        "originals/u1/legacy.jpg",
                        checksum="sha256:shared",
                        filename="f1.jpg",
                        content_type="image/jpeg",
                        size_bytes=12,
                        status="completed",
                    )
                ]
            }
        ]
    )
    table.name = "files"
    reservations = _PagedTable()
    reservations.get_item = lambda **_kwargs: {}
    repository = _file_repo(table)
    repository._reservations = reservations
    repository._reservations_table_name = "reservations"
    repository._client = _TransactionClient()
    candidate = FileRecord(
        file_id="new",
        user_id="u1",
        file_type="image",
        object_key="originals/u1/new.jpg",
        filename="f1.jpg",
        content_type="image/jpeg",
        size_bytes=12,
        checksum="sha256:shared",
        status="pending_upload",
    )

    reserved, created = repository.reserve(candidate)

    assert reserved.file_id == "legacy"
    assert created is False
    assert len(repository._client.calls) == 1


def test_legacy_fallback_scan_atomically_checks_file_and_creates_claim():
    table = _PagedTable(
        scan_pages=[
            {
                "Items": [
                    _item(
                        "legacy",
                        "originals/u1/legacy.jpg",
                        checksum="sha256:shared",
                    )
                ]
            }
        ]
    )
    table.name = "files"
    reservations = _PagedTable()
    repository = _file_repo(table)
    repository._reservations = reservations
    repository._reservations_table_name = "reservations"
    repository._client = _TransactionClient()

    record = repository.find_by_user_checksum("u1", "sha256:shared")

    assert record.file_id == "legacy"
    assert table.scan_calls[0]["ConsistentRead"] is True
    writes = repository._client.calls[0]["TransactItems"]
    assert writes[0] == {
        "ConditionCheck": {
            "TableName": "files",
            "Key": {"file_id": {"S": "legacy"}},
            "ConditionExpression": (
                "attribute_exists(file_id) AND user_id = :user_id "
                "AND checksum = :checksum"
            ),
            "ExpressionAttributeValues": {
                ":user_id": {"S": "u1"},
                ":checksum": {"S": "sha256:shared"},
            },
        }
    }
    assert writes[1]["Put"]["TableName"] == "reservations"
    assert writes[1]["Put"]["ConditionExpression"] == (
        "attribute_not_exists(reservation_key)"
    )
    assert writes[1]["Put"]["Item"]["file_id"] == {"S": "legacy"}


def test_legacy_claim_competition_loser_reads_the_winning_claim():
    table = _PagedTable(
        scan_pages=[
            {
                "Items": [
                    _item(
                        "legacy",
                        "originals/u1/legacy.jpg",
                        checksum="sha256:shared",
                    )
                ]
            }
        ]
    )
    table.name = "files"
    table.get_item = lambda **kwargs: {
        "Item": _item(
            "legacy",
            "originals/u1/legacy.jpg",
            checksum="sha256:shared",
        )
    }
    reservations = _PagedTable()
    get_results = iter(
        [
            {},
            {
                "Item": {
                    "reservation_key": "claim",
                    "file_id": "legacy",
                    "user_id": "u1",
                    "checksum": "sha256:shared",
                }
            },
        ]
    )
    reservations.get_item = lambda **_kwargs: next(get_results)

    repository = _file_repo(table)
    repository._reservations = reservations
    repository._reservations_table_name = "reservations"
    repository._client = _CanceledTransactionClient()

    record = repository.find_by_user_checksum("u1", "sha256:shared")

    assert record.file_id == "legacy"
    assert len(repository._client.calls) == 1


def test_legacy_claim_transaction_canceled_after_delete_returns_none():
    table = _PagedTable(
        scan_pages=[
            {
                "Items": [
                    _item(
                        "legacy",
                        "originals/u1/legacy.jpg",
                        checksum="sha256:shared",
                    )
                ]
            }
        ]
    )
    table.name = "files"
    reservations = _PagedTable()
    repository = _file_repo(table)
    repository._reservations = reservations
    repository._reservations_table_name = "reservations"
    repository._client = _CanceledTransactionClient()

    record = repository.find_by_user_checksum("u1", "sha256:shared")

    assert record is None
    assert table.get_calls == [
        {"Key": {"file_id": "legacy"}, "ConsistentRead": True}
    ]


def test_legacy_claim_transaction_real_failure_is_not_swallowed():
    legacy_item = _item(
        "legacy",
        "originals/u1/legacy.jpg",
        checksum="sha256:shared",
    )
    table = _PagedTable(scan_pages=[{"Items": [legacy_item]}])
    table.name = "files"
    table.get_item = lambda **_kwargs: {"Item": legacy_item}
    reservations = _PagedTable()
    repository = _file_repo(table)
    repository._reservations = reservations
    repository._reservations_table_name = "reservations"
    repository._client = _CanceledTransactionClient()

    with pytest.raises(ClientError) as caught:
        repository.find_by_user_checksum("u1", "sha256:shared")

    assert caught.value.response["Error"]["Code"] == "TransactionCanceledException"


def test_multiple_legacy_rows_for_one_checksum_fail_closed():
    table = _PagedTable(
        scan_pages=[
            {
                "Items": [
                    _item("legacy-1", "originals/u1/1.jpg", checksum="shared"),
                    _item("legacy-2", "originals/u1/2.jpg", checksum="shared"),
                ]
            }
        ]
    )
    repository = _file_repo(table)

    with pytest.raises(RuntimeError, match="multiple legacy"):
        repository.find_by_user_checksum("u1", "shared")


def test_canceled_new_reservation_reads_concurrent_winner():
    table = _PagedTable(scan_pages=[{"Items": []}])
    table.name = "files"
    winner = _item(
        "winner",
        "originals/u1/winner.jpg",
        checksum="sha256:shared",
        filename="f1.jpg",
        content_type="image/jpeg",
        size_bytes=12,
        status="pending_upload",
    )
    table.get_item = lambda **_kwargs: {"Item": winner}
    reservations = _PagedTable()
    get_results = iter(
        [
            {},
            {
                "Item": {
                    "reservation_key": "claim",
                    "file_id": "winner",
                    "user_id": "u1",
                    "checksum": "sha256:shared",
                }
            },
        ]
    )
    reservations.get_item = lambda **_kwargs: next(get_results)
    repository = _file_repo(table)
    repository._reservations = reservations
    repository._reservations_table_name = "reservations"
    repository._client = _CanceledTransactionClient()
    candidate = FileRecord(
        file_id="loser",
        user_id="u1",
        file_type="image",
        object_key="originals/u1/loser.jpg",
        filename="f1.jpg",
        content_type="image/jpeg",
        size_bytes=12,
        checksum="sha256:shared",
        status="pending_upload",
    )

    reserved, created = repository.reserve(candidate)

    assert reserved.file_id == "winner"
    assert created is False


def test_reuse_upload_is_conditioned_on_pending_or_failed_state():
    table = _PagedTable()
    table.updated = None
    repository = _file_repo(table)
    table.update_item = lambda **kwargs: {
        "Attributes": _item(
            "f1",
            "originals/u1/f1.jpg",
            filename="f1.jpg",
            content_type="image/jpeg",
            size_bytes=12,
            status="pending_upload",
        )
    } if not setattr(table, "updated", kwargs) else None

    reused = repository.reuse_upload("f1")

    assert reused.file_id == "f1"
    assert table.updated["ConditionExpression"] == "#s IN (:pending, :failed)"
    assert table.updated["ReturnValues"] == "ALL_NEW"


def test_delete_removes_file_and_checksum_reservation_in_one_transaction():
    table = _PagedTable()
    table.name = "files"
    table.get_item = lambda **_kwargs: {
        "Item": _item("f1", "originals/u1/f1.jpg", checksum="sha256:shared")
    }
    repository = _file_repo(table)
    repository._reservations_table_name = "reservations"
    repository._client = _TransactionClient()

    removed = repository.delete_by_ids(["f1"])

    assert removed == 1
    writes = repository._client.calls[0]["TransactItems"]
    assert writes == [
        {
            "Delete": {
                "TableName": "files",
                "Key": {"file_id": {"S": "f1"}},
                "ConditionExpression": (
                    "attribute_not_exists(file_id) OR "
                    "(user_id = :user_id AND checksum = :checksum)"
                ),
                "ExpressionAttributeValues": {
                    ":user_id": {"S": "u1"},
                    ":checksum": {"S": "sha256:shared"},
                },
            }
        },
        {
            "Delete": {
                "TableName": "reservations",
                "Key": {
                    "reservation_key": {
                        "S": repository._reservation_key("u1", "sha256:shared")
                    }
                },
                "ConditionExpression": (
                    "attribute_not_exists(reservation_key) OR file_id = :file_id"
                ),
                "ExpressionAttributeValues": {":file_id": {"S": "f1"}},
            }
        },
    ]


def test_late_delete_cancellation_is_idempotent_when_old_file_is_already_gone():
    table = _PagedTable()
    table.name = "files"
    responses = iter(
        [
            {"Item": _item("f1", "originals/u1/f1.jpg", checksum="shared")},
            {},
        ]
    )
    table.get_item = lambda **_kwargs: next(responses)
    repository = _file_repo(table)
    repository._reservations_table_name = "reservations"
    repository._client = _CanceledTransactionClient()

    removed = repository.delete_by_ids(["f1"])

    assert removed == 1


def test_late_delete_does_not_hide_cancellation_when_file_id_now_exists():
    table = _PagedTable()
    table.name = "files"
    old = _item("f1", "originals/u1/f1.jpg", checksum="old")
    replacement = _item("f1", "originals/u1/f1-new.jpg", checksum="new")
    responses = iter([{"Item": old}, {"Item": replacement}])
    table.get_item = lambda **_kwargs: next(responses)
    repository = _file_repo(table)
    repository._reservations_table_name = "reservations"
    repository._client = _CanceledTransactionClient()

    with pytest.raises(ClientError):
        repository.delete_by_ids(["f1"])


def test_processing_lease_acquisition_is_one_conditional_update():
    table = _PagedTable()
    table.update_item = lambda **kwargs: {
        "Attributes": _item(
            "f1",
            "originals/u1/f1.jpg",
            status="processing",
            processing_sequencer="seq-1",
            lease_expires_at="2026-08-26T00:15:00+00:00",
        )
    } if not setattr(table, "updated", kwargs) else None
    repository = _file_repo(table)
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    expires = datetime(2026, 8, 26, 0, 15, tzinfo=timezone.utc)

    state = repository.try_acquire_processing("f1", "seq-1", now, expires)

    assert state == "acquired"
    assert table.updated["ConditionExpression"] == (
        "attribute_exists(file_id) AND #s <> :completed AND "
        "(#s <> :processing OR attribute_not_exists(lease_expires_at) OR "
        "lease_expires_at <= :now)"
    )
    assert table.updated["ReturnValues"] == "ALL_NEW"


def _notification_repo(
    subscriptions: _PagedTable, notifications: _PagedTable
) -> DynamoDBNotificationRepository:
    repository = object.__new__(DynamoDBNotificationRepository)
    repository._subscriptions = subscriptions
    repository._notifications = notifications
    return repository


def test_by_keys_filters_file_records_returned_by_all():
    table = _PagedTable(
        scan_pages=[
            {
                "Items": [
                    _item("a", "originals/u1/a.jpg"),
                    _item("b", "originals/u2/b.jpg", thumbnail_key="thumbnails/u2/b.jpg"),
                ]
            }
        ]
    )

    records = _file_repo(table).by_keys(["thumbnails/u2/b.jpg"])

    assert [record.file_id for record in records] == ["b"]


def test_file_scans_follow_last_evaluated_key():
    table = _PagedTable(
        scan_pages=[
            {
                "Items": [_item("a", "originals/u1/a.jpg")],
                "LastEvaluatedKey": {"page": 1},
            },
            {"Items": [_item("b", "originals/u2/b.jpg")]},
        ]
    )

    records = _file_repo(table).all()

    assert [record.file_id for record in records] == ["a", "b"]


def test_notification_scans_and_queries_follow_last_evaluated_key():
    subscriptions = _PagedTable(
        scan_pages=[
            {
                "Items": [{"user_id": "u1", "species": "wombat"}],
                "LastEvaluatedKey": {"page": 1},
            },
            {"Items": [{"user_id": "u2", "species": "wombat"}]},
        ],
        query_pages=[
            {
                "Items": [{"user_id": "u1", "species": "magpie"}],
                "LastEvaluatedKey": {"page": 1},
            },
            {"Items": [{"user_id": "u1", "species": "wombat"}]},
        ],
    )
    notifications = _PagedTable(
        query_pages=[
            {
                "Items": [
                    {
                        "notification_id": "n1",
                        "user_id": "u1",
                        "file_id": "f1",
                        "species": "wombat",
                        "object_key": "originals/u1/f1.jpg",
                        "created_at": "2026-08-25T00:00:00+00:00",
                    }
                ],
                "LastEvaluatedKey": {"page": 1},
            },
            {
                "Items": [
                    {
                        "notification_id": "n2",
                        "user_id": "u1",
                        "file_id": "f2",
                        "species": "magpie",
                        "object_key": "originals/u1/f2.jpg",
                        "created_at": "2026-08-26T00:00:00+00:00",
                    }
                ]
            },
        ]
    )
    repository = _notification_repo(subscriptions, notifications)

    assert repository.subscriptions("u1") == ["magpie", "wombat"]
    assert repository.subscribers_for_species("wombat") == ["u1", "u2"]
    assert [item.notification_id for item in repository.notifications("u1")] == [
        "n2",
        "n1",
    ]
    assert all(call["ConsistentRead"] is True for call in subscriptions.scan_calls)


def test_legacy_notifications_without_delivery_status_are_pending_and_scan_is_strong():
    subscriptions = _PagedTable()
    notifications = _PagedTable(
        scan_pages=[
            {
                "Items": [
                    {
                        "notification_id": "n1",
                        "user_id": "u1",
                        "file_id": "f1",
                        "species": "wombat",
                        "object_key": "originals/u1/f1.jpg",
                        "created_at": "2026-08-25T00:00:00+00:00",
                    }
                ]
            }
        ]
    )
    repository = _notification_repo(subscriptions, notifications)

    pending = repository.pending_for_file("f1")

    assert [item.notification_id for item in pending] == ["n1"]
    assert notifications.scan_calls[0]["ConsistentRead"] is True
    assert notifications.scan_calls[0]["FilterExpression"] == (
        "file_id = :f AND (attribute_not_exists(delivery_status) OR "
        "delivery_status = :pending)"
    )


def test_mark_delivered_uses_condition_and_treats_conflict_as_already_done():
    subscriptions = _PagedTable()
    notifications = _PagedTable()
    update_calls = []

    def concurrent_delivery(**kwargs):
        update_calls.append(kwargs)
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
        )

    notifications.update_item = concurrent_delivery
    repository = _notification_repo(subscriptions, notifications)
    notification = Notification(
        notification_id="n1",
        user_id="u1",
        file_id="f1",
        species="wombat",
        object_key="originals/u1/f1.jpg",
    )

    repository.mark_delivered(notification)

    assert update_calls[0] == {
        "Key": {"user_id": "u1", "notification_id": "n1"},
        "UpdateExpression": "SET delivery_status = :delivered",
        "ConditionExpression": (
            "attribute_not_exists(delivery_status) OR delivery_status = :pending"
        ),
        "ExpressionAttributeValues": {
            ":pending": "pending",
            ":delivered": "delivered",
        },
    }


def test_ensure_notification_does_not_overwrite_existing_delivery_state():
    subscriptions = _PagedTable()
    notifications = _PagedTable()
    put_calls = []

    def existing_notification(**kwargs):
        put_calls.append(kwargs)
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        )

    notifications.put_item = existing_notification
    repository = _notification_repo(subscriptions, notifications)
    notification = Notification(
        notification_id="stable",
        user_id="u1",
        file_id="f1",
        species="wombat",
        object_key="originals/u1/f1.jpg",
    )

    repository.add_notification(notification)

    assert put_calls[0]["ConditionExpression"] == (
        "attribute_not_exists(notification_id)"
    )


def test_pending_notifications_are_scanned_by_file_and_marked_delivered():
    subscriptions = _PagedTable()
    notifications = _PagedTable(
        scan_pages=[
            {
                "Items": [
                    {
                        "notification_id": "n1",
                        "user_id": "u1",
                        "file_id": "f1",
                        "species": "wombat",
                        "object_key": "originals/u1/f1.jpg",
                        "created_at": "2026-08-25T00:00:00+00:00",
                        "delivery_status": "pending",
                    }
                ]
            }
        ]
    )
    repository = _notification_repo(subscriptions, notifications)

    pending = repository.pending_for_file("f1")
    repository.mark_delivered(pending[0])

    assert [item.notification_id for item in pending] == ["n1"]
    assert notifications.updated == {
        "Key": {"user_id": "u1", "notification_id": "n1"},
        "UpdateExpression": "SET delivery_status = :delivered",
        "ConditionExpression": (
            "attribute_not_exists(delivery_status) OR delivery_status = :pending"
        ),
        "ExpressionAttributeValues": {
            ":pending": "pending",
            ":delivered": "delivered",
        },
    }


def test_completion_converts_nested_floats_at_aws_boundary():
    table = _PagedTable()
    repository = _file_repo(table)

    repository.mark_completed(
        "f1",
        "originals/u1/f1.jpg",
        None,
        "image",
        {"wombat": 2},
        [
            {
                "species": "wombat",
                "confidence": 0.94,
                "box": {"x": 0.1, "pixels": 4},
            }
        ],
        "v1",
    )

    values = table.updated["ExpressionAttributeValues"]
    assert values[":d"][0]["confidence"] == Decimal("0.94")
    assert values[":d"][0]["box"] == {"x": Decimal("0.1"), "pixels": 4}
    assert values[":t"] == {"wombat": 2}


def test_completion_with_thumbnail_builds_valid_set_and_remove_expression():
    table = _PagedTable()
    repository = _file_repo(table)

    repository.mark_completed(
        "f1",
        "originals/u1/f1.jpg",
        "thumbnails/u1/f1.jpg",
        "image",
        {"wombat": 1},
        [],
        "v1",
    )

    assert table.updated["UpdateExpression"] == (
        "SET #s = :s, object_key = :o, file_type = :ft, #tags = :t, "
        "detections = :d, model_version = :m, thumbnail_key = :th "
        "REMOVE processing_sequencer, lease_expires_at, error_code, message"
    )
    assert table.updated["ConditionExpression"] == (
        "attribute_exists(file_id) AND #s <> :completed"
    )
    assert table.updated["ExpressionAttributeValues"][":completed"] == "completed"


def test_concurrent_completed_winner_is_preserved_as_idempotent_noop():
    table = _PagedTable()
    winner = _item(
        "f1",
        "originals/u1/f1.jpg",
        status="completed",
        tags={"wombat": Decimal("1")},
    )

    def lose_to_completed(**_kwargs):
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
        )

    table.update_item = lose_to_completed
    table.get_item = lambda **_kwargs: {"Item": winner}
    repository = _file_repo(table)

    repository.mark_completed(
        "f1", "originals/u1/f1.jpg", None, "image", {"fox": 9}, [], "late"
    )

    assert repository.get("f1").tags == {"wombat": Decimal("1")}


def test_completion_condition_failure_after_delete_is_not_swallowed():
    table = _PagedTable()

    def deleted_before_update(**_kwargs):
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
        )

    table.update_item = deleted_before_update
    repository = _file_repo(table)

    with pytest.raises(ClientError):
        repository.mark_completed(
            "f1", "originals/u1/f1.jpg", None, "image", {}, [], "v1"
        )


def test_tag_update_requires_existing_file_to_prevent_delete_ghost():
    table = _PagedTable()
    repository = _file_repo(table)

    repository.update_tags("f1", {"wombat": 2})

    assert table.updated == {
        "Key": {"file_id": "f1"},
        "UpdateExpression": "SET #tags = :t",
        "ConditionExpression": "attribute_exists(file_id)",
        "ExpressionAttributeNames": {"#tags": "tags"},
        "ExpressionAttributeValues": {":t": {"wombat": 2}},
    }


def test_stale_failed_callback_cannot_downgrade_completed_dynamo_record():
    table = _PagedTable()
    completed = _item(
        "f1", "originals/u1/f1.jpg", status="completed", checksum="checksum"
    )
    update_calls = []

    def completed_wins(**kwargs):
        update_calls.append(kwargs)
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
        )

    table.update_item = completed_wins
    table.get_item = lambda **_kwargs: {"Item": completed}
    repository = _file_repo(table)

    repository.mark_failed("f1", "INFERENCE_FAILED", "late callback")

    assert update_calls[0]["ConditionExpression"] == (
        "attribute_exists(file_id) AND #s <> :completed"
    )
    assert update_calls[0]["ExpressionAttributeValues"][":completed"] == "completed"


def test_failed_callback_condition_error_is_rethrown_when_record_is_not_completed():
    table = _PagedTable()

    def unexpected_conflict(**_kwargs):
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
        )

    table.update_item = unexpected_conflict
    table.get_item = lambda **_kwargs: {
        "Item": _item("f1", "originals/u1/f1.jpg", status="processing")
    }
    repository = _file_repo(table)

    with pytest.raises(ClientError):
        repository.mark_failed("f1", "INFERENCE_FAILED", "failure")


def test_from_item_exposes_json_compatible_detection_numbers_and_integer_tags():
    record = DynamoDBRepository._from_item(
        _item(
            "f1",
            "originals/u1/f1.jpg",
            detections=[
                {
                    "species": "wombat",
                    "confidence": Decimal("0.94"),
                    "box": {"x": Decimal("0.1"), "pixels": Decimal("4")},
                }
            ],
        )
    )

    assert record.tags == {"wombat": 2}
    assert isinstance(record.tags["wombat"], int)
    assert record.detections == [
        {
            "species": "wombat",
            "confidence": 0.94,
            "box": {"x": 0.1, "pixels": 4.0},
        }
    ]
    assert record.upload_time == datetime(2026, 8, 26, tzinfo=timezone.utc)
