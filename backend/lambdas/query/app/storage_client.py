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
    def delete(self, keys: list[str]) -> None:
        """Delete the given objects (originals + thumbnails) from storage."""


class StubStorageClient(StorageClient):
    def delete(self, keys: list[str]) -> None:
        # Real impl: Member B's guarded storage-delete Lambda (second cloud).
        logger.info("[stub] storage delete requested for %d object(s)", len(keys))
