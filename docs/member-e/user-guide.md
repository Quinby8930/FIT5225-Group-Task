# Member E User Guide

## Start The Local Demo

1. Start Member D locally:

```powershell
cd backend/lambdas/query
python seed.py
python -m uvicorn app.main:app --reload --port 8000
```

2. Start the frontend:

```powershell
cd frontend
npm install
$env:VITE_API_BASE_URL="http://localhost:8000"
npm run dev
```

3. Open `http://localhost:3000`.

## Demo Flow

1. Create an account or sign in through Cognito Hosted UI.
2. Upload an image or video. The UI computes a SHA-256 checksum, requests
   `POST /upload-url`, and uploads to the returned pre-signed URL.
3. Query by tag counts, for example `dingo:1, wombat:1`.
   The UI exchanges server-approved completed-media keys through authenticated
   `POST /asset-urls` calls so private thumbnails can be previewed without
   making the bucket public. Temporary links refresh automatically while the
   results remain open; only the owner can edit tags or delete a result.
4. Query by species, for example `wombat`.
5. Paste a thumbnail key and resolve the original file key.
6. Upload a query image in the Query tab to search by detected tags without
   storing the query file.
7. Select result keys and bulk add or remove tags.
8. Delete selected result keys.
9. Subscribe to a species tag and refresh the inbox after a matching file
   completes processing.

## Evidence Checklist

- Screenshot of Cognito login or Google external login.
- Screenshot of successful upload receipt including `file_id` and checksum.
- Screenshot of query results with thumbnail cards or object keys.
- Screenshot of bulk tag edit success.
- Screenshot of bulk delete success.
- Screenshot of subscription and notification inbox.
- Output of `npm test`.
- Output of `npm run build`.
- Demo walkthrough: `docs/member-e/demo-script.md`.

## Final Cloud Address

Member E only needs the final API base URL from Member A:

```text
VITE_API_BASE_URL=https://2dd2aqb32j.execute-api.ap-southeast-2.amazonaws.com/dev
```

Do not set `VITE_ASSET_BASE_URL`. Query result previews and full-image/video
links use `POST /asset-urls` on the same API Gateway base URL.

## SNS Notification Delivery

The default remains `stub`. An existing deployment configured with the legacy
`sns` value retains its previous static Topic/template behavior, so deploying
the package alone does not switch notification mode.

Available modes are:

```text
NOTIFICATION_PUBLISHER=stub
NOTIFICATION_PUBLISHER=shared_demo
NOTIFICATION_PUBLISHER=per_user
```

The single-inbox course demonstration mode uses:

```text
NOTIFICATION_PUBLISHER=shared_demo
SNS_TOPIC_ARN=arn:aws:sns:ap-southeast-2:<account-id>:<topic-name>
```

Per-user course-demo email uses:

```text
NOTIFICATION_PUBLISHER=per_user
SNS_USER_TOPIC_ARN_PREFIX=arn:aws:sns:ap-southeast-2:<account-id>:pba-user-
```

The subscribe request remains `{"species":"wombat"}`. The backend takes
`sub`, `email`, and `email_verified` only from API Gateway JWT claims, hashes
`sub` into `pba-user-<sha256>`, reuses an existing pending/confirmed email
subscription, and asks SNS to send a confirmation email only when none exists.
Email, user ID, and Topic ARN are never accepted from the browser. A per-user
SNS failure never falls back to the shared demo Topic.

The Query Lambda execution role needs only these SNS actions on
`arn:aws:sns:ap-southeast-2:<account-id>:pba-user-*`:

- `sns:CreateTopic`
- `sns:Subscribe` with `sns:Protocol` restricted to `email`
- `sns:ListSubscriptionsByTopic`
- `sns:Publish`

It does not need `SetSubscriptionAttributes`, `Unsubscribe`, `DeleteTopic`, a
new DynamoDB table, or a new Lambda.

### Course-demo limitations

This bounded implementation intentionally does not solve concurrent first
subscriptions, automatic Cognito email migration, SNS/DynamoDB atomicity,
automatic Topic cleanup, leases, reconciliation, or a compensation worker.
Species unsubscribe removes only the existing `(user_id, species)` record.
The durable in-app inbox remains the source of record and email delivery keeps
the existing at-least-once retry semantics.
