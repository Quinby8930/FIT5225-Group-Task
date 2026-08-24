"""Notification delivery abstraction — the *trigger*'s outbound hook.

When a completed file's tags match a subscription, the notification trigger
writes a durable :class:`Notification` record and then hands it to a
``NotificationPublisher`` to actually reach the user. Member D owns the trigger;
Member E owns the delivery UX (and the real SNS/email/push implementation), so
this mirrors the ``StorageClient`` / ``TagDetector`` integration-slot pattern.

The real implementation would publish to SNS with the subscribed user's topic /
endpoint; the stub below just logs, which is enough for local development and
for the trigger's unit tests.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.schemas import Notification

logger = logging.getLogger(__name__)


class NotificationPublisher(ABC):
    @abstractmethod
    def publish(self, notification: Notification) -> None:
        """Deliver one notification to its subscribed user."""


class StubNotificationPublisher(NotificationPublisher):
    def publish(self, notification: Notification) -> None:
        logger.info(
            "[stub] notify user %s: species '%s' matched in %s",
            notification.user_id,
            notification.species,
            notification.object_key,
        )
