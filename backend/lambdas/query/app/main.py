"""FastAPI application — the database, query & data-management API (Member D).

Endpoints
---------
Public (Member E frontend, Cognito-protected):
  POST /query/by-tags        find files by tags with minimum counts (AND)
  POST /query/by-species     find files containing a species
  GET  /query/by-thumbnail   map a thumbnail key -> full-size object key
  POST /query/by-file        detect tags on an uploaded file, return matches
  POST /tags/edit            bulk add/remove tags (operation 1=add, 0=remove)
  POST /files/delete         bulk delete (database + storage)
  POST /notifications/subscribe           subscribe a user to a species
  DELETE /notifications/subscribe         unsubscribe a user from a species
  GET  /notifications/subscriptions       list a user's subscriptions
  GET  /notifications                     list a user's notifications

Internal (Member B, see docs/member-b/api-contracts.md):
  POST /internal/uploads/reserve             reserve a unique (user_id, checksum)
  POST /internal/files/{file_id}/processing  acquire the processing lease
  PUT  /internal/files/{file_id}/complete    record a completed run (idempotent)
  PUT  /internal/files/{file_id}/failed      record a bounded failure (idempotent)

The notification trigger lives inside `complete`: when a file finishes
processing, its tags are matched against every subscription and a notification
is written for each subscribed user.

Authentication: Member A owns Cognito. Public routes use `get_current_user`,
which reads the verified Cognito `sub` claim from API Gateway in Lambda mode
and can verify a bearer token locally when the optional local auth dependency
is installed.
"""

from __future__ import annotations

import hmac
import logging
from datetime import timedelta

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import Settings, settings
from app.repository import (
    DynamoDBNotificationRepository,
    NotificationRepository,
    SQLiteNotificationRepository,
    SQLiteRepository,
)
from app.repository.base import DuplicateError, FileRepository
from app.schemas import (
    CompleteRequest,
    DeleteRequest,
    FailedRequest,
    FileRecord,
    NotificationListResponse,
    ProcessingRequest,
    QueryResponse,
    ReserveRequest,
    SpeciesQueryRequest,
    SubscribeRequest,
    SubscriptionListResponse,
    TagEditRequest,
    TagQueryRequest,
    utcnow,
)
from app.notification_client import (
    NotificationPublisher,
    SNSNotificationPublisher,
    StubNotificationPublisher,
)
from app.services.notification_service import build_notifications
from app.services.query_service import (
    filter_by_min_counts,
    filter_by_species,
    to_display_keys,
)
from app.storage_client import (
    LambdaStorageClient,
    StorageClient,
    StorageClientError,
    StorageClientUnavailable,
    StubStorageClient,
    UnavailableStorageClient,
)
from app.species import get_mapper
from app.tag_detector import (
    RemoteTagDetector,
    StubTagDetector,
    TagDetectionError,
    TagDetectionUnavailable,
    TagDetector,
    UnavailableTagDetector,
)
from examples.cognito_auth_example import build_get_current_user

app = FastAPI(title="Pacific BioArchive — Database & Query API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Processing lease window matches Member B's media-processing Lambda timeout.
LEASE_SECONDS = 900
FAILED_MESSAGE_MAX_CHARS = 240
MAX_QUERY_IMAGE_BYTES = 4_194_304
QUERY_IMAGE_CONTENT_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)
FORBIDDEN_OWNER_DETAIL = {
    "code": "FORBIDDEN_OWNER",
    "message": "media is not owned by the authenticated user",
}
METADATA_CONFLICT_DETAIL = {
    "code": "METADATA_CONFLICT",
    "message": "request metadata does not match the reserved file",
}
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dependency wiring. Swap `SQLiteRepository` for `DynamoDBRepository` to deploy.
# ---------------------------------------------------------------------------
def _build_repository() -> FileRepository:
    if settings.repository_backend == "dynamodb":
        from app.repository.dynamodb_repo import DynamoDBRepository

        return DynamoDBRepository(
            settings.dynamodb_table,
            settings.aws_region,
            settings.reservations_table,
        )
    return SQLiteRepository(settings.sqlite_path)


