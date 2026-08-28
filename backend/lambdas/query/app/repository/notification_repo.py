"""Subscription & notification repository.

Member D owns the data model, completion trigger, durable inbox, and SNS
publisher. Member E consumes the public endpoints for the frontend and in-app
notification experience.

Two backends, one interface, mirroring :class:`FileRepository`:

- ``SQLiteNotificationRepository`` — local, no AWS account needed.
- ``DynamoDBNotificationRepository`` — cloud (table names below).

Subscriptions are keyed by ``(user_id, species)``; notifications by a UUID.
Both tables are tiny, so lookups are a ``Scan``/in-memory filter on DynamoDB,
identical to the file repository (correct and simple at this scale).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.repository.dynamodb_repo import _query_all, _scan_all
from app.schemas import Notification


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


class NotificationRepository(ABC):
    @abstractmethod
    def subscribe(self, user_id: str, species: str) -> None:
        """Record a subscription (idempotent)."""

    @abstractmethod
    def unsubscribe(self, user_id: str, species: str) -> None:
        """Remove a subscription (idempotent)."""

    @abstractmethod
    def subscriptions(self, user_id: str) -> list[str]:
        """Return the species a user is subscribed to, sorted."""

    @abstractmethod
    def subscribers_for_species(self, species: str) -> list[str]:
        """Return every user_id subscribed to `species` (for the trigger)."""

    @abstractmethod
    def add_notification(self, notification: Notification) -> None:
        """Store one notification."""

    @abstractmethod
    def pending_for_file(self, file_id: str) -> list[Notification]:
        """Return notifications for a file whose external delivery is pending."""

    @abstractmethod
    def mark_delivered(self, notification: Notification) -> None:
        """Mark one pending notification as externally delivered."""

    @abstractmethod
    def notifications(self, user_id: str) -> list[Notification]:
        """Return a user's notifications, newest first."""


