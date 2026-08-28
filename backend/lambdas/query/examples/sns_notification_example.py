"""Legacy per-user-topic example for the `NotificationPublisher` interface.

Production already uses Member D's ``app.notification_client.SNSNotificationPublisher``
with the SAM-created topic. This file is retained only to illustrate a per-user
topic variant; Member E owns the frontend/in-app notification experience and
does not need to implement SNS publishing.
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
        # Illustrative only: a per-user design would resolve a topic ARN here.
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
