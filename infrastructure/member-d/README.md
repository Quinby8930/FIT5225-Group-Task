# Member D AWS infrastructure

This directory contains the Member D SAM stack: four DynamoDB tables, an SNS
topic, the query Lambda, its least-privilege adapter policies, and explicit
routes on the existing HTTP API.

The query application code lives in `../../backend/lambdas/query/`; the operator
setup guide (which single file to read) is in
[`../../docs/member-d/database-setup.md`](../../docs/member-d/database-setup.md).

## Resources

- **`PacificBioArchiveFiles`** — DynamoDB table, partition key `file_id`
  (String), `PAY_PER_REQUEST` billing (no capacity planning for this dataset).
- **`PacificBioArchiveUploadReservations`** — checksum claims, partition key
  `reservation_key`; a transaction writes this claim and the file row together.
- **`PacificBioArchiveSubscriptions`** — DynamoDB table, partition key `user_id`
  + sort key `species` (one subscription per user/species).
- **`PacificBioArchiveNotifications`** — DynamoDB table, partition key `user_id`
  + sort key `notification_id` (durable inbox plus pending/delivered state).
- **`NotificationTopic`** — shared SNS delivery topic. Set the optional
  `NotificationEmailEndpoint` parameter to create a conditional email subscription.
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

Member A performs the guided deployment for all AWS B/D resources. It requires
the existing HTTP API and JWT authorizer IDs, the A-managed Member B private
bucket and storage-delete function name, Member C's Alibaba Cloud HTTPS base
URL, and a non-empty shared internal key. `NotificationEmailEndpoint` may be
left empty. For `StorageDeleteFunctionName`, Member A uses Member B's
`StorageDeleteFunctionName` output, never its ARN output. Do not save a live
key in Git or use a fake/default endpoint.

## Outputs

| Output | Meaning |
|--------|---------|
| `TableName` | DynamoDB table the query API reads/writes (`PacificBioArchiveFiles`). |
| `ReservationsTableName` | Atomic `(user_id, checksum)` claims. |
| `SubscriptionsTableName` | Table holding (user, species) subscriptions. |
| `NotificationsTableName` | Table holding per-user notifications. |
| `QueryFunctionArn` | Deployed Member D query Lambda. |
| `NotificationTopicArn` | SNS topic used by the notification publisher. |

No live AWS deployment was performed while preparing these artifacts; account
validation and deployment remain manual steps for Member A. Member C only
deploys Alibaba Cloud; B/D do not configure AWS. The shared internal key is
configured only by A/C through a secure channel and never placed in Git or chat.

For an existing retained `FilesTable`, Member A must pause every mutation of
the Files and Reservations tables (reserve, processing/complete/failed callbacks,
and delete) and run
`backend/lambdas/query/migrate_reservations.py` in verify, backfill with
`--confirm-uploads-paused` (the legacy-named flag confirms all those mutations are
paused), then verify order. Runtime fallback claiming is a
fail-closed guard, not proof that migration completed.