def _build_notification_repository() -> NotificationRepository:
    if settings.repository_backend == "dynamodb":
        return DynamoDBNotificationRepository(
            subscriptions_table=settings.subscriptions_table,
            notifications_table=settings.notifications_table,
            region=settings.aws_region,
        )
    return SQLiteNotificationRepository(settings.sqlite_path)


def _build_storage(settings_: Settings = settings) -> StorageClient:
    backend = settings_.storage_backend.strip().lower()
    if not backend:
        return UnavailableStorageClient()
    if backend == "stub":
        return StubStorageClient()
    if backend == "lambda":
        if not settings_.storage_delete_function_name.strip():
            raise RuntimeError(
                "STORAGE_DELETE_FUNCTION_NAME function name is required for lambda"
            )
        return LambdaStorageClient(settings_.storage_delete_function_name)
    raise RuntimeError("STORAGE_BACKEND must be either 'stub' or 'lambda'")


def _build_detector(settings_: Settings = settings) -> TagDetector:
    backend = settings_.tag_detector_backend.strip().lower()
    if backend == "stub":
        return StubTagDetector()
    if backend != "remote":
        return UnavailableTagDetector()
    if not (
        settings_.query_input_bucket.strip()
        and settings_.inference_api_url.strip()
        and settings_.internal_api_key
    ):
        return UnavailableTagDetector()
    try:
        return RemoteTagDetector(
            bucket_name=settings_.query_input_bucket,
            inference_api_url=settings_.inference_api_url,
            internal_api_key=settings_.internal_api_key,
        )
    except ValueError:
        return UnavailableTagDetector()


repo: FileRepository = _build_repository()
notif_repo: NotificationRepository = _build_notification_repository()
detector: TagDetector = _build_detector()
storage: StorageClient = _build_storage()


def _build_publisher() -> NotificationPublisher:
    if settings.notification_publisher == "sns":
        return SNSNotificationPublisher(
            region=settings.aws_region,
            topic_arn=settings.sns_topic_arn,
            topic_arn_template=settings.sns_topic_arn_template,
        )
    return StubNotificationPublisher()


publisher: NotificationPublisher = _build_publisher()


def get_repo() -> FileRepository:
    return repo


def get_notification_repo() -> NotificationRepository:
    return notif_repo


def get_detector() -> TagDetector:
    return detector


def get_storage() -> StorageClient:
    return storage


def get_publisher() -> NotificationPublisher:
    return publisher


def get_settings() -> Settings:
    return settings


def require_internal_api_key(
    x_internal_api_key: str | None = Header(
        default=None, alias="X-Internal-Api-Key"
    ),
    settings_: Settings = Depends(get_settings),
) -> None:
    if not settings_.internal_api_key:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "INTERNAL_AUTH_NOT_CONFIGURED",
                "message": "internal API key is not configured",
            },
        )
    if x_internal_api_key is None or not hmac.compare_digest(
        x_internal_api_key, settings_.internal_api_key
    ):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_INTERNAL_API_KEY",
                "message": "invalid internal API key",
            },
        )


get_current_user = build_get_current_user()


def _normalise_species(species: str) -> str:
    return get_mapper().common_name(species)


def _normalise_tags(tags: dict[str, int]) -> dict[str, int]:
    normalised: dict[str, int] = {}
    for species, count in tags.items():
        short_name = _normalise_species(species)
        normalised[short_name] = normalised.get(short_name, 0) + count
    return normalised


def _normalise_detections(detections: list[dict]) -> list[dict]:
    return [
        {
            **detection,
            "species": _normalise_species(detection["species"]),
        }
        if isinstance(detection.get("species"), str)
        else dict(detection)
        for detection in detections
    ]


