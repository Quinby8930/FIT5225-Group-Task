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
