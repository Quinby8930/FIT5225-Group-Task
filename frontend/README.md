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

By default, the deployed frontend calls:

```text
https://2dd2aqb32j.execute-api.ap-southeast-2.amazonaws.com/dev
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

Both login and sign-up use Cognito's authorization-code flow with PKCE S256.
The callback shares one token exchange for repeated handling of the same code
(including React StrictMode effect replay). A failed exchange keeps the PKCE
transaction so the same callback can be retried; a successful exchange stores
the tokens before removing that transaction. Auth URL and callback helpers use
the runtime Cognito configuration by default; their narrow optional config
argument exists so unit tests can assert protocol behavior against placeholders
without coupling tests to a deployed client ID or domain.

## Authorization behavior

Archive queries remain global, so another user's result key can be shown as
read-only metadata. Only canonical `originals/{sub}/{file_id}/{filename}` and
`thumbnails/{sub}/{file_id}/thumbnail.jpg` keys for the signed-in user are
selectable or submitted for tag edits and deletion. Foreign or malformed keys
are excluded with an on-screen status, and private preview URLs are requested
only for current-user keys. The backend remains the authoritative ownership
boundary.

Subscription and notification requests do not send a `user_id`; Member D
derives the identity from the verified Cognito token. API failures are exposed
as structured `ApiError` values with their HTTP status, backend error code, and
response payload. A 401 response clears locally stored tokens.

## Verify

```powershell
npm test
npm run build
```

The E2E-style test mocks browser `fetch` and checks the real request shapes for
upload pre-signing, S3 PUT, private asset URL signing, tag query, bulk tag edit,
deletion, and identity-free subscription/inbox operations. Focused Node tests
cover PKCE/callback replay, structured API failures, and owner-key filtering.