def _require_matching_metadata(
    record: FileRecord,
    *,
    user_id: str,
    object_key: str | None = None,
    file_type: str | None = None,
) -> None:
    if (
        record.user_id != user_id
        or (object_key is not None and record.object_key != object_key)
        or (file_type is not None and record.file_type != file_type)
    ):
        raise HTTPException(status_code=409, detail=METADATA_CONFLICT_DETAIL)


def _publish_pending_notifications(
    file_id: str,
    notif_repo_: NotificationRepository,
    publisher_: NotificationPublisher,
) -> None:
    for notification in notif_repo_.pending_for_file(file_id):
        try:
            publisher_.publish(notification)
        except Exception:
            logger.exception(
                "notification publish failed; delivery remains pending",
                extra={
                    "notification_id": notification.notification_id,
                    "file_id": notification.file_id,
                },
            )
            continue
        try:
            notif_repo_.mark_delivered(notification)
        except Exception:
            logger.exception(
                "notification delivery state update failed; delivery remains pending",
                extra={
                    "notification_id": notification.notification_id,
                    "file_id": notification.file_id,
                },
            )


def _ensure_notification_inbox(
    file_id: str,
    object_key: str,
    tags: dict[str, int],
    notif_repo_: NotificationRepository,
) -> None:
    notifications = build_notifications(
        file_id, object_key, tags, notif_repo_.subscribers_for_species
    )
    for notification in notifications:
        notif_repo_.add_notification(notification)


# ---------------------------------------------------------------------------
# Authentication smoke test
# ---------------------------------------------------------------------------
@app.get("/auth-test")
def auth_test(user_id: str = Depends(get_current_user)) -> dict:
    return {"authenticated": True, "user_id": user_id}


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
@app.post("/query/by-tags", response_model=QueryResponse)
def query_by_tags(
    body: TagQueryRequest,
    repo_: FileRepository = Depends(get_repo),
    _user: str = Depends(get_current_user),
) -> QueryResponse:
    records = filter_by_min_counts(repo_.all(), body.tags)
    keys = to_display_keys(records)
    return QueryResponse(results=keys, count=len(keys))


@app.post("/query/by-species", response_model=QueryResponse)
def query_by_species(
    body: SpeciesQueryRequest,
    repo_: FileRepository = Depends(get_repo),
    _user: str = Depends(get_current_user),
) -> QueryResponse:
    records = filter_by_species(repo_.all(), body.species)
    keys = to_display_keys(records)
    return QueryResponse(results=keys, count=len(keys))


@app.get("/query/by-thumbnail")
def query_by_thumbnail(
    key: str,
    repo_: FileRepository = Depends(get_repo),
    _user: str = Depends(get_current_user),
) -> dict:
    record = repo_.by_thumbnail_key(key)
    if record is None:
        raise HTTPException(status_code=404, detail="thumbnail key not found")
    return {"original_key": record.object_key, "file_id": record.file_id}


@app.post("/query/by-file", response_model=QueryResponse)
async def query_by_file(
    file: UploadFile,
    repo_: FileRepository = Depends(get_repo),
    detector_: TagDetector = Depends(get_detector),
    _user: str = Depends(get_current_user),
) -> QueryResponse:
    content_type = (file.content_type or "").casefold()
    if content_type not in QUERY_IMAGE_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="unsupported query image type")
    # The extra byte distinguishes an exact-limit image from an oversized one
    # without allowing an unbounded in-memory read.
    content = await file.read(MAX_QUERY_IMAGE_BYTES + 1)
    if len(content) > MAX_QUERY_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="query image exceeds size limit")
    try:
        tags = detector_.detect(
            user_id=_user,
            file_name=file.filename or "upload",
            content_type=content_type,
            content=content,
        )
    except TagDetectionUnavailable as exc:
        raise HTTPException(status_code=503, detail="tag detector unavailable") from exc
    except TagDetectionError as exc:
        raise HTTPException(status_code=502, detail="tag detection failed") from exc
    tags = _normalise_tags(tags)
    if not tags:
        return QueryResponse(results=[], count=0)
    records = filter_by_min_counts(repo_.all(), tags)
    keys = to_display_keys(records)
    return QueryResponse(results=keys, count=len(keys))


