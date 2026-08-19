# Pacific BioArchive

FIT5225 Assignment 2 group project: a multi-cloud serverless wildlife media
platform.

## Target system

The application lets authenticated users upload wildlife images and videos.
After upload, serverless functions detect species, generate tags and thumbnails,
store metadata in a database, and expose REST APIs for tag-based search,
manual tag editing, deletion, and notifications.

## Repository structure

```text
frontend/                 Web UI and Cognito login integration
backend/lambdas/          Serverless functions
infrastructure/           Cloud configuration notes and IaC handoff
docs/                     Architecture, API, and deployment documentation
```

## Current contribution

Member A has added the authentication and authorization module:

- AWS Cognito Hosted UI integration.
- SPA app client configuration with no client secret.
- Authorization code flow with PKCE.
- `/callback` handling and token storage.
- Authenticated API request helper.
- API Gateway JWT authorizer handoff.
- `GET /auth-test` Lambda for verifying protected routes.

Member B has added a locally verified media-ingestion boundary:

- Authenticated S3 upload pre-signing bound to declared length, content type,
  and checksum, with per-user duplicate reservation through Member D's metadata
  contract.
- Private S3 object layout and `originals/`-only event processing.
- Aspect-ratio-preserving image thumbnails with a 40 MP decode ceiling and
  bounded video extraction at exactly one frame per second (840-second timeout,
  900-frame cap).
- Provider-neutral HTTP contracts for Member C inference and Member D metadata
  state, plus guarded storage deletion.
- AWS SAM resources for the media bucket, three Lambda functions, scoped IAM,
  the protected `POST /upload-url`, and unauthenticated browser preflight.

The Member B infrastructure has not been deployed from this repository. Live
AWS, Cognito, FFmpeg-layer, and Member C/D endpoint verification remains in the
manual handoff.

### Member B architecture

```text
Authenticated browser -> POST /upload-url -> private S3 originals/
  -> S3 ObjectCreated event -> media processing Lambda
  -> Member C /infer + Member D processing status
  -> private thumbnails/ and cleaned processing/ frames
```

### Member B verification

Run from the repository root with dependencies already installed:

```powershell
python -m pytest infrastructure/member-b/test_template.py backend/lambdas/media-processing/tests -q
node --test backend/lambdas/upload/test/*.test.mjs backend/lambdas/storage-delete/test/*.test.mjs
Set-Location frontend
node node_modules/vite/bin/vite.js build
Set-Location ..
```

Handoff references:

- [SAM infrastructure and parameters](infrastructure/member-b/README.md)
- [API contracts](docs/member-b/api-contracts.md)
- [Local testing](docs/member-b/local-testing.md)
- [Manual AWS and integration steps](docs/member-b/manual-aws-steps.md)

## Local frontend start

```bash
cd frontend
npm install
npm run dev
```

The current Cognito callback URL is:

```text
http://localhost:3000/callback
```

If the frontend runs on another port, update both Cognito and
`frontend/src/auth/cognitoConfig.js`.
