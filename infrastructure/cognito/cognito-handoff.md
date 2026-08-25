# Pacific BioArchive Cognito Handoff

## Owner

Member A owns authentication and authorization.

## Cognito resources

```text
AWS Region: ap-southeast-2
User Pool ID: ap-southeast-2_1hGEJyYO7
Cognito Domain: https://ap-southeast-21hgejyyo7.auth.ap-southeast-2.amazoncognito.com
SPA App Client Name: PacificBioArchive-SPA
SPA App Client ID: 65dgspco2djehpbpunc13t2oml
Callback URL: http://localhost:3000/callback
Sign-out URL: http://localhost:3000/logout
Issuer URL: https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_1hGEJyYO7
JWKS URL: https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_1hGEJyYO7/.well-known/jwks.json
Scopes: openid, email, profile
Login identifier: email
Required sign-up attributes: given_name, family_name
```

The SPA app client intentionally has no client secret. Browser code must not
contain a Cognito client secret.

## Frontend contract

The frontend should use `frontend/src/auth/cognitoAuth.js` to:

1. Redirect users to Cognito Hosted UI for sign-in and sign-up.
2. Handle `/callback?code=...` after Cognito redirects back.
3. Exchange the authorization code for tokens using PKCE.
4. Store tokens locally.
5. Send the ID token to protected APIs.
6. Redirect users through the Cognito logout endpoint for sign-out.

Protected API calls must include:

```http
Authorization: Bearer <id_token>
```

## HTTP API authorizer

```text
HTTP API name: PacificBioArchive-HTTP-API
HTTP API ID: 2dd2aqb32j
Authorizer name: CognitoJWTAuthorizer
Authorizer type: JWT
Identity source: $request.header.Authorization
Issuer: https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_1hGEJyYO7
Audience: 65dgspco2djehpbpunc13t2oml
```

Routes that should be protected:

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
GET /auth-test
```

Internal metadata routes used by Member B's Lambda should not use the browser
Cognito authorizer:

```text
POST /internal/uploads/reserve
POST /internal/files/{file_id}/processing
PUT /internal/files/{file_id}/complete
PUT /internal/files/{file_id}/failed
```

## Backend contract

After API Gateway verifies the token, Lambda can read the authenticated user
from:

```js
event.requestContext.authorizer.jwt.claims
```

Use the following claim as the database user identifier:

```text
sub
```

The backend can also read:

```text
email
given_name
family_name
```

## Demo evidence

Member A should capture screenshots for:

1. Cognito User Pool overview.
2. SPA app client showing no client secret.
3. Cognito domain.
4. Hosted UI sign-up page.
5. Email verification code.
6. Successful login redirect to `/callback?code=...`.
7. Hosted UI showing one external identity provider, such as Google.
8. HTTP API JWT authorizer.
9. A protected route returning `401` without a token.
10. The same route returning `200` with a Cognito token.
