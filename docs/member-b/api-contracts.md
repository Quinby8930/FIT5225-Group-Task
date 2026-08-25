# Member B API contracts

These JSON/HTTP boundaries let Member B integrate with Member C and Member D
without depending on their hosting provider, model implementation, or database.
All deployed internal HTTP requests use `Content-Type: application/json` and
send the required non-empty `INTERNAL_API_KEY` as `X-Internal-Api-Key`.
`METADATA_API_BASE_URL` and `INFERENCE_API_URL` must be valid HTTPS URLs;
clients reject plaintext or malformed endpoint configuration before sending a
request. Authenticated requests do not follow redirects, so the shared key is
never forwarded to a second destination. JSON dependency responses are limited
to 1 MiB.

## Ownership

| Boundary | Caller | Owner |
| --- | --- | --- |
| `POST /upload-url` | Authenticated frontend | Member B |
| `POST /asset-urls` | Authenticated frontend | Member B |
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

The checksum is canonical Base64 for the raw 32-byte SHA-256 digest. Images
are limited to 12,582,912 bytes so the original remains within C's source
limit. Videos keep a separate 262,144,000-byte limit. Accepted content types are
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

The pre-signed PUT binds the declared `size_bytes` as `Content-Length`, plus the
exact content type and checksum. The browser must use the two returned
`required_headers` when PUTting to the short-lived URL. Browser networking code
supplies `Content-Length` automatically; frontend JavaScript must not attempt to
set that forbidden header. The unauthenticated `OPTIONS /upload-url` route
returns `204` for the configured origin, while POST remains JWT-protected.

| Status | Code | Meaning |
| --- | --- | --- |
| `400` | `INVALID_REQUEST` | Body, filename, or size is invalid. |
| `400` | `UNSUPPORTED_FILE_TYPE` | Content type is outside the accepted set. |
| `400` | `INVALID_CHECKSUM` | Checksum is not canonical Base64 for 32 bytes. |
| `401` | `UNAUTHENTICATED` | The verified JWT `sub` claim is absent. |
| `413` | `FILE_TOO_LARGE` | The declared image or video size exceeds its media-specific cap. |
| `409` | `DUPLICATE_FILE` | Member D already reserved this user's checksum; `existing_file_id` is included when supplied. |
| `503` | `DEPENDENCY_UNAVAILABLE` | The metadata reservation did not complete. |
| `500` | `INTERNAL_ERROR` | Pre-signing or another unexpected operation failed. |

## Protected private asset URLs

```http
POST /asset-urls
Authorization: Bearer <Cognito ID token>
Content-Type: application/json
```

```json
{
  "keys": [
    "thumbnails/cognito-sub/11111111-2222-4333-8444-555555555555/thumbnail.jpg",
    "originals/cognito-sub/11111111-2222-4333-8444-555555555555/wombat.jpg"
  ]
}
```

The verified Cognito `sub` is the only user identifier used for authorization.
The endpoint accepts at most 100 keys, removes duplicates while preserving
first-seen order, and signs only non-empty objects below
`originals/{sub}/` or `thumbnails/{sub}/`. Cross-user keys, incomplete
prefixes, and internal `processing/` frames are rejected before any URL is
created. Each key is also limited to the S3 maximum of 1,024 UTF-8 bytes. The
S3 bucket remains private.

Success (`200`):

```json
{
  "assets": [
    {
      "key": "thumbnails/cognito-sub/11111111-2222-4333-8444-555555555555/thumbnail.jpg",
      "url": "https://temporary-presigned-get-url",
      "expires_in": 900
    }
  ]
}
```

The URL is an HTTPS bearer credential valid for 15 minutes. Responses include
`Cache-Control: no-store`. The frontend refreshes displayed URLs shortly before
expiry and must not persist or log them. An empty key list returns `200
{"assets":[]}`. The unauthenticated `OPTIONS /asset-urls` route returns `204`
for the configured browser origin.

| Status | Code | Meaning |
| --- | --- | --- |
| `400` | `INVALID_REQUEST` | JSON, key types, 1,024-byte key size, or the 100-key limit is invalid. |
| `401` | `UNAUTHENTICATED` | The verified JWT `sub` claim is absent. |
| `403` | `FORBIDDEN_KEY` | At least one key is outside the authenticated user's readable prefixes. |
| `500` | `INTERNAL_ERROR` | S3 signing or another unexpected operation failed. |

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
- The upload boundary aborts a stalled reservation request after five seconds
  and maps abort or network failure to `DEPENDENCY_UNAVAILABLE`.
