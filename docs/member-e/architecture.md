# Member E Architecture Notes

## UI Responsibilities

Member E owns the browser console and the user-facing integration across
Members A, B and D:

- Cognito Hosted UI sign-up, sign-in, sign-out and Google external login entry.
- Protected API calls with the Cognito ID token.
- Media upload through Member B's `POST /upload-url` and pre-signed S3 PUT.
- Private thumbnail and original access through Member B's authenticated
  `POST /asset-urls` and short-lived S3 GET URLs.
- Query, thumbnail lookup, by-file query, tag edit, delete, subscription and
  notification calls against Member D's API.
- Local build/test scripts and demo documentation.
- Optional SNS notification delivery through `NOTIFICATION_PUBLISHER=sns`.

## Runtime Diagram

```mermaid
flowchart LR
  User[Authenticated user] --> UI[React Vite frontend]
  UI --> Cognito[AWS Cognito Hosted UI]
  Cognito --> UI
  UI --> Upload[API Gateway POST /upload-url]
  Upload --> S3[AWS S3 originals bucket]
  UI --> Assets[API Gateway POST /asset-urls]
  Assets --> S3
  S3 --> Processor[AWS Lambda media processing]
  Processor --> ML[Secondary cloud /infer]
  Processor --> Metadata[Member D Query API]
  UI --> Metadata
  Metadata --> DynamoDB[AWS DynamoDB metadata tables]
  Metadata --> Notify[Subscriptions and notification records]
  Notify --> SNS[AWS SNS email topic]
  Notify --> Inbox[Frontend notification inbox]
```

## Report Contribution Text

Member E implemented the React frontend console, Cognito Hosted UI entry points,
authenticated API client, checksum-based upload workflow, private asset URL
resolution, query views, thumbnail-to-original lookup, file-based query form,
bulk tag editing, bulk deletion, subscription and notification inbox, signed
asset URL refresh/retry handling, local Node mocked contract/integration-flow tests (not browser E2E), CORS integration, and
user/demo documentation.
