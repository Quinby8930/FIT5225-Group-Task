from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from migrate_reservations import MigrationError, main, migrate_reservations


class _Table:
    def __init__(self, name, items=None):
        self.name = name
        self.items = items or []
        self.scan_calls = []
        self.get_calls = []

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        return {"Items": self.items}

    def get_item(self, **kwargs):
        self.get_calls.append(kwargs)
        key_name, key_value = next(iter(kwargs["Key"].items()))
        item = next((row for row in self.items if row.get(key_name) == key_value), None)
        return {"Item": item} if item is not None else {}


class _TransactionClient:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def transact_write_items(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


def _file(file_id, checksum="checksum"):
    return {"file_id": file_id, "user_id": "u1", "checksum": checksum}


def _claim(file_id, checksum="checksum"):
    from app.repository.dynamodb_repo import DynamoDBRepository

    return {
        "reservation_key": DynamoDBRepository._reservation_key("u1", checksum),
        "file_id": file_id,
        "user_id": "u1",
        "checksum": checksum,
    }


def _canceled():
    return ClientError(
        {"Error": {"Code": "TransactionCanceledException"}},
        "TransactWriteItems",
    )


def test_verify_reports_missing_claims_with_strong_scans_and_no_writes():
    files = _Table("files", [_file("f1"), _file("f2", "other")])
    claims = _Table("reservations")
    client = _TransactionClient()

    report = migrate_reservations(files, claims, client, apply=False)

    assert report == {
        "files": 2,
        "claims": 0,
        "claims_verified": 0,
        "claims_missing": 2,
        "claims_extra": 0,
        "claims_created": 0,
    }
    assert files.scan_calls == [{"ConsistentRead": True}]
    assert claims.scan_calls == [{"ConsistentRead": True}]
    assert client.calls == []


def test_verify_reports_orphan_claim_when_files_table_is_empty():
    files = _Table("files")
    claims = _Table("reservations", [_claim("missing")])

    report = migrate_reservations(files, claims, _TransactionClient(), apply=False)

    assert report["claims_extra"] == 1
    assert files.scan_calls == [{"ConsistentRead": True}]
    assert claims.scan_calls == [{"ConsistentRead": True}]


@pytest.mark.parametrize(
    ("file_items", "claim_items"),
    [([_file("f1")], []), ([], [_claim("missing")])],
    ids=["missing", "extra"],
)
def test_verify_cli_exits_nonzero_for_non_bijective_tables(
    monkeypatch, file_items, claim_items
):
    import sys
    from types import SimpleNamespace

    files = _Table("files", file_items)
    claims = _Table("reservations", claim_items)
    resource = SimpleNamespace(
        Table=lambda name: files if name == "files" else claims,
        meta=SimpleNamespace(client=_TransactionClient()),
    )
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(resource=lambda *_args, **_kwargs: resource),
    )

    exit_code = main(
        [
            "verify",
            "--files-table",
            "files",
            "--reservations-table",
            "reservations",
        ]
    )

    assert exit_code == 2


def test_verify_fails_closed_when_claim_points_to_wrong_file():
    files = _Table("files", [_file("f1")])
    claims = _Table("reservations", [_claim("missing")])

    with pytest.raises(MigrationError, match="different file"):
        migrate_reservations(files, claims, _TransactionClient(), apply=False)


@pytest.mark.parametrize(
    "claim",
    [
        {"reservation_key": "bad", "file_id": "f1", "user_id": "u1"},
        {
            "reservation_key": "bad",
            "file_id": "f1",
            "user_id": "u1",
            "checksum": "checksum",
        },
    ],
)
def test_verify_fails_closed_for_invalid_claim(claim):
    with pytest.raises(MigrationError, match="invalid reservation claim"):
        migrate_reservations(
            _Table("files", [_file("f1")]),
            _Table("reservations", [claim]),
            _TransactionClient(),
            apply=False,
        )


def test_backfill_condition_checks_file_and_conditionally_puts_claim_atomically():
    files = _Table("files", [_file("f1")])
    claims = _Table("reservations")
    client = _TransactionClient()

    report = migrate_reservations(files, claims, client, apply=True)

    assert report["claims_created"] == 1
    writes = client.calls[0]["TransactItems"]
    assert writes == [
        {
            "ConditionCheck": {
                "TableName": "files",
                "Key": {"file_id": {"S": "f1"}},
                "ConditionExpression": (
                    "attribute_exists(file_id) AND user_id = :user_id "
                    "AND checksum = :checksum"
                ),
                "ExpressionAttributeValues": {
                    ":user_id": {"S": "u1"},
                    ":checksum": {"S": "checksum"},
                },
            }
        },
        {
            "Put": {
                "TableName": "reservations",
                "Item": {
                    "reservation_key": {"S": _claim("f1")["reservation_key"]},
                    "file_id": {"S": "f1"},
                    "user_id": {"S": "u1"},
                    "checksum": {"S": "checksum"},
                },
                "ConditionExpression": "attribute_not_exists(reservation_key)",
            }
        },
    ]


def test_backfill_fails_closed_when_files_already_violate_uniqueness():
    files = _Table("files", [_file("f1"), _file("f2")])

    with pytest.raises(MigrationError, match="multiple files"):
        migrate_reservations(files, _Table("reservations"), _TransactionClient(), apply=True)


def test_backfill_transaction_loser_accepts_only_same_concurrent_winner():
    files = _Table("files", [_file("f1")])
    claims = _Table("reservations")
    client = _TransactionClient(_canceled())
    original_get = claims.get_item

    def concurrent_winner(**kwargs):
        claims.items = [_claim("f1")]
        claims.get_item = original_get
        return original_get(**kwargs)

    claims.get_item = concurrent_winner

    report = migrate_reservations(files, claims, client, apply=True)

    assert report["claims_verified"] == 1
    assert report["claims_created"] == 0


def test_backfill_transaction_cancellation_after_concurrent_delete_fails_closed():
    files = _Table("files", [_file("f1")])
    claims = _Table("reservations")
    client = _TransactionClient(_canceled())

    def deleted_file(**kwargs):
        files.items = []
        return {}

    files.get_item = deleted_file

    with pytest.raises(MigrationError, match="deleted or changed"):
        migrate_reservations(files, claims, client, apply=True)