- A `409` duplicate response is read with the same 1 MiB JSON limit and strict
  UTF-8 decoding; oversized or malformed bodies map to `DEPENDENCY_UNAVAILABLE`.

A reservation can be committed as `pending_upload` even if pre-signing fails or
the response containing the upload URL never reaches the browser. Before live
integration, Member D and Member B must agree on recovery or cleanup for that
state. No replay, cancellation, or cleanup endpoint is implemented or assumed
by this contract.

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
download. Member D must return `false` when the file is already completed. A
failed or lease-expired interrupted attempt must be re-acquirable with the same
sequencer; an active attempt must not be granted twice. Transport, non-success,
invalid UTF-8/JSON, oversized JSON, and malformed responses are retryable
dependency failures.

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
is an idempotent PUT: replaying the same completion produces the same completed
state without duplicated effects.

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

The message is truncated to 240 characters. Reportable error codes are
`INVALID_MEDIA`, `FRAME_EXTRACTION_FAILED`, `INFERENCE_FAILED`,
`INFERENCE_AUTH_FAILED`, `INFERENCE_REJECTED`, `INFERENCE_UNAVAILABLE`, and
`PROCESSING_TIME_BUDGET_EXHAUSTED`. B always records the failed transition
first. A non-retryable error then returns a stable failed result so Lambda does
not retry a terminal 401/4xx/contract rejection forever. A retryable error
rethrows only after Member D clears the active lease, permitting the same S3
event to acquire processing again. Member D must return a successful status
with a JSON object; failure of that metadata PUT propagates as retryable
`DEPENDENCY_UNAVAILABLE`. The failure PUT is idempotent for a replayed attempt.

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
temporary images in lexical order. Image decoding is capped at 40,000,000
pixels. Video extraction samples one frame per second, scales each frame so its
longest dimension is at most 1,024 pixels without upscaling, and writes JPEGs
at FFmpeg quality 5. Extraction times out after at most 600 seconds, rejects
more than 900 sampled frames (about 15 minutes), and rejects more than 2 GiB of
total frame output rather than silently truncating a longer video. FFmpeg is
non-interactive and can read only local `file`/`pipe` protocols.

The processing Lambda reserves 180 seconds from its reported remaining time
for frame upload, inference, status reporting, and cleanup. It recalculates the
FFmpeg timeout from that budget and rechecks the reserve before every frame
upload and before inference. Budget exhaustion deletes all frames already
uploaded under `processing/`, records the retryable failed transition to clear
the lease, and then lets the exception escape so Lambda delivery can retry.

Successful JSON response:

```json
{
  "tags": {"dingo": 2},
  "detections": [{"species": "dingo", "confidence": 0.94}],
  "model_version": "speciesnet-v1"
}
```

Species keys and detection species use the team's short wire names, such as
`dingo`, `wombat`, and `cassowary`, rather than scientific model labels.
`tags` must be an object and `model_version` a non-empty string. `detections`
contains at most 1,000 objects; every object has a non-empty species of at most
128 characters and a finite numeric confidence in `[0,1]`. Invalid UTF-8/JSON,
a response over 1 MiB, or a malformed schema maps to non-retryable
`INFERENCE_FAILED`.

C's application deadline is 45 seconds, the Alibaba Function Compute timeout
is 60 seconds, and B applies a 70-second timeout only to `InferenceClient`;
metadata clients retain their 10-second default. C HTTP `401` maps to
non-retryable `INFERENCE_AUTH_FAILED`; other C 4xx responses map to
non-retryable `INFERENCE_REJECTED`; C 5xx (including `504`), network failures,
and client timeouts map to retryable `INFERENCE_UNAVAILABLE`. Remote C response
bodies are never included in B's errors.

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
at most 1,000; missing objects remain a successful idempotent deletion. Success
requires every batch response to contain no per-object `Errors`; a mixed-success
S3 response becomes a generic retry-visible failure without exposing keys or
AWS error bodies. An empty key list is a successful no-op returning zero. An
empty individual key is forbidden.

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | `{"deleted_count":2}` | All unique keys were deleted with no per-object errors; an empty list returns `0`. |
| `400` | `{"code":"INVALID_REQUEST"}` | The invocation shape is invalid. |
| `403` | `{"code":"FORBIDDEN_KEY"}` | At least one key is empty or outside this user's owned prefixes. |
| `500` | `{"code":"INTERNAL_ERROR"}` | S3 reported per-object errors, or another runtime failure occurred. |
