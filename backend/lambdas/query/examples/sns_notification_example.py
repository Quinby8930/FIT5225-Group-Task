"""Member E — how to implement the `NotificationPublisher` for delivery.

Member D's notification trigger calls `NotificationPublisher.publish(notification)`
after writing a durable notification record. This example shows the real SNS
delivery path: each subscribed user has an SNS topic (or endpoint) that receives
a message when a file matching their subscription completes. Replace the
per-user topic lookup with whatever delivery channel Member E chooses
(SNS / email / push / an in-app inbox).

Wire it in `app/main.py`:

    from examples.sns_notification_example import SNSNotificationPublisher
    publisher: NotificationPublisher = SNSNotificationPublisher()
"""

from __future__ import annotations

import json

from app.notification_client import NotificationPublisher
from app.schemas import Notification


class SNSNotificationPublisher(NotificationPublisher):
    def __init__(self, region: str = "ap-southeast-2") -> None:
        import boto3  # lazy import: only needed on AWS

        self._sns = boto3.client("sns", region_name=region)

    def _topic_for(self, user_id: str) -> str:
        # TODO (Member E): resolve the user's subscription topic / endpoint ARN.
        # For a fixed per-user topic this is simply f"arn:aws:sns:{region}:{acct}:{user_id}".
        return f"arn:aws:sns:{self._sns.meta.region_name}:000000000000:bioarchive-{user_id}"

    def publish(self, notification: Notification) -> None:
        self._sns.publish(
            TopicArn=self._topic_for(notification.user_id),
            Subject=f"New {notification.species} sighting",
            Message=json.dumps(
                {
                    "file_id": notification.file_id,
                    "species": notification.species,
                    "object_key": notification.object_key,
                    "created_at": notification.created_at.isoformat(),
                }
            ),
        )
