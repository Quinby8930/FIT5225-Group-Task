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
import json

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


class SNSNotificationPublisher(NotificationPublisher):
    def __init__(
        self,
        *,
        region: str,
        topic_arn: str = "",
        topic_arn_template: str = "",
    ) -> None:
        if not topic_arn and not topic_arn_template:
            raise ValueError("SNS_TOPIC_ARN or SNS_TOPIC_ARN_TEMPLATE is required")
        import boto3  # lazy import: only required when SNS delivery is enabled

        self._sns = boto3.client("sns", region_name=region)
        self._topic_arn = topic_arn
        self._topic_arn_template = topic_arn_template

    def _topic_for(self, user_id: str) -> str:
        if self._topic_arn_template:
            return self._topic_arn_template.format(user_id=user_id)
        return self._topic_arn

    def publish(self, notification: Notification) -> None:
        self._sns.publish(
            TopicArn=self._topic_for(notification.user_id),
            Subject=f"Pacific BioArchive: {notification.species} sighting",
            Message=json.dumps(
                {
                    "notification_id": notification.notification_id,
                    "user_id": notification.user_id,
                    "file_id": notification.file_id,
                    "species": notification.species,
                    "object_key": notification.object_key,
                    "created_at": notification.created_at.isoformat(),
                }
            ),
            MessageAttributes={
                "user_id": {
                    "DataType": "String",
                    "StringValue": notification.user_id,
                },
                "species": {
                    "DataType": "String",
                    "StringValue": notification.species,
                },
            },
        )
