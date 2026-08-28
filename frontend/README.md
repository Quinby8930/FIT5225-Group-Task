# Pacific BioArchive Frontend

This React/Vite app provides the Pacific BioArchive public landing page and
authenticated archive workspace.

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

The dev server binds port 3000 with `--strictPort`: if the port is occupied it
fails loudly instead of silently moving to another port. Cognito only accepts
the registered exact callback URL, so a silently drifted port would break
login with a hosted-UI error. Free port 3000 first (close any stale Vite
process); if the port is genuinely unavailable, register the new port's exact
callback and sign-out URLs in the Cognito app client instead of improvising.

The Cognito app client must allow `http://localhost:3000/callback` as a callback
URL and `http://localhost:3000/logout` as a sign-out URL.

## Demo diagnostics

The signed-in shell hides demo diagnostics (session `sub`, **Check auth**) by
default. They appear automatically in `npm run dev`, and on any build by
appending `?demo=1` to the URL, for example
`https://quinby8930.github.io/FIT5225-Group-Task/?demo=1`.

## Deploy to GitHub Pages

The frontend is deployed from `main` by the GitHub Actions workflow in
`.github/workflows/deploy-frontend.yml`. The public site is:

```text
https://quinby8930.github.io/FIT5225-Group-Task/
```

GitHub Pages must use **GitHub Actions** as its source in the repository
settings. The production build uses the repository subpath:

```powershell
npm run build -- --base=/FIT5225-Group-Task/
```

The Cognito app client must also allow these GitHub Pages URLs:

```text
Callback URL: https://quinby8930.github.io/FIT5225-Group-Task/callback
Sign-out URL: https://quinby8930.github.io/FIT5225-Group-Task/logout
```

Keep the localhost callback and sign-out URLs as well so local testing still
works.

## Visual assets and regional context

The public landing page and signed-in Home view use locally served habitat
photographs so the interface does not depend on third-party image hosts. The
Pacific Coast map is an illustrative summary of the assignment scenario, not
live archive coverage or a species-range claim. Explore's suggested species
cards are explicitly described as inference-label examples rather than usage
or popularity data. Source links and licences are recorded in
[`IMAGE_CREDITS.md`](./IMAGE_CREDITS.md).

Every push to `main` runs the frontend tests, builds with the repository base
path, and publishes the resulting static site automatically. AWS services are
not deployed by this workflow. The existing API Gateway must allow the browser
origin below (an origin never includes the repository path):

```text
https://quinby8930.github.io
```

Member B and D infrastructure templates keep localhost support and expose a
`PublicAllowedOrigin` parameter with this production default. Because the HTTP
API itself is shared and supplied through `ExistingHttpApiId`, its global CORS
configuration remains a one-time AWS deployment setting rather than a resource
owned by either template.

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
