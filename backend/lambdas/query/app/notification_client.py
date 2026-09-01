"""Notification delivery abstraction — the *trigger*'s outbound hook.

When a completed file's tags match a subscription, the notification trigger
writes a durable :class:`Notification` record and then hands it to a
``NotificationPublisher`` to actually reach the user. Member D owns the trigger,
durable inbox, and included SNS implementation; Member E owns the frontend and
in-app notification experience. The stub below is only for local development
and unit tests.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from hashlib import sha256
import json
import re

from app.schemas import Notification

logger = logging.getLogger(__name__)

_USER_TOPIC_PREFIX = "pba-user-"
_TOPIC_ARN_PREFIX_PATTERN = re.compile(
    r"^arn:(?:aws|aws-cn|aws-us-gov):sns:([a-z0-9-]+):(\d{12}):pba-user-$"
)


class EmailSubscriptionError(RuntimeError):
    """Safe base error for the course-demo SNS subscription path."""


class EmailSubscriptionConflict(EmailSubscriptionError):
    """The deterministic user topic already targets another email."""


class EmailSubscriptionSetupError(EmailSubscriptionError):
    """SNS provisioning failed without exposing an email endpoint."""


class UserTopicResolver:
    """Resolve Cognito subjects to deterministic, injection-safe SNS topics."""

    def __init__(self, topic_arn_prefix: str) -> None:
        match = _TOPIC_ARN_PREFIX_PATTERN.fullmatch(topic_arn_prefix)
        if match is None:
            raise ValueError("SNS_USER_TOPIC_ARN_PREFIX is invalid")
        self._topic_arn_prefix = topic_arn_prefix
        self.region = match.group(1)

    def topic_name(self, user_id: str) -> str:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("authenticated user id is required")
        digest = sha256(user_id.encode("utf-8")).hexdigest()
        return f"{_USER_TOPIC_PREFIX}{digest}"

    def topic_arn(self, user_id: str) -> str:
        digest = self.topic_name(user_id).removeprefix(_USER_TOPIC_PREFIX)
        return f"{self._topic_arn_prefix}{digest}"


class SNSUserSubscriptionProvisioner:
    """Create/reuse one SNS email subscription for a Cognito user.

    This is deliberately the assignment-demo flow: it is serial and
    retry-friendly, but it does not implement a cross-service transaction or
    production-grade coordination for concurrent first subscriptions.
    """

    def __init__(self, *, region: str, resolver: UserTopicResolver) -> None:
        if resolver.region != region:
            raise ValueError("SNS user topic prefix region does not match AWS_REGION")
        import boto3  # lazy import: only required when SNS delivery is enabled

        self._sns = boto3.client("sns", region_name=region)
        self._resolver = resolver

    def ensure_subscription(self, user_id: str, email: str) -> None:
        expected_topic_arn = self._resolver.topic_arn(user_id)
        try:
            created = self._sns.create_topic(
                Name=self._resolver.topic_name(user_id)
            )
            if created.get("TopicArn") != expected_topic_arn:
                raise EmailSubscriptionSetupError(
                    "email notification setup failed"
                )

            matching_email = False
            different_email = False
            next_token: str | None = None
            seen_tokens: set[str] = set()
            while True:
                request = {"TopicArn": expected_topic_arn}
                if next_token is not None:
                    request["NextToken"] = next_token
                page = self._sns.list_subscriptions_by_topic(**request)
                subscriptions = page.get("Subscriptions")
                if not isinstance(subscriptions, list):
                    raise EmailSubscriptionSetupError(
                        "email notification setup failed"
                    )
                for subscription in subscriptions:
                    if subscription.get("Protocol") != "email":
                        continue
                    if subscription.get("Endpoint") == email:
                        matching_email = True
                    else:
                        different_email = True

                next_value = page.get("NextToken")
                if not next_value:
                    break
                if not isinstance(next_value, str) or next_value in seen_tokens:
                    raise EmailSubscriptionSetupError(
                        "email notification setup failed"
                    )
                seen_tokens.add(next_value)
                next_token = next_value

            if different_email:
                raise EmailSubscriptionConflict(
                    "user topic already has a different email subscription"
                )
            if matching_email:
                return
            self._sns.subscribe(
                TopicArn=expected_topic_arn,
                Protocol="email",
                Endpoint=email,
                ReturnSubscriptionArn=True,
            )
        except EmailSubscriptionError:
            raise
        except Exception:
            raise EmailSubscriptionSetupError(
                "email notification setup failed"
            ) from None


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
        resolver: UserTopicResolver | None = None,
    ) -> None:
        if not topic_arn and not topic_arn_template and resolver is None:
            raise ValueError(
                "SNS_TOPIC_ARN, SNS_TOPIC_ARN_TEMPLATE, or a resolver is required"
            )
        import boto3  # lazy import: only required when SNS delivery is enabled

        self._sns = boto3.client("sns", region_name=region)
        self._topic_arn = topic_arn
        self._topic_arn_template = topic_arn_template
        self._resolver = resolver

    def _topic_for(self, user_id: str) -> str:
        if self._resolver is not None:
            return self._resolver.topic_arn(user_id)
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


class PerUserSNSNotificationPublisher(SNSNotificationPublisher):
    """SNS publisher that can only resolve the authenticated user's topic."""

    def __init__(self, *, region: str, resolver: UserTopicResolver) -> None:
        super().__init__(region=region, resolver=resolver)
