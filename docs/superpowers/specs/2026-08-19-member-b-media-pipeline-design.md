# Member B Media Ingestion Pipeline Design

## Purpose

Build the AWS-owned ingestion boundary for Pacific BioArchive. An authenticated
user requests a short-lived upload URL, uploads an image or video directly to a
private S3 bucket, and an S3 event starts media preprocessing. The preprocessing
result is sent through stable HTTP contracts to Member C's inference service and
Member D's metadata service, so neither service's hosting provider or database
choice is coupled to Member B's code.

## Scope

Member B owns:

- a protected `POST /upload-url` Lambda integration;
- SHA-256 validation and per-user duplicate reservation;
- private S3 object naming and pre-signed PUT generation;
- S3 `ObjectCreated` processing for the `originals/` prefix;
- aspect-ratio-preserving compressed image thumbnails;
- video extraction at exactly one frame per second;
- temporary-frame upload, signed access, and cleanup;
- orchestration of inference and metadata status updates;
- idempotent storage deletion for original, thumbnail, and processing objects;
- AWS SAM infrastructure definitions, automated tests, and handoff documents.

Member B does not own Cognito setup, model internals, the metadata database,
query/tag APIs, the user interface, or notifications.

## Architecture

```text
Authenticated browser
  -> POST /upload-url with Cognito ID token
  -> existing API Gateway JWT authorizer
  -> Upload Lambda
       -> POST metadata /internal/uploads/reserve
       -> return S3 pre-signed PUT
  -> browser PUT originals/{user_id}/{file_id}/{filename}
  -> S3 ObjectCreated event (originals/ only)
  -> Media Processing Lambda
       -> POST metadata processing status
       -> create thumbnail OR extract one frame/second
       -> POST inference /infer with short-lived image URLs
       -> PUT metadata completion status
       -> delete temporary video frames
```

All S3 resources stay private. Browser-facing media access uses pre-signed GET
URLs produced by the API layer that owns the corresponding user operation.

## Upload Contract

### Request

```http
POST /upload-url
Authorization: Bearer <Cognito ID token>
Content-Type: application/json
```

```json
{
  "filename": "wombat.jpg",
  "content_type": "image/jpeg",
  "size_bytes": 2849132,
  "checksum_sha256": "base64-encoded-32-byte-digest"
}
```

The checksum is the Base64 form of the raw 32-byte SHA-256 digest. The default
maximum upload is 262,144,000 bytes and is configurable through
`MAX_UPLOAD_BYTES`. Accepted media types are `image/jpeg`, `image/png`,
`image/webp`, `video/mp4`, and `video/quicktime`.

### Success response

```json
{
  "file_id": "server-generated-uuid",
  "object_key": "originals/cognito-sub/server-generated-uuid/wombat.jpg",
  "upload_url": "https://s3-presigned-put-url",
  "expires_in": 300,
  "required_headers": {
    "Content-Type": "image/jpeg",
    "x-amz-checksum-sha256": "base64-encoded-32-byte-digest"
  }
}
```

The backend sanitizes the filename and generates the key. A client cannot
select an arbitrary S3 key. The PUT URL is valid for 300 seconds by default and
binds the declared content length, content type, and checksum. Browser code
sets the two returned `required_headers`; the browser supplies `Content-Length`
automatically and frontend code must not attempt to set that forbidden header.

### Error responses

| Status | Code | Meaning |
| --- | --- | --- |
| 400 | `INVALID_REQUEST` | JSON body or a required field is invalid |
| 400 | `UNSUPPORTED_FILE_TYPE` | MIME type is outside the accepted list |
| 400 | `INVALID_CHECKSUM` | checksum is not a Base64 SHA-256 digest |
| 401 | `UNAUTHENTICATED` | verified Cognito `sub` is unavailable |
| 409 | `DUPLICATE_FILE` | the same user already reserved the checksum |
| 503 | `DEPENDENCY_UNAVAILABLE` | metadata reservation cannot be completed |
| 500 | `INTERNAL_ERROR` | pre-signing or an unexpected operation fails |

