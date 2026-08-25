# Cross-Module Integration Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing A–E coursework code safe and internally consistent enough for real AWS ↔ Alibaba Cloud integration, while leaving deployment-owned values and the public-media product decision to the team.

**Architecture:** Keep the current API Gateway, Lambda, S3, Alibaba inference, and DynamoDB boundaries. Harden identity at D, normalize the C→B→D contract, replace D's production stubs with explicit adapters, and make E consume the secured API without selecting identities. Global search remains global; write operations and signed media stay owner-scoped.

**Tech Stack:** Python 3.12/FastAPI/Mangum/boto3, Node.js 20/AWS SDK, React 19/Vite, AWS SAM/CloudFormation, Alibaba Function Compute HTTP service.

**Spec:** `docs/superpowers/specs/2026-08-26-cross-module-integration-hardening-design.md`

## Global Constraints

- Keep archive-wide query results; enforce Cognito `sub` ownership for tag edit, file delete, subscriptions, and notifications.
- Public routes use API Gateway JWT authorization; all D internal routes additionally require non-empty `X-Internal-Api-Key` and fail closed when D is unconfigured.
- Wire species tags are team short names; C normalizes before returning and D normalizes before storing.
- Maximum B image upload and C source image size is exactly 12,582,912 bytes; maximum B video upload remains exactly 262,144,000 bytes.
- Maximum inference detections is exactly 1,000 at C and at B's response validator.
- C application timeout is 45 seconds, Alibaba Function Compute timeout remains 60 seconds, and B inference HTTP timeout defaults to 70 seconds.
- Production code never silently falls back to fake deletion or fake `dingo` inference; local tests may inject explicit fakes.
- Do not commit secrets, live endpoints, model binaries, registry credentials, or AWS credentials.
- Do not weaken B's owner-prefix asset signer and do not choose a public cross-owner media policy.
- Use TDD for every behavior change. Keep the complete branch at no more than five new commits, including this design/plan commit.

---

### Task 1: Secure Member D and repair its DynamoDB boundary

**Files:**
- Modify: `backend/lambdas/query/app/config.py`
- Modify: `backend/lambdas/query/app/schemas.py`
- Modify: `backend/lambdas/query/app/main.py`
- Modify: `backend/lambdas/query/app/repository/dynamodb_repo.py`
- Modify: `backend/lambdas/query/app/repository/notification_repo.py`
- Modify: `backend/lambdas/query/tests/test_queries.py`
- Create: `backend/lambdas/query/tests/test_dynamodb_repository.py`
- Modify: `backend/lambdas/query/INTEGRATION.md`

**Interfaces:**
- Consumes: verified Cognito subject string from `get_current_user`; B's `X-Internal-Api-Key`; existing reserve/processing/complete/failed JSON shapes.
- Produces: owner-safe public mutations; identity-free public notification request shapes; fail-closed internal auth; normalized/serializable DynamoDB records.

- [ ] **Step 1: Write failing ownership and identity tests**

Add a foreign `u2` record with canonical keys to the FastAPI fixture. Assert that global `/query/by-tags` still returns records from both owners, while `/tags/edit` and `/files/delete` return `403` with `detail.code == "FORBIDDEN_OWNER"` for a foreign or mixed-owner request and perform no DB/storage mutation. Change notification tests so the authenticated `u1` subscribes, lists, and unsubscribes without any `user_id`; assert a body containing `user_id: "u2"` is rejected with 422.

- [ ] **Step 2: Run the ownership tests and verify RED**

Run:

```powershell
& 'D:\Study\Monash\FIT5225\A2\.review-artifacts\query-venv-20260826\Scripts\python.exe' -m pytest tests/test_queries.py -q
```

Expected: failures show cross-owner mutation succeeds and legacy notification identity is accepted.

- [ ] **Step 3: Enforce public ownership and server-derived identity**

Make `SubscribeRequest` contain only `species` and set `model_config = {"extra": "forbid"}`. Add one helper that rejects the full edit/delete request before mutation when any resolved `FileRecord.user_id` differs from the authenticated subject. Call storage before metadata deletion. Remove public `user_id` body/query parameters and use `_user` for all subscription and notification repository operations. Preserve global query behavior.

Return ownership errors as:

```python
raise HTTPException(
    status_code=403,
    detail={"code": "FORBIDDEN_OWNER", "message": "media is not owned by the authenticated user"},
)
```

