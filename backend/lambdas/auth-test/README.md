# Auth Test Lambda

This Lambda proves that API Gateway is passing verified Cognito JWT claims to
the backend.

Runtime:

```text
Node.js 20.x
```

Handler:

```text
index.handler
```

Suggested HTTP API route:

```text
GET /auth-test
```

Protect this route with:

```text
CognitoJWTAuthorizer
```

Expected result:

- Without an `Authorization` header, API Gateway returns `401 Unauthorized`.
- With `Authorization: Bearer <id_token>`, Lambda returns the authenticated
  user's Cognito claims.
