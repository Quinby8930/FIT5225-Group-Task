"""Verify/backfill DynamoDB upload reservation claims during a paused cutover."""

from __future__ import annotations

import argparse
import json

from app.repository.dynamodb_repo import DynamoDBRepository, _scan_all


class MigrationError(RuntimeError):
    """The existing file/claim data cannot be migrated safely."""


def _expected_claim(file_item: dict) -> dict:
    for field in ("file_id", "user_id", "checksum"):
        if not isinstance(file_item.get(field), str) or not file_item[field]:
            raise MigrationError(f"file row has invalid {field}")
    return {
        "reservation_key": DynamoDBRepository._reservation_key(
            file_item["user_id"], file_item["checksum"]
        ),
        "file_id": file_item["file_id"],
        "user_id": file_item["user_id"],
        "checksum": file_item["checksum"],
    }


def _validated_claim(claim: dict) -> dict:
    for field in ("reservation_key", "file_id", "user_id", "checksum"):
        if not isinstance(claim.get(field), str) or not claim[field]:
            raise MigrationError(f"invalid reservation claim {field}")
    expected_key = DynamoDBRepository._reservation_key(
        claim["user_id"], claim["checksum"]
    )
    if claim["reservation_key"] != expected_key:
        raise MigrationError("invalid reservation claim key")
    return claim


def _require_matching_claim(actual: dict | None, expected: dict) -> None:
    if actual is None:
        raise MigrationError("reservation claim disappeared during migration")
    if any(actual.get(field) != value for field, value in expected.items()):
        raise MigrationError("reservation claim points to a different file")


def _file_still_matches(files_table, expected: dict) -> bool:
    item = files_table.get_item(
        Key={"file_id": expected["file_id"]}, ConsistentRead=True
    ).get("Item")
    return bool(
        item
        and item.get("user_id") == expected["user_id"]
        and item.get("checksum") == expected["checksum"]
    )


def _backfill_one(
    files_table, reservations_table, transaction_client, expected: dict
) -> bool:
    import botocore.exceptions

    try:
        transaction_client.transact_write_items(
            TransactItems=[
                {
                    "ConditionCheck": {
                        "TableName": files_table.name,
                        "Key": DynamoDBRepository._serialize_item(
                            {"file_id": expected["file_id"]}
                        ),
                        "ConditionExpression": (
                            "attribute_exists(file_id) AND user_id = :user_id "
                            "AND checksum = :checksum"
                        ),
                        "ExpressionAttributeValues": DynamoDBRepository._serialize_item(
                            {
                                ":user_id": expected["user_id"],
                                ":checksum": expected["checksum"],
                            }
                        ),
                    }
                },
                {
                    "Put": {
                        "TableName": reservations_table.name,
                        "Item": DynamoDBRepository._serialize_item(expected),
                        "ConditionExpression": (
                            "attribute_not_exists(reservation_key)"
                        ),
                    }
                },
            ]
        )
        return True
    except botocore.exceptions.ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            raise
        winner = reservations_table.get_item(
            Key={"reservation_key": expected["reservation_key"]},
            ConsistentRead=True,
        ).get("Item")
        if winner is not None:
            _require_matching_claim(_validated_claim(winner), expected)
            return False
        if not _file_still_matches(files_table, expected):
            raise MigrationError(
                "file was deleted or changed while creating reservation claim"
            ) from exc
        raise


def migrate_reservations(
    files_table, reservations_table, transaction_client, *, apply: bool
) -> dict:
    """Strongly verify both sides and atomically create missing claims."""

    files = _scan_all(files_table, ConsistentRead=True)
    claims = _scan_all(reservations_table, ConsistentRead=True)

    expected_by_key: dict[str, dict] = {}
    for file_item in files:
        expected = _expected_claim(file_item)
        key = expected["reservation_key"]
        previous = expected_by_key.get(key)
        if previous is not None and previous["file_id"] != expected["file_id"]:
            raise MigrationError("multiple files share one user/checksum")
        expected_by_key[key] = expected

    actual_by_key: dict[str, dict] = {}
    for raw_claim in claims:
        actual = _validated_claim(raw_claim)
        key = actual["reservation_key"]
        if key in actual_by_key:
            raise MigrationError("duplicate reservation claims share one key")
        actual_by_key[key] = actual

    extra_keys = actual_by_key.keys() - expected_by_key.keys()
    report = {
        "files": len(files),
        "claims": len(claims),
        "claims_verified": 0,
        "claims_missing": 0,
        "claims_extra": len(extra_keys),
        "claims_created": 0,
    }
    if apply and extra_keys:
        raise MigrationError("orphan reservation claims must be resolved before backfill")

    for key, expected in expected_by_key.items():
        actual = actual_by_key.get(key)
        if actual is not None:
            _require_matching_claim(actual, expected)
            report["claims_verified"] += 1
            continue
        report["claims_missing"] += 1
        if not apply:
            continue
        if _backfill_one(files_table, reservations_table, transaction_client, expected):
            report["claims_created"] += 1
        else:
            report["claims_verified"] += 1
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("verify", "backfill"))
    parser.add_argument("--files-table", required=True)
    parser.add_argument("--reservations-table", required=True)
    parser.add_argument("--region", default="ap-southeast-2")
    parser.add_argument(
        "--confirm-uploads-paused",
        action="store_true",
        help=(
            "confirm all Files/Reservations mutations are paused; the option "
            "name is retained for compatibility"
        ),
    )
    args = parser.parse_args(argv)
    if args.mode == "backfill" and not args.confirm_uploads_paused:
        parser.error("backfill requires --confirm-uploads-paused")

    import boto3

    resource = boto3.resource("dynamodb", region_name=args.region)
    transaction_client = resource.meta.client
    report = migrate_reservations(
        resource.Table(args.files_table),
        resource.Table(args.reservations_table),
        transaction_client,
        apply=args.mode == "backfill",
    )
    print(json.dumps(report, sort_keys=True))
    if args.mode == "verify" and (
        report["claims_missing"] or report["claims_extra"]
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