- [ ] **Step 4: Write failing internal-auth and payload-integrity tests**

For each of the four internal routes, cover: missing configured server secret → 503, missing/wrong request header → 401, correct header → existing success behavior. Cover processing/complete/failed bodies whose `user_id` differs from the reserved record and completion whose original key or file type differs; expect `409 detail.code == "METADATA_CONFLICT"` with no state transition.

- [ ] **Step 5: Run the internal tests and verify RED**

Run the focused tests from `tests/test_queries.py`; expected failures show internal calls are currently unauthenticated and mismatches mutate records.

- [ ] **Step 6: Add fail-closed internal authentication and integrity checks**

Add `internal_api_key: str` to settings. Introduce an overridable `get_settings()` and a shared dependency using `Header(alias="X-Internal-Api-Key")` plus `hmac.compare_digest`. Attach it to all internal routes. Validate the body against the reserved `user_id`, `object_key`, and `file_type` before transitions. Normalize `tags` keys and each detection's `species` through `get_mapper().common_name()` immediately before persistence and notification generation.

- [ ] **Step 7: Write failing DynamoDB adapter tests**

Use small fake table objects to prove:

```python
repo.by_keys(["originals/u1/f1/a.jpg"])
```

returns `FileRecord` values instead of calling `.get()` on a model; paginated `scan`/`query` results are fully combined; and `mark_completed(... detections=[{"confidence": 0.94}])` sends `Decimal("0.94")` recursively while `_from_item` exposes ordinary JSON-compatible numbers to Pydantic.

- [ ] **Step 8: Run the DynamoDB tests and verify RED**

Run:

```powershell
& 'D:\Study\Monash\FIT5225\A2\.review-artifacts\query-venv-20260826\Scripts\python.exe' -m pytest tests/test_dynamodb_repository.py -q
```

Expected: `FileRecord.get` fails, only the first page is returned, and float serialization remains unsafe.

- [ ] **Step 9: Repair DynamoDB conversion and pagination**

Add private `_scan_all`/`_query_all` helpers that follow `LastEvaluatedKey`. Fix `by_keys` to read `record.object_key` and `record.thumbnail_key`. Add recursive Dynamo conversion functions: floats become `Decimal(str(value))`; persisted `Decimal` confidences become Python floats when reconstructing detections, while integer tag counts remain integers. Apply pagination to file scans/lookups and Dynamo notification queries/scans.

- [ ] **Step 10: Run all Member D tests and commit**

Run the complete D suite. Commit only Task 1 files:

```text
fix: secure query metadata boundaries
```

---

### Task 2: Align the Member B–C inference contract and runtime limits

**Files:**
- Create: `backend/ml-inference/app/species.py`
- Modify: `backend/ml-inference/app/config.py`
- Modify: `backend/ml-inference/app/inference.py`
- Modify: `backend/ml-inference/app/main.py`
- Modify: `backend/ml-inference/tests/test_contract.py`
- Modify: `backend/ml-inference/.env.example`
- Modify: `backend/ml-inference/s.yaml`
- Modify: `backend/ml-inference/docs/API_CONTRACT.md`
- Modify: `backend/ml-inference/docs/B_HANDOFF.md`
- Modify: `backend/lambdas/upload/validation.mjs`
- Modify: `backend/lambdas/upload/service.mjs`
- Modify: `backend/lambdas/upload/index.mjs`
- Modify: `backend/lambdas/upload/test/validation.test.mjs`
- Modify: `backend/lambdas/upload/test/service.test.mjs`
- Modify: `backend/lambdas/upload/test/handler.test.mjs`
- Modify: `backend/lambdas/media-processing/handler.py`
- Modify: `backend/lambdas/media-processing/media_pipeline/http_clients.py`
- Modify: `backend/lambdas/media-processing/tests/test_handler.py`
- Modify: `backend/lambdas/media-processing/tests/test_http_clients.py`
- Modify: `infrastructure/member-b/template.yaml`
- Modify: `infrastructure/member-b/test_template.py`
- Modify: `docs/member-b/api-contracts.md`

**Interfaces:**
- Consumes: C's bundled seven-column `labels.txt`; B's image/video upload request; C `/infer` response.
- Produces: short-name C responses, bounded streaming inference, image-specific upload cap, 70-second B HTTP timeout, validated detection payloads.

- [ ] **Step 1: Write failing C mapping, streaming, deadline, and result-limit tests**

