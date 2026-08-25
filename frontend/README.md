# Member E Frontend

This React/Vite app is the Member E deliverable for the Pacific BioArchive UI
and integration workflow.

## Configuration

Create `frontend/.env.local` when using non-default endpoints:

```text
VITE_API_BASE_URL=http://localhost:8000
VITE_COGNITO_REGION=ap-southeast-2
VITE_COGNITO_USER_POOL_ID=ap-southeast-2_1hGEJyYO7
VITE_COGNITO_CLIENT_ID=65dgspco2djehpbpunc13t2oml
VITE_COGNITO_DOMAIN=https://ap-southeast-21hgejyyo7.auth.ap-southeast-2.amazoncognito.com
VITE_COGNITO_REDIRECT_SIGN_IN=http://localhost:3000/callback
VITE_COGNITO_REDIRECT_SIGN_OUT=http://localhost:3000/logout
```

The S3 bucket remains private. Current-user S3 keys returned by Member D are
deduplicated, split into batches of 100, and exchanged for 15-minute HTTPS URLs
through Member B's authenticated `POST /asset-urls` endpoint. Displayed URLs
refresh shortly before expiry. Results owned by another user remain visible as
metadata but receive no private preview URL. No public bucket or
`VITE_ASSET_BASE_URL` is required.

## Run

```powershell
npm install
npm run dev
```

The Cognito app client must allow `http://localhost:3000/callback` as a callback
URL and `http://localhost:3000/logout` as a sign-out URL.

## Verify

```powershell
npm test
npm run build
```

The E2E-style test mocks browser `fetch` and checks the real request shapes for
upload pre-signing, S3 PUT, private asset URL signing, tag query, bulk tag edit,
deletion, and subscription creation.