# ---------------------------------------------------------------------------
# Data management
# ---------------------------------------------------------------------------
@app.post("/tags/edit")
def edit_tags(
    body: TagEditRequest,
    repo_: FileRepository = Depends(get_repo),
    _user: str = Depends(get_current_user),
) -> dict:
    records = repo_.by_keys(body.keys)
    if any(record.user_id != _user for record in records):
        raise HTTPException(status_code=403, detail=FORBIDDEN_OWNER_DETAIL)
    normalised_tags = [_normalise_species(tag) for tag in body.tags]
    updated = 0
    for record in records:
        tags = dict(record.tags)
        for tag in normalised_tags:
            if body.operation == 1:  # add
                tags[tag] = tags.get(tag, 0) + 1
            else:  # remove — ignore tags that aren't present (spec requirement)
                tags.pop(tag, None)
        repo_.update_tags(record.file_id, tags)
        updated += 1
    return {"updated": updated, "matched_keys": [r.object_key for r in records]}


@app.post("/files/delete")
def delete_files(
    body: DeleteRequest,
    repo_: FileRepository = Depends(get_repo),
    storage_: StorageClient = Depends(get_storage),
    _user: str = Depends(get_current_user),
) -> dict:
    records = repo_.by_keys(body.keys)
    if any(record.user_id != _user for record in records):
        raise HTTPException(status_code=403, detail=FORBIDDEN_OWNER_DETAIL)
    keys_to_delete: list[str] = []
    for record in records:
        keys = [record.object_key] + (
            [record.thumbnail_key] if record.thumbnail_key else []
        )
        keys_to_delete.extend(keys)
    # Delete storage first so a storage failure cannot leave orphaned objects
    # after their metadata has already disappeared.
    if keys_to_delete:
        try:
            storage_.delete(_user, keys_to_delete)
        except StorageClientUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="storage deletion unavailable"
            ) from exc
        except StorageClientError as exc:
            raise HTTPException(
                status_code=502, detail="storage deletion failed"
            ) from exc
    removed_db = repo_.delete_by_ids([record.file_id for record in records])
    return {
        "deleted_db_records": removed_db,
        "storage_objects_removed": len(keys_to_delete),
    }


# ---------------------------------------------------------------------------
# Subscriptions & notifications (Member E frontend calls these)
# ---------------------------------------------------------------------------
@app.post("/notifications/subscribe", status_code=201)
def subscribe(
    body: SubscribeRequest,
    notif_repo_: NotificationRepository = Depends(get_notification_repo),
    _user: str = Depends(get_current_user),
) -> dict:
    species = _normalise_species(body.species)
    notif_repo_.subscribe(_user, species)
    return {"user_id": _user, "species": species, "subscribed": True}


@app.delete("/notifications/subscribe")
def unsubscribe(
    species: str,
    notif_repo_: NotificationRepository = Depends(get_notification_repo),
    _user: str = Depends(get_current_user),
) -> dict:
    species = _normalise_species(species)
    notif_repo_.unsubscribe(_user, species)
    return {"user_id": _user, "species": species, "subscribed": False}


@app.get("/notifications/subscriptions", response_model=SubscriptionListResponse)
def list_subscriptions(
    notif_repo_: NotificationRepository = Depends(get_notification_repo),
    _user: str = Depends(get_current_user),
) -> SubscriptionListResponse:
    species = notif_repo_.subscriptions(_user)
    return SubscriptionListResponse(species=species, count=len(species))


@app.get("/notifications", response_model=NotificationListResponse)
def list_notifications(
    notif_repo_: NotificationRepository = Depends(get_notification_repo),
    _user: str = Depends(get_current_user),
) -> NotificationListResponse:
    notifications = notif_repo_.notifications(_user)
    return NotificationListResponse(notifications=notifications, count=len(notifications))