Add literal expectations for `Canis_familiaris → dingo`, `Canis_dingo → dingo`, `Vombatus_ursinus → wombat`, `Casuarius_casuarius → cassowary`, case-insensitive matching, and unknown pass-through. Make inference tests assert URLs are fetched and predicted one at a time, returned `tags` and `detections.species` use short names, a crossed monotonic deadline raises `InferenceTimeoutError`, and prediction 1,001 raises `InferenceResultLimitError` without returning a partial inconsistent result.

- [ ] **Step 2: Run C tests and verify RED**

Run `python -m pytest tests -q` from `backend/ml-inference`; expected failures show scientific names, materialized source lists, detached timeout behavior, and no detection cap.

- [ ] **Step 3: Implement the C contract**

Create a labels-file mapper using field 5 + field 6 as the scientific key and the final word of field 7 as the team tag. Inject it into `InferenceService`. Iterate directly over `request.image_urls`, open/predict/close one image per iteration, check a supplied monotonic deadline before and after fetch/decode/predict, and reject before appending detection 1,001. Add `MAX_DETECTIONS=1000` to `Settings`.

Remove the `ThreadPoolExecutor` timeout wrapper. In the HTTP handler call:

```python
deadline = time.monotonic() + settings.request_timeout_seconds
result = inference_service.infer(request, deadline=deadline)
```

Set `InferenceServer.daemon_threads = False`. Catch `InferenceResultLimitError` as HTTP 422 `detection_limit_exceeded` and `InferenceTimeoutError` as HTTP 504.

- [ ] **Step 4: Write failing B upload-limit tests**

Assert an image of exactly 12,582,912 bytes is accepted, an image one byte larger raises `FILE_TOO_LARGE`, and a video larger than 12 MiB remains valid until 262,144,000 bytes. Assert the handler maps `FILE_TOO_LARGE` to HTTP 413 and passes both configured caps to the service.

- [ ] **Step 5: Run B upload tests and verify RED**

Run `npm test` from `backend/lambdas/upload`; expected failures show one shared size limit and no 413 code.

- [ ] **Step 6: Implement the B image-specific cap**

Thread `{maxBytes, maxImageBytes}` through validation, service, and handler. Parse `MAX_IMAGE_UPLOAD_BYTES` with default `12_582_912`; keep `MAX_UPLOAD_BYTES=262_144_000` for videos. Map `FILE_TOO_LARGE` to 413. Add SAM parameter `MaxImageUploadBytes: 12582912` and UploadFunction environment variable.

- [ ] **Step 7: Write failing B inference-client tests**

Assert handler construction passes `INFERENCE_HTTP_TIMEOUT_SECONDS=70`; `InferenceClient` receives that timeout; a response with 1,001 detections, an empty/oversized species, non-finite confidence, or confidence outside `[0,1]` is rejected. Assert HTTP 401/4xx becomes non-retryable `INFERENCE_REJECTED`/`INFERENCE_AUTH_FAILED`, while 5xx, 504, timeout, and network errors become retryable `INFERENCE_UNAVAILABLE`.

- [ ] **Step 8: Run B media tests and verify RED**

Run the focused handler/client tests. Expected failures show the inherited 10-second timeout, shallow response validation, and collapsed errors.

- [ ] **Step 9: Implement B timeout, validation, and error mapping**

Add `INFERENCE_HTTP_TIMEOUT_SECONDS` parsing with default 70 and pass it only to `InferenceClient`; keep metadata's existing timeout. Validate at most 1,000 detection objects with a non-empty species of at most 128 characters and a finite numeric confidence in `[0,1]`. Preserve the existing 1 MiB response-byte limit. Map dependency status classes as specified without exposing remote response bodies.

- [ ] **Step 10: Synchronize templates/docs, run B and C suites, and commit**

Update C env/SAM/docs and B SAM/contract docs with exact limits and timeout hierarchy. Run C tests, B Python/SAM tests, and upload Node tests. Commit only Task 2 files:

```text
fix: align cross-cloud inference contract
```

---

### Task 3: Replace Member D production stubs and make its deployment explicit