## Metadata Contracts

Every request includes `Content-Type: application/json`. When
`INTERNAL_API_KEY` is configured it is sent as `X-Internal-Api-Key`.

### Reserve upload

```http
POST {METADATA_API_BASE_URL}/internal/uploads/reserve
```

```json
{
  "file_id": "uuid",
  "user_id": "cognito-sub",
  "checksum": "base64-sha256",
  "filename": "wombat.jpg",
  "file_type": "image",
  "content_type": "image/jpeg",
  "size_bytes": 2849132,
  "object_key": "originals/cognito-sub/uuid/wombat.jpg",
  "status": "pending_upload"
}
```

The metadata implementation enforces a unique `(user_id, checksum)` pair.
`201` reserves the upload. `409` returns
`{"existing_file_id":"existing-uuid"}`.

The reservation request has a five-second client deadline. A transport failure
or deadline expiry maps to `DEPENDENCY_UNAVAILABLE`. A committed
`pending_upload` reservation can still exist when pre-signing fails or the
upload response is not delivered. Before live integration, Member D and Member
B must agree on recovery or cleanup for that state; this repository does not
invent a reservation-replay or cancellation endpoint.

### Begin processing

```http
POST {METADATA_API_BASE_URL}/internal/files/{file_id}/processing
```

```json
{
  "user_id": "cognito-sub",
  "object_key": "originals/cognito-sub/uuid/wombat.jpg",
  "sequencer": "s3-event-sequencer"
}
```

`200` with `{"should_process":true}` grants the processing lease. `200` with
`{"should_process":false}` makes a duplicate or stale S3 event a no-op.
Member D must return `false` for an already completed file. A failed or expired
interrupted attempt must be re-acquirable with the same sequencer, while an
active lease for that event is not duplicated.

### Complete processing

```http
PUT {METADATA_API_BASE_URL}/internal/files/{file_id}/complete
```

```json
{
  "user_id": "cognito-sub",
  "file_type": "image",
  "original_key": "originals/cognito-sub/uuid/wombat.jpg",
  "thumbnail_key": "thumbnails/cognito-sub/uuid/thumbnail.jpg",
  "tags": {"wombat": 2},
  "detections": [{"species":"wombat","confidence":0.94}],
  "model_version": "speciesnet-v1",
  "status": "completed"
}
```

For videos, `thumbnail_key` is `null`. Completion PUTs are idempotent: replaying
the same completion does not duplicate state or produce an error.

### Record failure

```http
PUT {METADATA_API_BASE_URL}/internal/files/{file_id}/failed
```

```json
{
  "user_id": "cognito-sub",
  "error_code": "FRAME_EXTRACTION_FAILED",
  "message": "bounded diagnostic message",
  "status": "failed"
}
```

Failure PUTs are also idempotent for retries of the same processing attempt.

## Inference Contract

```http
POST {INFERENCE_API_URL}/infer
```

```json
{
  "file_id": "uuid",
  "media_type": "video",
  "image_urls": [
    "https://temporary-frame-url-1",
    "https://temporary-frame-url-2"
  ]
}
```

```json
{
  "tags": {"dingo": 2},
  "detections": [{"species":"dingo","confidence":0.94}],
  "model_version": "speciesnet-v1"
}
```

The inference endpoint can run in AWS or another provider. The media pipeline
only depends on this JSON contract.

## Storage Layout

```text
originals/{user_id}/{file_id}/{sanitized_filename}
thumbnails/{user_id}/{file_id}/thumbnail.jpg
processing/{user_id}/{file_id}/frames/frame-000001.jpg
```

Only `originals/` generates S3 processing events. Temporary video frames are
deleted in a `finally` path after inference. A lifecycle rule also expires
objects under `processing/` as a recovery control.

