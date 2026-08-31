# Member D AWS infrastructure

This directory contains the Member D SAM stack: four DynamoDB tables, an SNS
topic, the query Lambda, its least-privilege adapter policies, and explicit
routes on the existing HTTP API.

The query application code lives in `../../backend/lambdas/query/`; the deployment
entry guide is in
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

## Choose the deployment path

### Fresh ordinary AWS account

Use normal SAM deployment only when the account has no existing Member D query
Lambda, integration, routes, or tables:

```bash
sam build --template-file infrastructure/member-d/dynamodb.yaml
sam deploy --guided
```

在 guided prompt 的 `Save arguments to configuration file` 处回答 `N`；不要把共享内部
key 保存到 `samconfig.toml`。

Member A performs the guided deployment for all AWS B/D resources. It requires
the existing HTTP API and JWT authorizer IDs, the A-managed Member B private
bucket and storage-delete function name, Member C's Alibaba Cloud HTTPS base
URL, and a non-empty shared internal key. `NotificationEmailEndpoint` may be
left empty. For `StorageDeleteFunctionName`, Member A uses Member B's
`StorageDeleteFunctionName` output, never its ARN output. Do not save a live
key in Git or use a fake/default endpoint.

### Existing project account

The current account already has an unmanaged live reservations table, Query
Lambda, one integration, and sixteen Member D non-OPTIONS routes. The
stack-managed Query Lambda role also has the narrowly audited reservation-only
permission drift described in the runbook. Do **not** run `sam deploy --guided`
in that account before adoption. Follow
[`../../docs/member-d/aws-resource-adoption.md`](../../docs/member-d/aws-resource-adoption.md)
to import exactly those nineteen resources, pass the first explicit execution
approval, verify runtime continuity, then review and separately approve the
normal UPDATE that reconciles the role. That first UPDATE keeps legacy callbacks
temporarily enabled until the latest Member B deployment is proven to forward
lease tokens. Legacy broad Lambda permission removal, reservations backfill,
Member B deployment, and the final UPDATE to disable compatibility are separate
reviewed and approved writes; see the runbook for the exact order.

If the stack is `UPDATE_ROLLBACK_COMPLETE` because a RouteKey already exists,
never retry the same template and never delete the live route, integration,
function, or stack to clear the conflict.

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
