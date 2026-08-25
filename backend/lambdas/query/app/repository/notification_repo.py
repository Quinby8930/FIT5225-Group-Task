"""Subscription & notification repository.

Member D owns the *data model* (``subscriptions`` and ``notifications`` tables)
and the *trigger* that writes notifications when a completed file's tags match a
subscription. The delivery channel (SNS/email/push) is Member E's concern — the
trigger just writes a durable record and hands it to a ``NotificationPublisher``.

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
    created_at      TEXT NOT NULL
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
            "INSERT OR IGNORE INTO notifications VALUES (?,?,?,?,?,?)",
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

    def notifications(self, user_id: str) -> list[Notification]:
        rows = self._conn.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [
            Notification(
                notification_id=r["notification_id"],
                user_id=r["user_id"],
                file_id=r["file_id"],
                species=r["species"],
                object_key=r["object_key"],
                created_at=_dt(r["created_at"]),
            )
            for r in rows
        ]


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
        )
        return sorted(item["user_id"] for item in items)

    def add_notification(self, notification: Notification) -> None:
        self._notifications.put_item(
            Item={
                "user_id": notification.user_id,
                "notification_id": notification.notification_id,
                "file_id": notification.file_id,
                "species": notification.species,
                "object_key": notification.object_key,
                "created_at": notification.created_at.isoformat(),
            }
        )

    def notifications(self, user_id: str) -> list[Notification]:
        items = _query_all(
            self._notifications,
            KeyConditionExpression="user_id = :u",
            ExpressionAttributeValues={":u": user_id},
        )
        result = [
            Notification(
                notification_id=i["notification_id"],
                user_id=i["user_id"],
                file_id=i["file_id"],
                species=i["species"],
                object_key=i["object_key"],
                created_at=_dt(i.get("created_at")),
            )
            for i in items
        ]
        result.sort(key=lambda n: n.created_at, reverse=True)
        return result
