# Pacific BioArchive — Database & Query API (Member D)

FastAPI service implementing the database schema and all query/data-management
endpoints for the FIT5225 A2 multi-cloud serverless platform. Runs locally with
SQLite (no AWS account needed) and swaps to DynamoDB by changing one env var.

> **对接请先读 [INTEGRATION.md](INTEGRATION.md)** —— 标签命名契约、数据 schema、
> 三个集成插槽（TagDetector / StorageClient / get_current_user）和完整 API 契约都在里面。

## Database

- **Cloud:** AWS **DynamoDB** — table `PacificBioArchiveFiles`, partition key
  `file_id`, region `ap-southeast-2` (same region as Cognito/S3/Lambda).
- **Local:** **SQLite** at `data/pacific_bioarchive.db` — no AWS account needed.

Both backends sit behind the same `FileRepository` interface
(`app/repository/base.py`), so the query/endpoint code is identical — only the
`REPO_BACKEND` env var changes. See `app/config.py`.

## Requirements

- Python 3.12+ (matches the starter package)
- `pip install -r requirements.txt`

## Run locally

```bash
# 1. Seed the local SQLite database (7 demo records)
python seed.py

# 2. Start the API (auto docs at http://localhost:8000/docs)
python -m uvicorn app.main:app --reload --port 8000
```

Reset the demo data by deleting `data/pacific_bioarchive.db` then re-running `seed.py`.

## Endpoints

Public (Cognito-protected, called by the frontend):

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/query/by-tags` | Find files by tags with minimum counts (AND) |
| POST | `/query/by-species` | Find files containing a species |
| GET  | `/query/by-thumbnail?key=...` | Map thumbnail key -> full-size object key |
| POST | `/query/by-file` | Detect tags on an uploaded file, return matches |
| POST | `/tags/edit` | Bulk add/remove tags (`operation` 1=add, 0=remove) |
| POST | `/files/delete` | Bulk delete (database + storage) |
| POST | `/notifications/subscribe` | Subscribe a user to a species tag |
| DELETE | `/notifications/subscribe` | Unsubscribe a user from a species tag |
| GET  | `/notifications/subscriptions?user_id=...` | List a user's subscriptions |
| GET  | `/notifications?user_id=...` | List a user's notifications |

Internal metadata state machine (called by Member B, see
[`docs/member-b/api-contracts.md`](../../docs/member-b/api-contracts.md)):

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/internal/uploads/reserve` | Reserve a unique `(user_id, checksum)` upload (`201`/`409`) |
| POST | `/internal/files/{id}/processing` | Acquire the processing lease (`{"should_process": bool}`) |
| PUT  | `/internal/files/{id}/complete` | Record a completed run (idempotent) |
| PUT  | `/internal/files/{id}/failed` | Record a bounded failure (idempotent, message ≤240 chars) |

`complete` also fires the **notification trigger**: every user subscribed to a
species the file detected (count ≥1) gets a notification record + a
`NotificationPublisher.publish` call. Subscriptions and notifications live in
their own tables (`subscriptions` / `notifications`), same SQLite/DynamoDB swap.

Import `postman_collection.json` into Postman for ready-made requests, or replay
the JSON fixtures in `events/` with `curl` (see `events/README.md`).

## Run tests

```bash
python -m pytest tests/ -v
```

## Switching to DynamoDB (cloud)

```bash
pip install boto3
export REPO_BACKEND=dynamodb
export DYNAMODB_TABLE=PacificBioArchiveFiles
export AWS_REGION=ap-southeast-2
```

The query API code is unchanged — only the repository backend swaps
(`app/config.py` -> `_build_repository()` in `app/main.py`).

## Wiring to other members

- **Member C (ML):** replace `StubTagDetector` with an adapter around the
  MegaDetector + SpeciesNet pipeline (`app/tag_detector.py`). Output tags must
  use the team short species name (`app/species.py`).
- **Member B (storage):** replace `StubStorageClient` with the guarded
  storage-delete Lambda invocation (`app/storage_client.py`, takes S3 **keys**).
  Member B also writes metadata through the internal endpoints above, not by
  importing this repo directly.
- **Member A (auth):** replace the body of `get_current_user` in `app/main.py`
  with `build_get_current_user()` from `examples/cognito_auth_example.py`
  (real Cognito params already filled in) — every public route already depends on it.
- **Member E (notifications):** replace `StubNotificationPublisher` with a real
  SNS/email/push implementation. This repo now includes an SNS publisher that
  is enabled with `NOTIFICATION_PUBLISHER=sns` plus either `SNS_TOPIC_ARN` or
  `SNS_TOPIC_ARN_TEMPLATE`. Member D owns the trigger + durable records +
  subscription/notification endpoints; Member E owns the delivery UX on top.

## Cloud database setup (for the AWS operator)

Three DynamoDB tables (`PacificBioArchiveFiles`, `PacificBioArchiveSubscriptions`,
`PacificBioArchiveNotifications`) and the Lambda IAM role are declared in
[`infrastructure/member-d/dynamodb.yaml`](../../infrastructure/member-d/dynamodb.yaml).
The step-by-step operator guide (which file to read, what to run, and the env
vars to pass to the API) lives in
[`docs/member-d/database-setup.md`](../../docs/member-d/database-setup.md); the
failure-mode reference is
[`docs/member-d/troubleshooting.md`](../../docs/member-d/troubleshooting.md).