# ---------------------------------------------------------------------------
# Internal metadata state machine (Member B -> Member D)
# ---------------------------------------------------------------------------
@app.post("/internal/uploads/reserve", status_code=201)
def reserve_upload(
    body: ReserveRequest,
    repo_: FileRepository = Depends(get_repo),
    _internal_auth: None = Depends(require_internal_api_key),
) -> JSONResponse:
    candidate = FileRecord(
        file_id=body.file_id,
        user_id=body.user_id,
        checksum=body.checksum,
        filename=body.filename,
        file_type=body.file_type,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        object_key=body.object_key,
        status="pending_upload",
    )
    try:
        reserved, created = repo_.reserve(candidate)
    except DuplicateError as exc:
        return JSONResponse(
            status_code=409, content={"existing_file_id": exc.existing_file_id}
        )
    if not created:
        if (
            reserved.filename != body.filename
            or reserved.file_type != body.file_type
            or reserved.content_type != body.content_type
            or reserved.size_bytes != body.size_bytes
        ):
            raise HTTPException(status_code=409, detail=METADATA_CONFLICT_DETAIL)
        reused = repo_.reuse_upload(reserved.file_id)
        if reused is None:
            current = repo_.get(reserved.file_id)
            return JSONResponse(
                status_code=409,
                content={
                    "existing_file_id": current.file_id if current else reserved.file_id
                },
            )
        reserved = reused
    return JSONResponse(
        status_code=201,
        content={
            "file_id": reserved.file_id,
            "object_key": reserved.object_key,
            "status": "pending_upload",
            "reused": not created,
        },
    )


@app.post("/internal/files/{file_id}/processing")
def acquire_processing(
    file_id: str,
    body: ProcessingRequest,
    repo_: FileRepository = Depends(get_repo),
    _internal_auth: None = Depends(require_internal_api_key),
) -> dict:
    record = repo_.get(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="file not found")
    _require_matching_metadata(
        record, user_id=body.user_id, object_key=body.object_key
    )
    now = utcnow()
    state = repo_.try_acquire_processing(
        file_id,
        body.sequencer,
        now,
        now + timedelta(seconds=LEASE_SECONDS),
    )
    return {"should_process": state == "acquired", "state": state}


@app.put("/internal/files/{file_id}/complete")
def complete_processing(
    file_id: str,
    body: CompleteRequest,
    repo_: FileRepository = Depends(get_repo),
    notif_repo_: NotificationRepository = Depends(get_notification_repo),
    publisher_: NotificationPublisher = Depends(get_publisher),
    _internal_auth: None = Depends(require_internal_api_key),
) -> dict:
    record = repo_.get(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="file not found")
    _require_matching_metadata(
        record,
        user_id=body.user_id,
        object_key=body.original_key,
        file_type=body.file_type,
    )
    if record.status == "completed":
        _publish_pending_notifications(file_id, notif_repo_, publisher_)
        return {}
    tags = _normalise_tags(body.tags)
    detections = _normalise_detections(body.detections)
    # Ensure the durable inbox before the completed transition. If completion
    # persistence fails, a retry rebuilds the same deterministic notification IDs.
    _ensure_notification_inbox(file_id, body.original_key, tags, notif_repo_)
    repo_.mark_completed(
        file_id,
        body.original_key,
        body.thumbnail_key,
        body.file_type,
        tags,
        detections,
        body.model_version,
    )
    _publish_pending_notifications(file_id, notif_repo_, publisher_)
    return {}


@app.put("/internal/files/{file_id}/failed")
def fail_processing(
    file_id: str,
    body: FailedRequest,
    repo_: FileRepository = Depends(get_repo),
    _internal_auth: None = Depends(require_internal_api_key),
) -> dict:
    record = repo_.get(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="file not found")
    _require_matching_metadata(record, user_id=body.user_id)
    if record.status == "completed":
        return {}  # never downgrade a completed file
    repo_.mark_failed(file_id, body.error_code, body.message[:FAILED_MESSAGE_MAX_CHARS])
    return {}