**Files:**
- Modify: `backend/lambdas/query/app/config.py`
- Modify: `backend/lambdas/query/app/main.py`
- Modify: `backend/lambdas/query/app/storage_client.py`
- Modify: `backend/lambdas/query/app/tag_detector.py`
- Create: `backend/lambdas/query/tests/test_storage_client.py`
- Create: `backend/lambdas/query/tests/test_tag_detector.py`
- Modify: `backend/lambdas/query/tests/test_queries.py`
- Modify: `backend/lambdas/query/requirements.txt`
- Modify: `infrastructure/member-d/dynamodb.yaml`
- Create: `infrastructure/member-d/test_template.py`
- Modify: `docs/member-d/database-setup.md`

**Interfaces:**
- Consumes: B direct Lambda event `{"user_id": string, "keys": string[]}`; C image inference body `{"file_id": string, "media_type": "image", "image_urls": [https_url]}`; existing HTTP API and authorizer IDs.
- Produces: validated Lambda deletion, image-only staged query inference, explicit SAM Lambda/routes/permissions.

- [ ] **Step 1: Write failing storage adapter and endpoint-ordering tests**

Test a real `LambdaStorageClient` boundary with a fake boto3 Lambda client. Cover valid nested `statusCode=200`/`deleted_count`, invocation status outside 2xx, `FunctionError`, response payload over 1 MiB, malformed JSON, and nested non-2xx. Add an endpoint test proving a storage exception leaves the metadata record untouched.

- [ ] **Step 2: Run storage tests and verify RED**

Run the new storage/client and focused delete endpoint tests; expected failures show no real adapter and DB-first deletion.

- [ ] **Step 3: Implement guarded Lambda deletion**

Add `LambdaStorageClient` with `InvocationType="RequestResponse"`, bounded payload decoding, and strict response validation. Build it when `STORAGE_BACKEND=lambda` and `STORAGE_DELETE_FUNCTION_NAME` is non-empty; `STORAGE_BACKEND=stub` remains explicit local-only behavior. Keep storage-before-DB endpoint ordering from Task 1.

- [ ] **Step 4: Write failing remote query-by-file tests**

Test an adapter with fake S3 and HTTP boundaries. It must stage one image under `query-inputs/{user_id}/{uuid}/{safe_filename}`, presign HTTPS GET for 120 seconds, call C `/infer` with the shared key, return normalized tags, and delete the staged object in `finally` on success or failure. Endpoint tests reject non-image content with 415 and payloads above 12,582,912 bytes with 413. Missing production configuration returns 503; it never returns the stub `dingo` result.

- [ ] **Step 5: Run detector tests and verify RED**

Run the new detector and focused query endpoint tests; expected failures show the current two-argument stub and unbounded multipart read.

- [ ] **Step 6: Implement the S3-staged HTTPS detector**

Extend `TagDetector.detect` to keyword arguments `user_id`, `file_name`, `content_type`, and `content`. Implement `RemoteTagDetector` using injected S3/HTTP operations, validate the C response shape, and always clean up. Build it only when `TAG_DETECTOR_BACKEND=remote` with non-empty bucket, HTTPS inference endpoint, and internal key. Keep explicit `stub` selection for local tests. The public endpoint accepts `image/jpeg`, `image/png`, and `image/webp` only and performs a bounded `read(12_582_913)`.

- [ ] **Step 7: Write failing Member D template tests**

Parse `infrastructure/member-d/dynamodb.yaml` with the same intrinsic-tag loader pattern as Member B. Assert it declares SAM transform, QueryFunction Python 3.12/Mangum handler, Dynamo table variables, required non-secret parameters, internal key as `NoEcho`, Lambda invoke permission for B deletion, S3 query-input permissions, one API integration, JWT public routes, `NONE` internal routes, OPTIONS routes, and method-scoped API Gateway invoke permission.

- [ ] **Step 8: Run template tests and verify RED**

Run `python -m pytest infrastructure/member-d/test_template.py -q`; expected failures show the current file creates tables/role only.

- [ ] **Step 9: Convert the D template into a deployable SAM stack**

Retain all three existing tables. Add `AWS::Serverless::Function` for `../../backend/lambdas/query/`, `lambda_function.handler`, runtime `python3.12`, timeout 90, memory 1024, environment selecting DynamoDB/Lambda/remote detector, and least-privilege DynamoDB/S3/Lambda invoke policies. Add explicit routes for every documented public/internal method; public routes use `ExistingJwtAuthorizerId`, internal routes use `AuthorizationType: NONE` because application-key auth is mandatory. Do not add a `$default` route.

- [ ] **Step 10: Run D tests/docs validation and commit**

