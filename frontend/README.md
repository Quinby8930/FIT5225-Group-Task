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

The S3 bucket remains private. Structured completed-media results are normalized
by `file_id`, and their preview keys are split into batches of at most 100 for
Member B's authenticated `POST /asset-urls` endpoint. Any authenticated user
may preview a completed item when the server returns `can_preview: true`; only
server-provided `can_manage: true` items can be selected for mutation. Signed
URLs remain in memory, refresh before expiry, and are never written to browser
storage. No public bucket or `VITE_ASSET_BASE_URL` is required.

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

Archive queries remain global. The client treats the structured `items` contract
as authoritative: it never infers media type, preview permission, or management
permission from keys. Legacy `results` values are shown as non-previewable,
non-manageable references. Mutation requests are derived only from the selected
current structured items' `original_key` values where `can_manage === true`.
The backend remains the authoritative ownership boundary.

Subscription and notification requests do not send a `user_id`; Member D
derives the identity from the verified Cognito token. API failures are exposed
as structured `ApiError` values with their HTTP status, backend error code, and
response payload. A 401 response clears locally stored tokens.

## Verify

```powershell
npm test
npm run build
```

The Node mocked contract/integration flow test mocks `fetch` and checks request shapes for upload
pre-signing, S3 PUT, asset URL signing, tag query, bulk tag edit, deletion, and
identity-free subscription/inbox operations; it is not a real browser E2E test.
Focused Node tests cover PKCE/callback replay, structured API failures, query
normalization, thumbnail lookup's structured `item`, selection-to-mutation mapping, and per-key URL state handling. Browser viewport, focus, and keyboard checks at 390/768/1440 remain deployment-time manual verification.
