# Cross-Module Integration Hardening Design

## Context

The repository's isolated test suites pass, but the current AWS → Alibaba
Cloud → DynamoDB path is not safe to deploy as one system. Public Member D
routes authenticate a Cognito token without authorizing the token subject,
Member B sends an internal API key that Member D ignores, real DynamoDB values
can fail serialization, Member C returns a different species vocabulary from
Members D/E, and Member B can time out before Member C's advertised processing
window closes. Several runtime integrations are still explicit stubs.

This design hardens the existing coursework architecture. It does not replace
the services, introduce a new database, or make live cloud changes.

## Decisions

### Archive visibility and ownership

Search stays archive-wide. An authenticated user may receive metadata keys for
records belonging to other users. Mutations are owner-only: tag edit, file
delete, subscription management, and notification inbox access always derive
identity from the verified Cognito `sub` claim. Client-supplied `user_id` is not
an authority.

Private S3 objects remain owner-only. The `/asset-urls` Lambda continues to
sign only `originals/{sub}/...` and `thumbnails/{sub}/...`. Consequently,
archive-wide results owned by another user remain read-only metadata without a
preview until the team explicitly chooses a public-media policy. This design
does not weaken the signer to guess that policy.

### Public and internal authentication

API Gateway JWT authorization remains the outer boundary for browser routes.
Member D also enforces ownership in application code so a valid JWT is not a
blanket authorization.

All four Member B → D internal routes require `X-Internal-Api-Key`. Member D
compares it with a non-empty `INTERNAL_API_KEY` using constant-time comparison.
Missing server configuration fails closed with HTTP 503; a missing or incorrect
request key returns HTTP 401. Member B continues sending the same header from
upload and processing clients. Internal payload identity and object keys must
match the record reserved earlier; disagreement returns HTTP 409 without a
state change.

### Species vocabulary

The wire contract uses the team's short common-name tags (`dingo`, `wombat`,
`cassowary`, and so on). Member C maps scientific model labels using its bundled
`labels.txt` before returning `tags` and `detections`. Member D normalizes again
before persistence as defense in depth. Unknown labels pass through unchanged
so a future class does not crash processing.

### DynamoDB correctness

Member D fixes the `FileRecord.get()` defect and recursively converts Python
floating-point values to `Decimal` at the DynamoDB boundary. Scan/query helpers
consume all pages so searches, duplicate checks, subscriptions, and
notifications do not silently stop at DynamoDB's first response page. These
changes are covered through fake DynamoDB resources and the real repository
methods, not source-text assertions.

The larger uniqueness/outbox redesign is deliberately excluded from this
coursework hardening batch. Cross-item `(user_id, checksum)` uniqueness and
exactly-once notification delivery require a new claim/outbox data model and a
team migration decision. They are recorded as follow-up decisions rather than
quietly approximated.

### B/C runtime limits

Images accepted by Member B are capped at 12 MiB, matching Member C. Videos keep
the existing 262,144,000-byte upload cap because C receives extracted frames,
not the original video.

Member C processes URLs one at a time and releases each image before fetching
the next. It caps detections at 1,000 and rejects overflow rather than returning
a response that Member B cannot accept. Member B validates each detection and
enforces the same count.

Member C's application budget remains 45 seconds and Alibaba Function Compute's
template timeout remains 60 seconds. Member B's HTTP timeout is configurable
and defaults to 70 seconds, so it does not abandon and delete temporary frames
before either C's cooperative timeout or the platform hard timeout. Member C no
longer detaches a running executor after reporting 504; the request thread owns
the inference work and checks a monotonic deadline between fetch/decode/predict
boundaries. A single non-cooperative model call can only be stopped by the
Function Compute process timeout, which is why B's timeout exceeds 60 seconds.

Member B preserves C's useful failure categories: authentication/validation
failures are non-retryable contract failures, while dependency 5xx/timeout and
network failures are retryable inference-unavailable failures.

### Member D runtime adapters