Run all D tests plus the new template tests. Update setup docs to distinguish repository-complete resources from deployment-owned values. Commit only Task 3 files:

```text
feat: wire query service production adapters
```

---

### Task 4: Make Member E auth and API calls consume the secured contract

**Files:**
- Modify: `frontend/src/auth/cognitoAuth.js`
- Modify: `frontend/src/auth/AuthCallback.jsx`
- Create: `frontend/src/auth/cognitoAuth.test.mjs`
- Modify: `frontend/src/api/apiClient.js`
- Modify: `frontend/src/api/mediaApi.js`
- Create: `frontend/src/api/apiClient.test.mjs`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/e2e/memberEFlow.test.mjs`
- Modify: `frontend/src/lib/assetUrls.mjs`
- Modify: `frontend/src/lib/assetUrls.test.mjs`
- Modify: `frontend/package.json`
- Modify: `frontend/README.md`

**Interfaces:**
- Consumes: Cognito authorization-code/PKCE Hosted UI; D notification routes without `user_id`; FastAPI nested and Lambda top-level error codes; B owner-prefix rule.
- Produces: sign-up PKCE, single-flight callback exchange, structured client errors, owner-only mutation controls, identity-free notification calls.

- [ ] **Step 1: Write failing PKCE and callback single-flight tests**

With stub storage/WebCrypto/fetch, assert both login and sign-up URLs include state, S256 challenge, client ID, code response type, scopes, and callback URL while storing a verifier. Invoke `handleAuthCallback` twice concurrently for the same URL and assert exactly one `/oauth2/token` POST and the same resolved tokens. Assert bad state makes no request and a failed exchange can retry while the PKCE transaction remains.

- [ ] **Step 2: Run auth tests and verify RED**

Add the auth test file to `npm test` and run it. Expected failures show sign-up lacks PKCE and two callback calls exchange the one-time code twice.

- [ ] **Step 3: Implement shared PKCE and callback single-flight**

Create one internal authorization URL builder used by `/oauth2/authorize` and `/signup`. Add a module-scoped `Map` keyed by authorization code; cache the whole exchange promise before `fetch`, retain successful promises for page lifetime, and delete only failed entries. Remove PKCE storage only after successful token storage. In `AuthCallback`, capture the initial URL and guard navigation so StrictMode causes one visible completion.

- [ ] **Step 4: Write failing structured API-error tests**

Assert `{code:"DUPLICATE_FILE"}` and `{detail:{code:"FORBIDDEN_OWNER"}}` produce `ApiError` instances preserving `status`, `code`, and `payload`; malformed/non-JSON 5xx produces a controlled error; 401 clears tokens.

- [ ] **Step 5: Run API-client tests and verify RED**

Run the new file; expected failures show codes are discarded and invalid JSON escapes as a parsing exception.

- [ ] **Step 6: Implement `ApiError` and code-based duplicate handling**

Parse response text defensively, derive nested/top-level code, and throw `ApiError`. Preserve existing authorization headers and 401 token clearing. Change duplicate upload UI to compare `error.code === "DUPLICATE_FILE"`.

- [ ] **Step 7: Write failing identity-free notification and owner-control tests**

Update the end-to-end call expectations so subscribe body is exactly `{"species":"wombat"}` and unsubscribe/list URLs contain no `user_id`. Add pure owner-key helper tests proving only canonical `originals/{sub}/...` and `thumbnails/{sub}/...` values are manageable; foreign/malformed keys are excluded while global results remain present.

- [ ] **Step 8: Run frontend tests and verify RED**

Run `npm test`; expected failures show legacy notification identities and unfiltered management keys.

- [ ] **Step 9: Implement identity-free calls and owner-only management UI**

Change media API notification signatures to species/no-argument forms and update `NotificationPanel`. Export/reuse an owner-key predicate. Pass `canManage` to result rows, disable foreign selection, defensively reject foreign toggles, and submit only owned keys from manual/selected inputs. Show excluded foreign keys as read-only instead of deleting them from global results.

- [ ] **Step 10: Run frontend test/build, full repository verification, and commit**

Run `npm test` and `npm run build` in `frontend`, then run every suite named in the design's Testing section. Commit only Task 4 files:

```text
fix: secure frontend auth and ownership flows
```

After the commit, verify `git diff --check`, a clean working tree, and exactly five or fewer commits ahead of `origin/main`. Do not push or merge without user approval.
