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

Internal (Member B, see docs/member-b/api-contracts.md):
  POST /internal/uploads/reserve             reserve a unique (user_id, checksum)
  POST /internal/files/{file_id}/processing  acquire the processing lease
  PUT  /internal/files/{file_id}/complete    record a completed run (idempotent)
  PUT  /internal/files/{file_id}/failed      record a bounded failure (idempotent)

Authentication: Member A owns Cognito. Wire the token verification into
`get_current_user` (currently a placeholder) — every public route already
depends on it, so nothing else changes once Cognito is connected.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.repository import SQLiteRepository
from app.repository.base import DuplicateError, FileRepository
from app.schemas import (
    CompleteRequest,
    DeleteRequest,
    FailedRequest,
    FileRecord,
    ProcessingRequest,
    QueryResponse,
    ReserveRequest,
    SpeciesQueryRequest,
    TagEditRequest,
    TagQueryRequest,
    utcnow,
)
from app.services.query_service import (
    filter_by_min_counts,
    filter_by_species,
    to_display_keys,
)
from app.storage_client import StorageClient, StubStorageClient
from app.tag_detector import StubTagDetector, TagDetector

app = FastAPI(title="Pacific BioArchive — Database & Query API", version="1.0.0")

# Processing lease window matches Member B's media-processing Lambda timeout.
LEASE_SECONDS = 900
FAILED_MESSAGE_MAX_CHARS = 240


# ---------------------------------------------------------------------------
# Dependency wiring. Swap `SQLiteRepository` for `DynamoDBRepository` to deploy.
# ---------------------------------------------------------------------------
def _build_repository() -> FileRepository:
    if settings.repository_backend == "dynamodb":
        from app.repository.dynamodb_repo import DynamoDBRepository

        return DynamoDBRepository(settings.dynamodb_table, settings.aws_region)
    return SQLiteRepository(settings.sqlite_path)


repo: FileRepository = _build_repository()
detector: TagDetector = StubTagDetector()
storage: StorageClient = StubStorageClient()


def get_repo() -> FileRepository:
    return repo


def get_detector() -> TagDetector:
    return detector


def get_storage() -> StorageClient:
    return storage


# Placeholder — replace with Cognito token verification (Member A).
def get_current_user() -> str:
    return "demo-user"


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
    # Read into memory; the query file is NOT persisted to the repository.
    content = await file.read()
    tags = detector_.detect(file.filename or "upload", content)
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
    updated = 0
    for record in records:
        tags = dict(record.tags)
        for tag in body.tags:
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
    file_ids = [r.file_id for r in records]
    # Remove both sides: database entries AND storage objects.
    removed_db = repo_.delete_by_ids(file_ids)
    all_keys = [r.object_key for r in records] + [
        r.thumbnail_key for r in records if r.thumbnail_key
    ]
    storage_.delete(all_keys)
    return {"deleted_db_records": removed_db, "storage_objects_removed": len(all_keys)}


# ---------------------------------------------------------------------------
# Internal metadata state machine (Member B -> Member D)
# ---------------------------------------------------------------------------
@app.post("/internal/uploads/reserve", status_code=201)
def reserve_upload(
    body: ReserveRequest,
    repo_: FileRepository = Depends(get_repo),
) -> JSONResponse:
    existing = repo_.find_by_user_checksum(body.user_id, body.checksum)
    if existing is not None:
        return JSONResponse(
            status_code=409, content={"existing_file_id": existing.file_id}
        )
    try:
        repo_.add(
            FileRecord(
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
        )
    except DuplicateError as exc:
        return JSONResponse(
            status_code=409, content={"existing_file_id": exc.existing_file_id}
        )
    return JSONResponse(
        status_code=201, content={"file_id": body.file_id, "status": "pending_upload"}
    )


@app.post("/internal/files/{file_id}/processing")
def acquire_processing(
    file_id: str,
    body: ProcessingRequest,
    repo_: FileRepository = Depends(get_repo),
) -> dict:
    record = repo_.get(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="file not found")
    if record.status == "completed":
        return {"should_process": False}
    now = utcnow()
    if (
        record.status == "processing"
        and record.lease_expires_at is not None
        and record.lease_expires_at > now
    ):
        return {"should_process": False}  # active lease: don't grant twice
    repo_.mark_processing(file_id, body.sequencer, now + timedelta(seconds=LEASE_SECONDS))
    return {"should_process": True}


@app.put("/internal/files/{file_id}/complete")
def complete_processing(
    file_id: str,
    body: CompleteRequest,
    repo_: FileRepository = Depends(get_repo),
) -> dict:
    record = repo_.get(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="file not found")
    if record.status == "completed":
        return {}  # idempotent: replaying completion is a no-op
    repo_.mark_completed(
        file_id,
        body.original_key,
        body.thumbnail_key,
        body.file_type,
        body.tags,
        body.detections,
        body.model_version,
    )
    return {}


@app.put("/internal/files/{file_id}/failed")
def fail_processing(
    file_id: str,
    body: FailedRequest,
    repo_: FileRepository = Depends(get_repo),
) -> dict:
    record = repo_.get(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="file not found")
    if record.status == "completed":
        return {}  # never downgrade a completed file
    repo_.mark_failed(file_id, body.error_code, body.message[:FAILED_MESSAGE_MAX_CHARS])
    return {}
