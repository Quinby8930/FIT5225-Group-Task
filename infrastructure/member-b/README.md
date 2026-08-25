# Member B AWS SAM infrastructure

This directory defines the private AWS boundary for media upload,
preprocessing, authenticated temporary asset access, and guarded storage
deletion. It connects `POST /upload-url` and `POST /asset-urls` to the existing
HTTP API and JWT authorizer, and sends metadata and inference requests only
through deployment-time HTTPS endpoint parameters. The template rejects
plaintext endpoint values, and both runtime clients validate HTTPS again before
sending the required shared internal API key.

No AWS deployment was performed while preparing these repository artifacts.
The template has local structural coverage; account-specific validation and
deployment remain manual steps.

## Resources

- A private, AES256-encrypted S3 bucket with Bucket Owner Enforced ownership,
  all four Block Public Access settings, browser CORS, and one-day expiry for
  recovery cleanup under `processing/`.
- A Node.js 20 upload Lambda with write access only to `originals/*`.
- A Python 3.12 processing Lambda triggered only by object creation under
  `originals/`; it can read originals and processing frames, write thumbnails
  and processing frames, and delete processing frames.
- A Node.js 20 storage-delete Lambda with delete access only to `originals/*`,
  `thumbnails/*`, and `processing/*`.
- A Node.js 20 asset-URL Lambda with read access only to `originals/*` and
  `thumbnails/*`; it signs at most 100 current-user keys for 15 minutes.
- API Gateway v2 integrations, protected JWT POST routes, unauthenticated
  OPTIONS preflight routes, and method-scoped Lambda invoke permissions for the
  existing HTTP API.

## Parameters

| Parameter | Required | Default | Purpose |
| --- | --- | --- | --- |
| `ExistingHttpApiId` | Yes | None | Existing API Gateway HTTP API to receive `POST /upload-url`; it must be supplied at deployment. |
| `ExistingJwtAuthorizerId` | Yes | None | Authorizer ID already configured on that HTTP API. |
| `AllowedOrigin` | No | `http://localhost:3000` | Exact browser origin allowed by Lambda responses and S3 CORS. |
| `MetadataApiBaseUrl` | Yes | None | Member D HTTPS base URL used for reservation and processing status calls. |
| `InferenceApiUrl` | Yes | None | Member C HTTPS base URL; the processing client appends `/infer`. |
| `InternalApiKey` | Yes | None (`NoEcho`, minimum length 1) | Non-empty shared internal HTTP credential; provide it only through an approved deployment secret-handling process. |
| `FfmpegLayerArn` | No | Empty | ARN of a compatible layer that exposes `/opt/bin/ffmpeg`; omitted when empty. |
| `MaxUploadBytes` | No | `262144000` | Maximum accepted upload size passed to the upload handler. |

Member C and Member D remain behind the HTTP contracts. The template contains
no invented or environment-specific endpoint value.

## Outputs

| Output | Meaning |
| --- | --- |
| `MediaBucketName` | Generated private S3 bucket name. |
| `UploadFunctionArn` | Upload Lambda ARN. |
| `MediaProcessingFunctionArn` | Processing Lambda ARN. |
| `StorageDeleteFunctionArn` | Guarded storage-delete Lambda ARN for Member D integration. |
| `AssetUrlsFunctionArn` | Authenticated private-asset URL Lambda ARN. |

## Package and tool dependencies

- Node.js 20 for all three Node Lambda test suites.
- Python 3.12 with `pytest`, `PyYAML`, and
  `backend/lambdas/media-processing/requirements.txt` (`Pillow==11.3.0`) for
  local tests.
- `@aws-sdk/client-s3` and `@aws-sdk/s3-request-presigner` from
  `backend/lambdas/upload/package.json`.
- `@aws-sdk/client-s3` from
  `backend/lambdas/storage-delete/package.json`.
- `@aws-sdk/client-s3` and `@aws-sdk/s3-request-presigner` from
  `backend/lambdas/asset-urls/package.json`.
- AWS SAM CLI for `sam validate --lint` and `sam build --use-container`, plus a
  running Docker installation for the container build. The container is
  required because Pillow includes native Lambda dependencies. Lambda's Python
  runtime supplies `boto3`; the separate FFmpeg layer supplies the binary.

Dependency installation is an explicit developer setup step and was not
performed as part of this infrastructure task.

## Local validation and build

Run from the repository root after the documented dependencies are already
available:

```powershell
python -m pytest infrastructure/member-b/test_template.py -q
sam validate --template-file infrastructure/member-b/template.yaml --lint
sam build --use-container --template-file infrastructure/member-b/template.yaml
```

`sam validate --lint` can contact AWS-backed validation depending on SAM CLI
configuration. It does not deploy the stack. If SAM CLI is unavailable, the
PyYAML structural suite is the required local fallback and verifies the
security-sensitive template shape.

Complete local regression commands are in
[`../../docs/member-b/local-testing.md`](../../docs/member-b/local-testing.md).

## Manual prerequisites

Before any deployment, the student must obtain approval and supply:

1. An active AWS Academy session and the course-approved AWS region.
2. The real existing HTTP API ID and its JWT authorizer ID.
3. Reachable HTTPS Member C inference and Member D metadata endpoint values.
4. An approved, architecture-compatible FFmpeg Lambda layer whose executable
   is `/opt/bin/ffmpeg`.
5. The same non-empty internal API key used by Members C and D, supplied
   through the approved secret-handling process, never committed to this
   repository or copied into documentation.
6. Approval to build/package/deploy and permission to capture live evidence.

The complete operator checklist is in
[`../../docs/member-b/manual-aws-steps.md`](../../docs/member-b/manual-aws-steps.md).
