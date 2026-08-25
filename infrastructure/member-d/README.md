# Member D AWS infrastructure

This directory contains the Member D SAM stack: three DynamoDB tables, the
query Lambda, its least-privilege adapter policies, and explicit routes on the
existing HTTP API.

The query application code lives in `../../backend/lambdas/query/`; the operator
setup guide (which single file to read) is in
[`../../docs/member-d/database-setup.md`](../../docs/member-d/database-setup.md).

## Resources

- **`PacificBioArchiveFiles`** — DynamoDB table, partition key `file_id`
  (String), `PAY_PER_REQUEST` billing (no capacity planning for this dataset).
- **`PacificBioArchiveSubscriptions`** — DynamoDB table, partition key `user_id`
  + sort key `species` (one subscription per user/species).
- **`PacificBioArchiveNotifications`** — DynamoDB table, partition key `user_id`
  + sort key `notification_id` (the notification trigger writes here).
- **`QueryFunction`** — Python 3.12/Mangum API with DynamoDB, private
  `query-inputs/*` staging, and Member B guarded-delete invocation access.
- **HTTP API routes** — JWT on every public route; `NONE` only on explicit
  internal and OPTIONS routes. Internal routes remain protected by the shared
  application key. There is no `$default` route.

## Deploy

```bash
sam build --template-file infrastructure/member-d/dynamodb.yaml
sam deploy --guided
```

The guided deployment requires the existing HTTP API and JWT authorizer IDs,
Member B private bucket and storage-delete function name, Member C HTTPS base
URL, and a non-empty shared internal key. Do not save a live key in Git or use a
fake/default endpoint.

## Outputs

| Output | Meaning |
|--------|---------|
| `TableName` | DynamoDB table the query API reads/writes (`PacificBioArchiveFiles`). |
| `SubscriptionsTableName` | Table holding (user, species) subscriptions. |
| `NotificationsTableName` | Table holding per-user notifications. |
| `QueryFunctionArn` | Deployed Member D query Lambda. |

No live AWS deployment was performed while preparing these artifacts; account
validation and deployment remain manual steps for the AWS operator.
