# Member B API contracts

These JSON/HTTP boundaries let Member B integrate with Member C and Member D
without depending on their hosting provider, model implementation, or database.
All internal HTTP requests use `Content-Type: application/json` and add
`X-Internal-Api-Key` only when `INTERNAL_API_KEY` is configured.

## Ownership

| Boundary | Caller | Owner |
| --- | --- | --- |
| `POST /upload-url` | Authenticated frontend | Member B |
| `POST /internal/uploads/reserve` | Member B upload Lambda | Member D |
| Processing/complete/failed metadata endpoints | Member B processing Lambda | Member D |
| `POST /infer` | Member B processing Lambda | Member C |
| Direct storage-delete invocation | Member D deletion workflow | Member B |

## Protected upload URL

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
  "checksum_sha256": "BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc="
}
```

The checksum is canonical Base64 for the raw 32-byte SHA-256 digest. The
default size limit is 262,144,000 bytes. Accepted content types are
`image/jpeg`, `image/png`, `image/webp`, `video/mp4`, and `video/quicktime`.
The verified Cognito `sub` becomes `user_id`; callers cannot provide it or
choose an S3 key.

Success (`200`):

```json
{
  "file_id": "11111111-2222-4333-8444-555555555555",
  "object_key": "originals/cognito-sub/11111111-2222-4333-8444-555555555555/wombat.jpg",
  "upload_url": "https://temporary-presigned-put-url",
  "expires_in": 300,
  "required_headers": {
    "Content-Type": "image/jpeg",
    "x-amz-checksum-sha256": "BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc="
  }
}
```

The browser must use the exact required headers when PUTting to the returned
short-lived URL. `OPTIONS` returns `204` for the configured origin.

| Status | Code | Meaning |
| --- | --- | --- |
| `400` | `INVALID_REQUEST` | Body, filename, or size is invalid. |
| `400` | `UNSUPPORTED_FILE_TYPE` | Content type is outside the accepted set. |
| `400` | `INVALID_CHECKSUM` | Checksum is not canonical Base64 for 32 bytes. |
| `401` | `UNAUTHENTICATED` | The verified JWT `sub` claim is absent. |
| `409` | `DUPLICATE_FILE` | Member D already reserved this user's checksum; `existing_file_id` is included when supplied. |
| `503` | `DEPENDENCY_UNAVAILABLE` | The metadata reservation did not complete. |
| `500` | `INTERNAL_ERROR` | Pre-signing or another unexpected operation failed. |

## Member D metadata contracts

### Reserve an upload

```http
POST {METADATA_API_BASE_URL}/internal/uploads/reserve
```

```json
{
  "file_id": "11111111-2222-4333-8444-555555555555",
  "user_id": "cognito-sub",
  "checksum": "BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc=",
  "filename": "wombat.jpg",
  "file_type": "image",
  "content_type": "image/jpeg",
  "size_bytes": 2849132,
  "object_key": "originals/cognito-sub/11111111-2222-4333-8444-555555555555/wombat.jpg",
  "status": "pending_upload"
}
```

- `201` reserves a unique `(user_id, checksum)` pair.
- `409` returns `{"existing_file_id":"existing-uuid"}`.
- Other responses are treated as `DEPENDENCY_UNAVAILABLE` by the upload
  boundary.

### Acquire the processing lease

```http
POST {METADATA_API_BASE_URL}/internal/files/{file_id}/processing
```

```json
{
  "user_id": "cognito-sub",
  "object_key": "originals/cognito-sub/11111111-2222-4333-8444-555555555555/wombat.jpg",
  "sequencer": "0068A4B1D2C3E4F500"
}
```

`200` returns exactly a JSON object containing a Boolean lease decision:

```json
{"should_process": true}
```

`false` means the duplicate or stale event is a successful no-op before S3
download. Transport, non-success, invalid JSON, and malformed responses are
retryable dependency failures.

### Complete processing

```http
PUT {METADATA_API_BASE_URL}/internal/files/{file_id}/complete
```

Image payload:

```json
{
  "user_id": "cognito-sub",
  "file_type": "image",
  "original_key": "originals/cognito-sub/11111111-2222-4333-8444-555555555555/wombat.jpg",
  "thumbnail_key": "thumbnails/cognito-sub/11111111-2222-4333-8444-555555555555/thumbnail.jpg",
  "tags": {"wombat": 2},
  "detections": [{"species": "wombat", "confidence": 0.94}],
  "model_version": "speciesnet-v1",
  "status": "completed"
}
```

For videos, `file_type` is `video` and `thumbnail_key` is `null`. Member D must
return a successful status with a JSON object, for example `200 {}`. Completion
is an idempotent PUT.

### Record a bounded processing failure

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

The message is truncated to 240 characters. Locally reportable error codes are
`INVALID_MEDIA`, `FRAME_EXTRACTION_FAILED`, and `INFERENCE_FAILED`. Member D
must return a successful status with a JSON object. Metadata failures remain
retryable so S3/Lambda delivery can retry.

## Member C inference contract

```http
POST {INFERENCE_API_URL}/infer
```

```json
{
  "file_id": "11111111-2222-4333-8444-555555555555",
  "media_type": "video",
  "image_urls": [
    "https://temporary-frame-url-1",
    "https://temporary-frame-url-2"
  ]
}
```

For an image, `media_type` is `image` and `image_urls` contains one temporary
GET URL for the original. For a video it contains the one-frame-per-second
temporary images in order.

Successful JSON response:

```json
{
  "tags": {"dingo": 2},
  "detections": [{"species": "dingo", "confidence": 0.94}],
  "model_version": "speciesnet-v1"
}
```

`tags` must be an object, `detections` a list, and `model_version` a non-empty
string. A transport, HTTP, JSON, or schema failure maps to `INFERENCE_FAILED`.

## Guarded storage deletion

Member D invokes the Member B Lambda directly after its public deletion flow
has authorized the user and handled database ownership:

```json
{
  "user_id": "cognito-sub",
  "keys": [
    "originals/cognito-sub/11111111-2222-4333-8444-555555555555/wombat.jpg",
    "thumbnails/cognito-sub/11111111-2222-4333-8444-555555555555/thumbnail.jpg"
  ]
}
```

Only keys below `originals/{user_id}/`, `thumbnails/{user_id}/`, and
`processing/{user_id}/` are accepted. Valid keys are deleted in S3 batches of
at most 1,000; missing objects remain a successful idempotent deletion.

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | `{"deleted_count":2}` | All supplied keys were accepted and submitted for deletion. |
| `400` | `{"code":"INVALID_REQUEST"}` | Invocation shape or an empty/ambiguous key is invalid. |
| `403` | `{"code":"FORBIDDEN_KEY"}` | At least one key is outside this user's owned prefixes. |
| `500` | `{"code":"INTERNAL_ERROR"}` | Unexpected S3 or runtime failure. |
