# Team Integration Guide

## Member A deliverables

Member A provides the following code and configuration:

- Cognito Hosted UI login and logout flow.
- SPA authorization code flow with PKCE.
- Callback handling for `/callback?code=...`.
- Token storage and JWT claim parsing.
- Protected API request helper.
- API Gateway JWT authorizer configuration.
- Test Lambda for proving authorization works.

## Files for Member E

Frontend owner should copy or adapt:

```text
frontend/src/auth/cognitoConfig.js
frontend/src/auth/cognitoAuth.js
frontend/src/auth/AuthCallback.jsx
frontend/src/auth/AuthControls.jsx
frontend/src/api/apiClient.js
frontend/src/App.jsx
```

Required frontend behavior:

1. Show a sign-in button that calls `signIn()`.
2. Add a `/callback` route that renders `AuthCallback`.
3. Show a sign-out button that calls `signOut()`.
4. Call backend APIs through `apiRequest()` so the ID token is included.

If the frontend runs on a port other than `3000`, update both Cognito and
`cognitoConfig.js`.

## Files for Members B and D

Backend owners should use:

```text
backend/lambdas/auth-test/index.mjs
backend/lambdas/auth-test/README.md
```

They should add a test route first:

```text
GET /auth-test
```

Then protect it with:

```text
CognitoJWTAuthorizer
```

After that, protect all real public application routes:

```text
POST /upload-url
POST /query/by-tags
POST /query/by-species
GET /query/by-thumbnail
POST /query/by-file
POST /tags/edit
POST /files/delete
POST /notifications/subscribe
DELETE /notifications/subscribe
GET /notifications/subscriptions
GET /notifications
```

Do not attach the browser Cognito authorizer to Member D's internal metadata
routes. Those routes are called by Member B's Lambda workflow, not by the
frontend:

```text
POST /internal/uploads/reserve
POST /internal/files/{file_id}/processing
PUT /internal/files/{file_id}/complete
PUT /internal/files/{file_id}/failed
```

## Proof for marking

Member A should commit these files and include screenshots showing:

1. The SPA app client has no client secret.
2. Cognito Hosted UI can register a user.
3. Email verification is received.
4. Login redirects to `/callback?code=...`.
5. Google, or another external identity provider, appears on the Hosted UI.
6. HTTP API has `CognitoJWTAuthorizer`.
7. A protected route rejects requests without a token.
8. The same route accepts requests with a token.
