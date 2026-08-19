# Member D AWS infrastructure

This directory declares Member D's database resources: a single DynamoDB table
for file metadata plus the IAM role the query Lambda assumes. It is owned by
Member D and deployed in `ap-southeast-2` alongside Cognito, S3, and the other
Lambdas.

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
- **`PacificBioArchive-QueryLambdaRole`** — IAM role for the query Lambda,
  granting `dynamodb:PutItem / GetItem / Scan / Query / UpdateItem / DeleteItem`
  on those three tables, plus the standard Lambda execution policy.

## Deploy

```bash
aws cloudformation deploy \
  --template-file infrastructure/member-d/dynamodb.yaml \
  --stack-name PacificBioArchive-Database \
  --region ap-southeast-2 \
  --capabilities CAPABILITY_NAMED_IAM
```

## Outputs

| Output | Meaning |
|--------|---------|
| `TableName` | DynamoDB table the query API reads/writes (`PacificBioArchiveFiles`). |
| `SubscriptionsTableName` | Table holding (user, species) subscriptions. |
| `NotificationsTableName` | Table holding per-user notifications. |
| `QueryLambdaRoleArn` | Attach this role to the query Lambda. |

No live AWS deployment was performed while preparing these artifacts; account
validation and deployment remain manual steps for the AWS operator.
