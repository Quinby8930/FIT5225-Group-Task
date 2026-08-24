"""Pure notification-trigger logic — no framework or database dependency.

Given a completed file's tag counts and a way to look up who subscribes to a
species, produce the notifications to write and deliver. Kept free of the
repository so it is trivially unit-testable and identical on SQLite/DynamoDB.
"""

from __future__ import annotations

from uuid import uuid4
from typing import Callable

from app.schemas import Notification


def build_notifications(
    file_id: str,
    object_key: str,
    tags: dict[str, int],
    subscribers_for_species: Callable[[str], list[str]],
) -> list[Notification]:
    """Return one notification per (subscribed user, matched species).

    A tag only counts when at least one individual of that species was detected
    (`count >= 1`). Every user subscribed to a matched species gets one
    notification pointing at the completed file.
    """
    notifications: list[Notification] = []
    for species, count in tags.items():
        if count < 1:
            continue
        for user_id in subscribers_for_species(species):
            notifications.append(
                Notification(
                    notification_id=str(uuid4()),
                    user_id=user_id,
                    file_id=file_id,
                    species=species,
                    object_key=object_key,
                )
            )
    return notifications
