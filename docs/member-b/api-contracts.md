# Member B API contracts

These JSON/HTTP boundaries let Member B integrate with Member C and Member D
without depending on their hosting provider, model implementation, or database.
All deployed internal HTTP requests use `Content-Type: application/json` and
send the required non-empty `INTERNAL_API_KEY` as `X-Internal-Api-Key`.
Member A configures all AWS B/D resources and their environment variables;
Member C configures only the Alibaba Cloud side. The shared key is handled only
by A/C through a secure channel and must never be committed to Git or posted in chat.
`METADATA_API_BASE_URL` and `INFERENCE_API_URL` must be valid HTTPS URLs;
clients reject plaintext or malformed endpoint configuration before sending a
request. Authenticated requests do not follow redirects, so the shared key is
never forwarded to a second destination. JSON dependency responses are limited
to 1 MiB. The inference client appends `/infer`, so `INFERENCE_API_URL` rejects
an exact decoded `infer` path segment; metadata stage paths such as `/dev`
remain valid.

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
| `409` | `DUPLICATE_FILE` | A completed archive record has the same checksum; the response contains only `code`, `existing_file_id`, and `tags`. |
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

The verified Cognito `sub` is required to call the endpoint. The endpoint accepts
1--100 keys, removes duplicates while preserving first-seen order, and asks
Member D to authorize each canonical archive key against completed metadata.
Any signed-in user may preview a completed original or thumbnail; no owner-prefix
authorization is performed by this endpoint. Invalid prefixes and archive keys
that are missing or not completed are reported per key, while an unavailable or
malformed authorization response fails the entire request closed. Each key is
limited to the S3 maximum of 1,024 UTF-8 bytes. The S3 bucket remains private.

Success (`200`):

```json
{
  "assets": [
    {
      "key": "thumbnails/cognito-sub/11111111-2222-4333-8444-555555555555/thumbnail.jpg",
      "url": "https://temporary-presigned-get-url",
      "expires_in": 900
    }
  ],
  "errors": [
    {"key": "processing/cognito-sub/frame.jpg", "code": "FORBIDDEN_KEY"}
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
| `503` | `AUTHORIZATION_UNAVAILABLE` | Member D authorization could not be safely verified; no key is signed. |
| `500` | `INTERNAL_ERROR` | S3 signing or another unexpected operation failed. |

## Member D metadata contracts

### Authorize completed archive assets

```http
POST {METADATA_API_BASE_URL}/internal/assets/authorize
```

```json
{"keys":["originals/cognito-sub/file.jpg","thumbnails/cognito-sub/file.jpg"]}
```

This internal endpoint accepts only 1--100 unique canonical `originals/` or
`thumbnails/` S3 keys (duplicates are collapsed in first-seen order), with a
non-empty owner and file path and no empty, `.`, `..`, or backslash segments.
It is protected by `X-Internal-Api-Key`. It returns one decision per key:
`{"key":"...","allowed":true}` only when the key is recorded by a
`completed` media record, or `{"key":"...","allowed":false,"code":"FORBIDDEN_KEY|NOT_FOUND|NOT_COMPLETED"}`.
Member B treats any unavailable, non-2xx, oversized, redirected, or malformed
response as `AUTHORIZATION_UNAVAILABLE` and signs no URLs.

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

- `201` returns `{"file_id":"...","object_key":"...","status":"pending_upload",`
  `"reused":false}` for a new claim. An abandoned `pending_upload` or `failed`
  claim returns the same shape with the original `file_id/object_key` and
  `"reused":true`; Member B must pre-sign that returned key.
- An existing same-user `processing` claim returns `409` with
  `{"existing_file_id":"existing-uuid","tags":{}}`. A same-user `completed`
  claim, or a `completed` record owned by any user with the same checksum,
  returns `409` with its current safe tag counts, for example
  `{"existing_file_id":"existing-uuid","tags":{"cat":1}}`.
- Cross-user `pending_upload`, `processing`, and `failed` rows do not block a
  new reservation. If historical completed rows share a checksum, Member D
  chooses the earliest `upload_time`, then the lowest `file_id`.
- A reusable claim whose filename, file type, content type, or size differs
  returns `409 METADATA_CONFLICT` and is not reset.
- Other responses are treated as `DEPENDENCY_UNAVAILABLE` by the upload
  boundary.
- The upload boundary aborts a stalled reservation request after five seconds
  and maps abort or network failure to `DEPENDENCY_UNAVAILABLE`.
- A `409` duplicate response is read with the same 1 MiB JSON limit and strict
  UTF-8 decoding. Its identifier, positive integer tag counts, field count,
  and exact allowed field set are validated; oversized or malformed bodies map
  to `DEPENDENCY_UNAVAILABLE` and pre-signing does not run.

The cross-user completed lookup is a demonstration-scale, best-effort DynamoDB
Scan using the existing permission. It does not change the per-user reservation
key and does not claim atomic global checksum uniqueness during concurrent
uploads. Duplicate responses never include owner, email, filename, object key,
or thumbnail key.

A reservation can remain `pending_upload` if pre-signing fails or the response
never reaches the browser. Repeating the upload request with identical immutable
metadata reuses its identity and creates a fresh presigned URL. A `failed` row is
also reset to `pending_upload`. Deleting the file clears its checksum reservation,
so a later upload can create a new identity.

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

`200` returns a JSON object containing a Boolean lease decision, a state
discriminator, and a fresh unguessable token when the lease is acquired:

```json
{"should_process": true, "state": "acquired", "lease_token": "<opaque-token>"}
```

The three valid states are `acquired` with `should_process:true` and a token,
`completed`
with `should_process:false`, and `lease_active` with `should_process:false`.
Before returning `completed`, Member D freshly reads the stored completed
metadata and idempotently ensures/publishes its notification inbox. This lets a
new S3 delivery recover an inbox write that failed after the completion CAS;
Member B still treats the response as a successful no-op before S3 download or
inference. `lease_active` is a
retryable processing error, so a concurrent S3 delivery is retried after the
current lease expires or clears. A failed or lease-expired interrupted attempt must be
re-acquirable with the same sequencer; an active attempt must not be granted
twice. Every acquisition receives a different token. Transport, non-success,
invalid UTF-8/JSON, oversized JSON, and
malformed responses are retryable dependency failures.

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
  "lease_token": "<token returned by processing>",
  "status": "completed"
}
```

