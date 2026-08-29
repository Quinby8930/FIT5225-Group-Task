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

Local development uses the stub publisher and the frontend inbox. In AWS, set:

```text
NOTIFICATION_PUBLISHER=sns
SNS_TOPIC_ARN=arn:aws:sns:ap-southeast-2:<account-id>:<topic-name>
```

For per-user SNS topics, use:

```text
NOTIFICATION_PUBLISHER=sns
SNS_TOPIC_ARN_TEMPLATE=arn:aws:sns:ap-southeast-2:<account-id>:bioarchive-{user_id}
```

The publisher includes `user_id` and `species` as SNS message attributes and
the full notification payload as JSON.
