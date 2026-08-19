"""Storage client abstraction for cross-module delete coordination.

Deleting a file must remove *both* the database record (Member D) and the
cloud storage objects (Member B). Rather than hard-coding S3 here, we define
an interface and a stub; Member B provides the real guarded-delete Lambda
(invoked with ``{"user_id": ..., "keys": [...]}``, see
`docs/member-b/api-contracts.md`). The delete endpoint calls both, so nothing
is orphaned on either side.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


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
