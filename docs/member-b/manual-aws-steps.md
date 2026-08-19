# Member B manual AWS and integration steps

No AWS deployment or live service integration was performed while creating the
Member B implementation. This checklist separates repository evidence from
operations that require the student, account access, teammate endpoints, or
deployment approval.

## Already implemented and locally verifiable

- Protected upload handler behavior and checksum-bound S3 pre-signing.
- Per-user duplicate reservation through Member D's HTTP contract.
- S3 event parsing, 40 MP-bounded image thumbnails, one-frame-per-second video
  sampling with an 840-second/900-frame bound, Member C inference calls, Member
  D status calls, and temporary-frame cleanup.
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
aws apigatewayv2 get-authorizers --api-id 2dd2aqb32j --region $CourseRegion
```

Record only the authorizer ID in the deployment interaction. Verify that it is
a JWT authorizer for the expected Cognito issuer and audience; do not copy
tokens into documentation.

### 3. Obtain Member C and Member D endpoint values

Ask Member C for the reachable inference base URL and Member D for the
reachable metadata base URL. Validate their ownership and the JSON contracts
in [`api-contracts.md`](api-contracts.md). Keep these as deployment parameter
values; do not replace the parameters with guessed or fake endpoints.

Before live integration, agree with Member D on recovery or cleanup for a
committed `pending_upload` reservation when pre-signing fails or the browser
never receives the URL. Also confirm the lease semantics: completed files
return `should_process:false`, failed or expired interrupted work is
re-acquirable with the same sequencer, and completion/failure PUTs are
idempotent. Do not invent a replay or lease-token endpoint to fill this gate.

If the team uses an internal API key, pass it using the course-approved secret
workflow. Do not put the value in Git, documentation, screenshots, chat, or a
saved command line.

### 4. Provide the FFmpeg Lambda layer

Obtain or build an approved layer compatible with the processing function's
Python 3.12 Lambda environment and architecture. It must expose an executable
at exactly `/opt/bin/ffmpeg`. Record the layer ARN as the `FfmpegLayerArn`
deployment parameter only after checking its source and permissions.

The repository does not contain FFmpeg binaries, archives, or a layer ARN.

### 5. Validate, review, and obtain deployment approval

Start Docker, then run local checks. The container build is required for
Pillow's native Lambda dependencies:

```powershell
python -m pytest infrastructure/member-b/test_template.py backend/lambdas/media-processing/tests -q
node --test backend/lambdas/upload/test/*.test.mjs backend/lambdas/storage-delete/test/*.test.mjs
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
teammate endpoint values, the approved FFmpeg layer, and the intended browser
origin when prompted. Review the change set before confirmation.

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

### 7. Collect CloudWatch and S3 evidence

Capture submission-safe screenshots showing:

- the three Lambda functions and successful invocations;
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
- Give Member D the `StorageDeleteFunctionArn` output and agree on an
  invocation permission/role before its public delete workflow invokes it.
- Exercise success, duplicate/stale-event skip, inference failure, and metadata
  retry behavior end to end.

### 9. Prepare the demo and submission

Demonstrate authenticated upload, image/video processing, tags, duplicate
handling, and deletion. Include local test output and approved live screenshots
in the team evidence. Confirm no credentials, tokens, endpoint secrets, model
weights, build output, or archives are staged before the final submission.
