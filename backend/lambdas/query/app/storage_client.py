"""Storage client abstraction for cross-module delete coordination.

Deleting a file must remove *both* the database record (Member D) and the
cloud storage objects (Member B). Rather than hard-coding S3 here, we define
an interface and a stub; Member B provides the real guarded-delete Lambda
(invoked with ``{"user_id": ..., "keys": [...]}``, see
`docs/member-b/api-contracts.md`). The delete endpoint calls both, so nothing
is orphaned on either side.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 1024 * 1024


class StorageClientError(RuntimeError):
    """Member B's guarded deletion could not be confirmed."""


class StorageClientUnavailable(StorageClientError):
    """The production storage adapter is not configured."""


class StorageClient(ABC):
    @abstractmethod
    def delete(self, user_id: str, keys: list[str]) -> None:
        """Delete the given objects (originals + thumbnails) owned by `user_id`.

        `user_id` is required because Member B's guarded storage-delete Lambda
        enforces that every key sits under ``originals/{user_id}/``,
        ``thumbnails/{user_id}/`` or ``processing/{user_id}/`` (see
        `docs/member-b/api-contracts.md`). Grouping deletions by owner keeps that
        ownership check correct when a bulk delete spans multiple users.
        """


class StubStorageClient(StorageClient):
    def delete(self, user_id: str, keys: list[str]) -> None:
        # Real impl: Member B's guarded storage-delete Lambda (second cloud).
        logger.info(
            "[stub] storage delete requested for %d object(s) owned by %s",
            len(keys),
            user_id,
        )


class LambdaStorageClient(StorageClient):
    """Synchronously invoke Member B's guarded storage-delete Lambda."""

    def __init__(self, function_name: str, *, lambda_client=None) -> None:
        if not function_name or not function_name.strip():
            raise ValueError("storage delete function name must not be empty")
        if lambda_client is None:
            import boto3

            lambda_client = boto3.client("lambda")
        self._function_name = function_name
        self._client = lambda_client

    def delete(self, user_id: str, keys: list[str]) -> None:
        response = self._client.invoke(
            FunctionName=self._function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps({"user_id": user_id, "keys": keys}).encode("utf-8"),
        )
        if not isinstance(response, dict) or response.get("StatusCode") != 200:
            status = response.get("StatusCode") if isinstance(response, dict) else None
            raise StorageClientError(f"storage Lambda invocation status was {status}")
        if response.get("FunctionError"):
            raise StorageClientError("storage Lambda returned a function error")

        payload_stream = response.get("Payload")
        if payload_stream is None or not callable(getattr(payload_stream, "read", None)):
            raise StorageClientError("storage Lambda response was malformed")
        payload = payload_stream.read(MAX_RESPONSE_BYTES + 1)
        if not isinstance(payload, bytes):
            raise StorageClientError("storage Lambda response was malformed")
        if len(payload) > MAX_RESPONSE_BYTES:
            raise StorageClientError("storage Lambda response exceeded the size limit")

        try:
            envelope = json.loads(payload.decode("utf-8"))
            nested_status = envelope["statusCode"]
            body = json.loads(envelope["body"])
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageClientError("storage Lambda response was malformed") from exc
        if type(nested_status) is not int:
            raise StorageClientError("storage Lambda response was malformed")
        if not 200 <= nested_status < 300:
            raise StorageClientError(
                f"storage Lambda returned nested status {nested_status}"
            )
        if not (
            isinstance(body, dict)
            and type(body.get("deleted_count")) is int
            and body["deleted_count"] == len(keys)
        ):
            raise StorageClientError("storage Lambda response was malformed")


class UnavailableStorageClient(StorageClient):
    """Fail closed instead of pretending that storage objects were deleted."""

    def delete(self, user_id: str, keys: list[str]) -> None:
        raise StorageClientUnavailable("storage deletion is not configured")
