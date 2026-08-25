"""Behavioral tests for Member D's DynamoDB serialization boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.repository.dynamodb_repo import DynamoDBRepository
from app.repository.notification_repo import DynamoDBNotificationRepository
from app.schemas import FileRecord


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

    @staticmethod
    def _page(pages, kwargs):
        index = 0 if "ExclusiveStartKey" not in kwargs else kwargs["ExclusiveStartKey"]["page"]
        return pages[index]

    def scan(self, **kwargs):
        return self._page(self.scan_pages, kwargs)

    def query(self, **kwargs):
        return self._page(self.query_pages, kwargs)

    def update_item(self, **kwargs):
        self.updated = kwargs


def _file_repo(table: _PagedTable) -> DynamoDBRepository:
    repository = object.__new__(DynamoDBRepository)
    repository._table = table
    return repository


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