# ---------------------------------------------------------------------------
# SQLite (local)
# ---------------------------------------------------------------------------
_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id  TEXT NOT NULL,
    species  TEXT NOT NULL,
    PRIMARY KEY (user_id, species)
);
CREATE TABLE IF NOT EXISTS notifications (
    notification_id TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    file_id         TEXT NOT NULL,
    species         TEXT NOT NULL,
    object_key      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_notif_user    ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_sub_species   ON subscriptions(species);
"""


class SQLiteNotificationRepository(NotificationRepository):
    def __init__(self, db_path: str = "data/pacific_bioarchive.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        import sqlite3

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SQLITE_SCHEMA)
        notification_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(notifications)").fetchall()
        }
        if "delivery_status" not in notification_columns:
            self._conn.execute(
                "ALTER TABLE notifications ADD COLUMN delivery_status "
                "TEXT NOT NULL DEFAULT 'pending'"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notif_delivery "
            "ON notifications(file_id, delivery_status)"
        )
        self._conn.commit()

    def subscribe(self, user_id: str, species: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO subscriptions(user_id, species) VALUES (?,?)",
            (user_id, species),
        )
        self._conn.commit()

    def unsubscribe(self, user_id: str, species: str) -> None:
        self._conn.execute(
            "DELETE FROM subscriptions WHERE user_id=? AND species=?",
            (user_id, species),
        )
        self._conn.commit()

    def subscriptions(self, user_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT species FROM subscriptions WHERE user_id=? ORDER BY species",
            (user_id,),
        ).fetchall()
        return [r["species"] for r in rows]

    def subscribers_for_species(self, species: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT user_id FROM subscriptions WHERE species=? ORDER BY user_id",
            (species,),
        ).fetchall()
        return [r["user_id"] for r in rows]

    def add_notification(self, notification: Notification) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO notifications "
            "(notification_id, user_id, file_id, species, object_key, created_at, "
            "delivery_status) VALUES (?,?,?,?,?,?,'pending')",
            (
                notification.notification_id,
                notification.user_id,
                notification.file_id,
                notification.species,
                notification.object_key,
                notification.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    @staticmethod
    def _notification_from_row(row) -> Notification:
        return Notification(
            notification_id=row["notification_id"],
            user_id=row["user_id"],
            file_id=row["file_id"],
            species=row["species"],
            object_key=row["object_key"],
            created_at=_dt(row["created_at"]),
        )

    def pending_for_file(self, file_id: str) -> list[Notification]:
        rows = self._conn.execute(
            "SELECT * FROM notifications "
            "WHERE file_id=? AND delivery_status='pending' ORDER BY created_at",
            (file_id,),
        ).fetchall()
        return [self._notification_from_row(row) for row in rows]

    def mark_delivered(self, notification: Notification) -> None:
        self._conn.execute(
            "UPDATE notifications SET delivery_status='delivered' "
            "WHERE notification_id=? AND delivery_status='pending'",
            (notification.notification_id,),
        )
        self._conn.commit()

    def notifications(self, user_id: str) -> list[Notification]:
        rows = self._conn.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [self._notification_from_row(row) for row in rows]


# ---------------------------------------------------------------------------
# DynamoDB (cloud)
# ---------------------------------------------------------------------------
class DynamoDBNotificationRepository(NotificationRepository):
    def __init__(
        self,
        subscriptions_table: str = "PacificBioArchiveSubscriptions",
        notifications_table: str = "PacificBioArchiveNotifications",
        region: str = "ap-southeast-2",
    ) -> None:
        import boto3  # lazy import: only needed when deployed to AWS

        resource = boto3.resource("dynamodb", region_name=region)
        self._subscriptions = resource.Table(subscriptions_table)
        self._notifications = resource.Table(notifications_table)

    def subscribe(self, user_id: str, species: str) -> None:
        self._subscriptions.put_item(
            Item={"user_id": user_id, "species": species}
        )

    def unsubscribe(self, user_id: str, species: str) -> None:
        self._subscriptions.delete_item(
            Key={"user_id": user_id, "species": species}
        )

    def subscriptions(self, user_id: str) -> list[str]:
        items = _query_all(
            self._subscriptions,
            KeyConditionExpression="user_id = :u",
            ExpressionAttributeValues={":u": user_id},
        )
        return sorted(item["species"] for item in items)

    def subscribers_for_species(self, species: str) -> list[str]:
        items = _scan_all(
            self._subscriptions,
            FilterExpression="species = :s",
            ExpressionAttributeValues={":s": species},
            ConsistentRead=True,
        )
        return sorted(item["user_id"] for item in items)

    def add_notification(self, notification: Notification) -> None:
        import botocore.exceptions

        try:
            self._notifications.put_item(
                Item={
                    "user_id": notification.user_id,
                    "notification_id": notification.notification_id,
                    "file_id": notification.file_id,
                    "species": notification.species,
                    "object_key": notification.object_key,
                    "created_at": notification.created_at.isoformat(),
                    "delivery_status": "pending",
                },
                ConditionExpression="attribute_not_exists(notification_id)",
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return
            raise

    @staticmethod
    def _notification_from_item(item: dict) -> Notification:
        return Notification(
            notification_id=item["notification_id"],
            user_id=item["user_id"],
            file_id=item["file_id"],
            species=item["species"],
            object_key=item["object_key"],
            created_at=_dt(item.get("created_at")),
        )

    def pending_for_file(self, file_id: str) -> list[Notification]:
        items = _scan_all(
            self._notifications,
            FilterExpression=(
                "file_id = :f AND (attribute_not_exists(delivery_status) OR "
                "delivery_status = :pending)"
            ),
            ExpressionAttributeValues={":f": file_id, ":pending": "pending"},
            ConsistentRead=True,
        )
        result = [self._notification_from_item(item) for item in items]
        result.sort(key=lambda notification: notification.created_at)
        return result

    def mark_delivered(self, notification: Notification) -> None:
        import botocore.exceptions

        try:
            self._notifications.update_item(
                Key={
                    "user_id": notification.user_id,
                    "notification_id": notification.notification_id,
                },
                UpdateExpression="SET delivery_status = :delivered",
                ConditionExpression=(
                    "attribute_not_exists(delivery_status) OR "
                    "delivery_status = :pending"
                ),
                ExpressionAttributeValues={
                    ":pending": "pending",
                    ":delivered": "delivered",
                },
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return
            raise

    def notifications(self, user_id: str) -> list[Notification]:
        items = _query_all(
            self._notifications,
            KeyConditionExpression="user_id = :u",
            ExpressionAttributeValues={":u": user_id},
        )
        result = [self._notification_from_item(item) for item in items]
        result.sort(key=lambda n: n.created_at, reverse=True)
        return result