For videos, `file_type` is `video` and `thumbnail_key` is `null`. Member D must
return a successful status with a JSON object, for example `200 {}`. Completion
is fenced by the active acquisition token, idempotent for metadata, and uses a
deterministic inbox identity. Member D wins the token-conditioned metadata update
before it creates any inbox rows, so a stale worker cannot persist notifications
or overwrite a newer lease. A completed replay idempotently ensures the inbox
from the metadata already stored on the completed record (never from the retry
body), then republishes pending entries.
Delivery is at-least-once, so consumers should dedupe by
`notification_id` if SNS succeeded before delivery-state persistence failed.

### Record a bounded processing failure

```http
PUT {METADATA_API_BASE_URL}/internal/files/{file_id}/failed
```

```json
{
  "user_id": "cognito-sub",
  "error_code": "FRAME_EXTRACTION_FAILED",
  "message": "bounded diagnostic message",
  "lease_token": "<token returned by processing>",
  "status": "failed"
}
```

The message is truncated to 240 characters. B reports every error after it has
acquired a lease: known `MediaPipelineError` values retain their error code,
while an unexpected original exception uses the stable `PROCESSING_FAILED`
code and the bounded generic message. B always attempts the failed transition
first; an error from that best-effort reporting call never replaces the original
processing error. A non-retryable media error then returns a stable failed
result so Lambda does not retry a terminal 401/4xx/contract rejection forever.
A retryable error rethrows after the failed transition is attempted, permitting
the same S3 event to acquire processing again. The failure PUT is fenced by the
active acquisition token and is idempotent for a replayed attempt; a stale
worker cannot clear a newer lease.

The request schema permits an omitted `lease_token` only for a controlled rolling
deployment. Member D rejects omission by default. The only safe rollout is:

1. deploy Member D with `AllowLegacyProcessingCallbacks=true`;
2. deploy this token-aware Member B and confirm acquired callbacks include a
   32–256 character token;
3. redeploy Member D with `AllowLegacyProcessingCallbacks=false` (the default).

Do not leave compatibility enabled in steady state. While enabled, tokenless
callbacks may transition only a record whose current status is `processing`;
they can never mutate `pending_upload`, `failed`, `completed`, or `deleting`.
The temporary window still omits lease-token equality, so a stale legacy worker
can race a newer worker while both generations observe `processing`. This
residual same-status generation risk is why the flag is only a rollout bridge.

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
temporary images in lexical order. B makes consecutive video requests of at
most 30 URLs, presigning each batch immediately before its call. It sums tag
counts and sorts their keys, concatenates detections in batch/response order,
requires exact `model_version` consistency, and rejects the whole video if the
aggregate would exceed 1,000 tag counts or 1,000 detections. Image decoding is capped at 40,000,000
pixels per C service request. Video extraction samples one frame per second, scales each frame so its
longest dimension is at most 1,024 pixels without upscaling, and writes JPEGs
at FFmpeg quality 5. Extraction times out after at most 600 seconds, rejects
more than 900 sampled frames (about 15 minutes), and rejects more than 2 GiB of
total frame output rather than silently truncating a longer video. The
900-frame extraction bound is not a completion guarantee because every batch
must still fit the remaining Lambda and C inference deadlines. FFmpeg is
non-interactive and can read only local `file`/`pipe` protocols.

The processing Lambda reserves 180 seconds from its reported remaining time
for frame upload, inference, status reporting, and cleanup. It recalculates the
FFmpeg timeout from that budget and rechecks the reserve before every frame
upload and every inference batch. Budget exhaustion deletes all frames already
uploaded under `processing/`, records the retryable failed transition to clear
the lease, and then lets the exception escape so Lambda delivery can retry.
Metadata completion occurs only after every batch succeeds, so partial video
results are never persisted.

For image processing, a thumbnail uploaded before a later pre-completion error
is deleted before failure reporting. Cleanup failure never replaces the primary
processing error. Once the first completion call is attempted, B does not
delete the thumbnail because a timeout may be an ambiguous remote success.

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
`tags` must be an object with at most 1,000 raw keys and a total count no greater
than 1,000. B trims species keys, combines whitespace-equivalent duplicates
without case-folding, and sorts the normalized keys. Each species is nonblank
and at most 128 characters after trimming; each count is an integer (not a
Boolean) in `[0,1000]`. `model_version` is a non-empty string. `detections`
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