## Media Rules

- Thumbnails fit inside 512 x 512 pixels without enlarging the original.
- Thumbnails are JPEG, converted to RGB, and saved with quality 82 and
  optimization enabled.
- Video frame extraction uses `fps=1`; it does not select every source frame.
- Image decode is limited to 40,000,000 pixels. Pillow decompression-bomb
  warnings/errors and larger decoded dimensions map to `INVALID_MEDIA`.
- The executable path is configured by `FFMPEG_PATH`, defaulting to
  `/opt/bin/ffmpeg` in Lambda.
- FFmpeg has an 840-second subprocess timeout, below the Lambda's 900-second
  timeout.
- FFmpeg extracts at most 901 samples to detect overflow; more than the
  900-frame processing cap (about 15 minutes at one frame per second) is
  rejected rather than truncated.
- Image decoding errors map to `INVALID_MEDIA`.
- Video command failures or an empty frame set map to
  `FRAME_EXTRACTION_FAILED`.
- Inference failures map to `INFERENCE_FAILED`.
- Metadata failures remain retryable and cause the Lambda invocation to fail.

## Idempotency

S3 events are treated as at-least-once and potentially out of order. The
metadata processing lease is the source of truth. A record with
`should_process=false` is skipped before media work. Completed files return
`false`; failed or lease-expired interrupted work remains re-acquirable with the
same sequencer. S3 writes use deterministic keys, so a retry replaces the same
thumbnail or frame objects. Completion and failure are idempotent PUTs.
Temporary objects are removed on every exit path.

## Storage Deletion Contract

The internal storage deletion Lambda accepts a direct invocation:

```json
{
  "user_id": "cognito-sub",
  "keys": [
    "originals/cognito-sub/uuid/wombat.jpg",
    "thumbnails/cognito-sub/uuid/thumbnail.jpg"
  ]
}
```

It rejects keys outside `originals/{user_id}/`, `thumbnails/{user_id}/`, and
`processing/{user_id}/`, deletes valid keys in S3 batches, and treats missing
objects as successfully deleted. An empty key list is a successful idempotent
no-op returning zero; an empty individual key is forbidden. A deletion batch is
successful only when S3 returns no per-object `Errors`; mixed-success responses
raise a generic retry-visible failure. Member D's public `DELETE /files` route
owns database removal and invokes this storage boundary.

## Security

- The S3 bucket has Block Public Access enabled.
- API Gateway validates Cognito JWTs before invoking the upload Lambda.
- The Lambda also rejects events without `claims.sub`.
- PUT URLs contain the exact key, content length, content type, and SHA-256
  checksum.
- IAM grants each Lambda access only to the required bucket prefixes/actions.
- Internal HTTP credentials are environment variables and are not committed.
- Logs exclude tokens, internal API keys, pre-signed URLs, and full request
  headers.

## Test Strategy

- Node's built-in test runner covers upload validation, object-key generation,
  duplicate mapping, signing inputs/options, reservation timeout, handler
  responses, storage-delete authorization, and per-object delete failures.
- Pytest covers S3 event parsing, thumbnail geometry/compression, FFmpeg command
  construction and bounds, image decode bounds, pipeline idempotency,
  success/failure metadata, per-object delete failures, and temporary cleanup.
- AWS, inference, and metadata boundaries are dependency-injected. Unit tests
  use behavior fakes; HTTP serialization has focused adapter tests.
- `sam validate --lint` is used when SAM CLI is available; otherwise the
  template receives a YAML parse and structural assertion test.
- Existing frontend production build remains part of the regression gate.

## Work Requiring External Access

The local implementation stops before real AWS deployment, GitHub push,
Cognito token acquisition, Member C endpoint integration, and Member D endpoint
integration. Those operations require account access or concrete endpoint
values. The repository will contain deployment instructions and exact required
environment variables for the manual handoff.
