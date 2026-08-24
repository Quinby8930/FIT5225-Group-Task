"""Member B — how to implement the `StorageClient` for storage deletion.

Member D's delete endpoint calls `StorageClient.delete(user_id, keys)` to remove
objects from storage after the DB records are gone. The keys are already S3
object keys (`originals/...`, `thumbnails/...`), so no URL parsing is needed.

In the deployed system this should invoke Member B's guarded storage-delete
Lambda (payload `{"user_id": ..., "keys": [...]}`, see
`docs/member-b/api-contracts.md`), which enforces per-user key-prefix ownership.
The direct S3 `delete_objects` path below is a local/dev stand-in — copy the
Lambda-invocation variant for production.
"""

from __future__ import annotations

from app.storage_client import StorageClient


class S3StorageClient(StorageClient):
    def __init__(self, bucket: str = "pacific-bioarchive", region: str = "ap-southeast-2"):
        import boto3  # lazy import: only needed on AWS

        self._bucket = bucket
        self._client = boto3.client("s3", region_name=region)

    def delete(self, user_id: str, keys: list[str]) -> None:
        objects = [{"Key": k} for k in keys if k]
        if not objects:
            return
        # S3 supports deleting up to 1000 keys per call. In production the
        # guarded Lambda enforces that each key sits under {user_id}'s prefix;
        # this stand-in trusts the caller (Member D groups keys by owner).
        self._client.delete_objects(
            Bucket=self._bucket,
            Delete={"Objects": objects, "Quiet": True},
        )
