# Member B manual AWS and integration steps

No AWS deployment or live service integration was performed while creating the
Member B implementation. This checklist separates repository evidence from
operations that require the student, account access, teammate endpoints, or
deployment approval.

## Already implemented and locally verifiable

- Protected upload handler behavior and checksum-bound S3 pre-signing.
- Protected, current-user-only temporary GET URLs for originals and thumbnails.
- Per-user duplicate reservation through Member D's HTTP contract.
- S3 event parsing, 40 MP-bounded image thumbnails, one-frame-per-second video
  sampling with a 600-second/900-frame bound, 1,024-pixel scaling, a 2 GiB
  extracted-frame ceiling, Lambda time-budget checks, Member C inference calls,
  Member D status calls, and temporary-frame cleanup.
- Guarded, prefix-scoped storage deletion for Member D.
- Private-bucket SAM resources, prefix-filtered notification, least-privilege
  S3 IAM statements, and API Gateway v2 route resources.
- Structural, Node, and Python tests plus the existing frontend build command.

These artifacts do not prove that any cloud resource exists.

## Operations requiring the student

### 1. Start an approved AWS Academy session

Log in through the course portal, choose the course-approved region, and use
the temporary credentials only through the approved AWS console/CLI mechanism.
Never store credentials, tokens, or copied console values in repository files,
screenshots, documentation, or shell scripts.

For a non-secret region prompt in PowerShell:

```powershell
$CourseRegion = Read-Host 'Course-approved AWS region'
aws sts get-caller-identity
```

Confirm the returned account is the intended AWS Academy account before any
change.

### 2. Look up the existing JWT authorizer ID

```powershell
$HttpApiId = Read-Host 'Existing HTTP API ID'
aws apigatewayv2 get-authorizers --api-id $HttpApiId --region $CourseRegion
```

Record only the authorizer ID in the deployment interaction. Verify that it is
a JWT authorizer for the expected Cognito issuer and audience; do not copy
tokens into documentation.

### 3. Obtain Member C and Member D endpoint values

Ask Member C for the reachable HTTPS inference base URL and Member D for the
reachable HTTPS metadata base URL. Plain HTTP values are rejected by both the
SAM parameters and runtime clients because internal credentials and media
metadata must be protected in transit. Validate endpoint ownership and the JSON
contracts in [`api-contracts.md`](api-contracts.md). Keep these as deployment
parameter values; do not replace the parameters with guessed or fake endpoints.

Before live integration, agree with Member D on recovery or cleanup for a
committed `pending_upload` reservation when pre-signing fails or the browser
never receives the URL. Also confirm the lease response state: `acquired`
returns `should_process:true`, `completed` returns `should_process:false` and
is a successful no-op, and `lease_active` returns `should_process:false` but
must be retried rather than treated as a completed duplicate. Failed or expired
interrupted work is re-acquirable with the same sequencer, and completion/failure
PUTs are idempotent. Any post-acquisition processing error must make a
best-effort failed transition so the lease is released; a failure to report it
must not replace the original processing error. Do not invent a replay or
lease-token endpoint to fill this gate.

Obtain the team's non-empty shared internal API key through the course-approved
secret workflow for only Member A's AWS deployment and Member C's Alibaba Cloud
service. Member A configures the AWS services owned by B and D; Member C
configures the Alibaba Cloud inference service. No team member may put the
value in Git, documentation, screenshots, chat, or a saved command line.

### 4. Provide the FFmpeg Lambda layer

Obtain or build an approved versioned Lambda layer compatible with the
processing function's Python 3.12 Lambda environment and architecture. It must
expose an executable at exactly `/opt/bin/ffmpeg`. `FfmpegLayerArn` is required
at deployment and must be a valid Lambda Layer ARN; check its source and
permissions before supplying it.

The repository does not contain FFmpeg binaries, archives, or a layer ARN.

### 5. Validate, review, and obtain deployment approval

Start Docker, then run local checks. The container build is required for
Pillow's native Lambda dependencies:

```powershell
python -m pytest infrastructure/member-b/test_template.py backend/lambdas/media-processing/tests -q
node --test backend/lambdas/upload/test/*.test.mjs backend/lambdas/storage-delete/test/*.test.mjs backend/lambdas/asset-urls/test/*.test.mjs
sam validate --template-file infrastructure/member-b/template.yaml --lint
sam build --use-container --template-file infrastructure/member-b/template.yaml
```

Review the generated change set with the team and obtain explicit approval
before creating or changing AWS resources. After approval, use the guided
workflow so required values are entered interactively rather than documented:

```powershell
sam deploy --guided --template-file .aws-sam/build/template.yaml
```

Supply the approved region, the existing HTTP API and authorizer, the two
teammate endpoint values, the approved FFmpeg layer, the intended browser
origin, and the required non-empty shared `InternalApiKey` when prompted. The
template has no live API ID or empty-key default. Do not put the shared key in
the repository or command-line arguments, and review the change set before
confirmation.

### 6. Perform a live Cognito/browser upload test

Use the real frontend sign-in flow to obtain a session; do not print or save the
token. From the authenticated UI:

1. Request `POST /upload-url` for one accepted image and one accepted video.
2. PUT each file to its returned URL with the exact `Content-Type` and
   `x-amz-checksum-sha256` headers. Do not set `Content-Length` in frontend
   JavaScript; the browser supplies it automatically and the signature binds it
   to the declared upload size.
3. Verify an unauthenticated request returns `401` and a repeated checksum for
   the same user returns `409` after Member D integration is active.
4. Verify the browser origin works and an unapproved origin is not granted CORS
   access.
5. Request `POST /asset-urls` for the uploaded original and generated thumbnail,
   open the returned HTTPS URLs, and verify a different user's key returns
   `403` without exposing the key in the response.

### 7. Collect CloudWatch and S3 evidence

Capture submission-safe screenshots showing:

- the four Lambda functions and successful invocations;
- the processing trigger filtered to `originals/`;
- CloudWatch entries that contain no token, API key, request header dump, or
  pre-signed URL;
- private S3 objects under `originals/` and, for an image, `thumbnails/`;
- no lingering video frames under `processing/` after completion;
- bucket Block Public Access, encryption, ownership, CORS, and lifecycle
  configuration.

Redact account identifiers when required by course policy. Do not make objects
public for screenshots.

### 8. Complete Member C and Member D integration

- Confirm Member C accepts `POST /infer` and returns tags, detections, and a
  non-empty model version.
- Confirm Member D supports reserve, processing lease, completion, and failure
  endpoints, enforces unique `(user_id, checksum)` reservations, implements the
  agreed reservation recovery/cleanup, and satisfies the lease/idempotency
  semantics above.
- When Member A deploys Member D's stack, pass the `StorageDeleteFunctionName`
  output to its `StorageDeleteFunctionName` parameter. Keep
  `StorageDeleteFunctionArn` only for audit/IAM reference; it is not the value
  accepted by that parameter. Confirm the invocation permission/role before
  the public delete workflow invokes it.
- Exercise success, duplicate/stale-event skip, inference failure, and metadata
  retry behavior end to end.

### 9. Prepare the demo and submission

Demonstrate authenticated upload, image/video processing, tags, duplicate
handling, and deletion. Include local test output and approved live screenshots
in the team evidence. Confirm no credentials, tokens, endpoint secrets, model
weights, build output, or archives are staged before the final submission.