Production deletion uses `LambdaStorageClient`, invoking Member B's guarded
delete Lambda synchronously with `{"user_id": ..., "keys": [...]}`. It validates
the Lambda invocation status, `FunctionError`, nested response status, response
size, and JSON shape. Member D calls storage before deleting metadata, so a
storage failure leaves a record that can be retried.

Production query-by-file supports images only. Member D bounds the multipart
input to exactly 4,194,304 bytes, stages it under a request-scoped private S3
prefix, creates a short-lived HTTPS GET URL, and calls Member C's `/infer` using
the shared internal key with redirects disabled and a 25-second timeout. The D
Lambda timeout is 30 seconds. Cleanup is attempted after every put attempt;
cleanup failures are controlled and never mask an original inference failure.
The B/C upload and source limits remain 12,582,912 bytes with the separate
45/60/70 timeout ordering. Video query-by-file returns
415 because the existing C API expects extracted frames and no non-persisting
video-frame service exists. Local tests inject fakes; production configuration
must explicitly select the remote detector so a missing endpoint never returns
the old fake `dingo` result.

Member D's SAM template creates the query Lambda, its environment, permissions,
one API Gateway integration, explicit JWT-protected public routes, unauthenticated
API Gateway internal routes protected by the application key, and Lambda invoke
permissions. It requires the existing HTTP API ID, JWT authorizer ID, Member B
bucket/delete function, C endpoint, and secret as deployment parameters.

### Frontend authentication and API use

Login and sign-up share one PKCE transaction builder. Both store a verifier and
state and send `code_challenge_method=S256`. Callback exchange is single-flight
per authorization code, so React StrictMode replays share one token request.
Failed exchanges remain retryable with the same valid PKCE transaction; a
successful exchange stores tokens and removes the transaction.

The API client throws a structured `ApiError` preserving HTTP status, backend
code, and payload while safely handling non-JSON errors. Duplicate-file UI uses
`error.code`, not message substring matching.

Notification requests no longer send `user_id`. Query results stay global, but
only keys under the signed-in user's canonical media prefixes can be selected
or manually submitted for edit/delete. Server authorization remains the real
security boundary.

## Error handling

- Public ownership violation: HTTP 403 with `detail.code=FORBIDDEN_OWNER`.
- D internal server secret absent: HTTP 503 with
  `detail.code=INTERNAL_AUTH_NOT_CONFIGURED`.
- D internal key absent/incorrect: HTTP 401 with
  `detail.code=INVALID_INTERNAL_API_KEY`.
- Internal record/payload disagreement: HTTP 409 with
  `detail.code=METADATA_CONFLICT`.
- Oversized B image upload: HTTP 413 with `code=FILE_TOO_LARGE`.
- Unsupported query-by-file media: HTTP 415.
- Configured storage adapter failure: HTTP 502 with metadata preserved;
  unconfigured storage adapter: HTTP 503.
- C detection overflow: HTTP 422 with `error=detection_limit_exceeded`.
- Real adapter/configuration failures fail closed; no production route silently
  falls back to a stub.

## Testing

Every behavior change is test-first. Focused tests demonstrate the failure on
the current code, then pass after the minimal implementation. The final gate
runs Member B Python/SAM tests, all three Member B Node suites, Member C tests,
Member D tests including new DynamoDB/adapter tests, frontend Node tests, and a
frontend production build.

## Deployment-owned inputs

The repository cannot supply or validate the following values:

- the shared internal API key or Secrets Manager delivery mechanism;
- the live Alibaba Function Compute HTTPS endpoint, registry image, and model
  assets;
- Member B's deployed media bucket, FFmpeg layer, and storage-delete Lambda
  function name for Member D's deployment parameter; its ARN is retained only
  for audit/IAM reference;
- final API Gateway stage/routes and live authorizer attachment;
- a public-media policy for cross-owner previews;
- whether video query-by-file must be added through a new frame-extraction
  function;
- whether the team wants a reservation-claim/outbox schema migration for hard
  concurrency and exactly-once guarantees.

No secret value is committed, no cloud resource is deployed, and no branch is
pushed by this implementation.
