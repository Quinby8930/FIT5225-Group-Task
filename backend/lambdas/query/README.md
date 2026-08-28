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

# 2. Explicitly select local-only adapters. Omitting these values fails closed.
export STORAGE_BACKEND=stub
export TAG_DETECTOR_BACKEND=stub

# 3. Start the API (auto docs at http://localhost:8000/docs)
python -m uvicorn app.main:app --reload --port 8000
```

Reset the demo data by deleting `data/pacific_bioarchive.db` then re-running `seed.py`.

## Endpoints

Public (Cognito-protected, called by the frontend):

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/query/by-tags` | Find completed files by tags with minimum counts (AND) |
| POST | `/query/by-species` | Find completed files containing a species |
| GET  | `/query/by-thumbnail?key=...` | Map thumbnail key or trusted S3 HTTPS URL -> full-size object key plus structured `item` metadata |
| POST | `/query/by-file` | Detect tags on an uploaded file, return matches |
| POST | `/tags/edit` | Bulk add/remove tags (`operation` 1=add, 0=remove) |
| POST | `/files/delete` | Bulk delete (database + storage) |
| POST | `/notifications/subscribe` | Subscribe a user to a species tag |
| DELETE | `/notifications/subscribe` | Unsubscribe a user from a species tag |
| GET  | `/notifications/subscriptions?user_id=...` | List a user's subscriptions |
| GET  | `/notifications?user_id=...` | List a user's notifications |

Query responses retain the legacy `results` and `count` fields and add `items`.
Each item contains only `file_id`, `file_type`, `display_key`, `original_key`,
`thumbnail_key`, `can_preview`, and `can_manage`; `can_manage` is true only
for the authenticated owner. Tag edit/delete accept legacy `keys` and `urls`;
either field may be omitted, but at least one reference is required. URLs accept
only canonical archive keys or trusted HTTPS URLs for `QUERY_INPUT_BUCKET` and
are normalized before use.

Internal metadata state machine (called by Member B, see
[`docs/member-b/api-contracts.md`](../../docs/member-b/api-contracts.md)):

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/internal/uploads/reserve` | Atomically reserve or reuse `(user_id, checksum)` (`201`/`409`) |
| POST | `/internal/files/{id}/processing` | Atomically acquire lease (`should_process` + `state`) |
| PUT  | `/internal/files/{id}/complete` | Record a completed run (idempotent) |
| PUT  | `/internal/files/{id}/failed` | Record a bounded failure (idempotent, message ≤240 chars) |

`complete` also fires the **notification trigger**: every user subscribed to a
species the file detected (count ≥1) gets a notification record + a
`NotificationPublisher.publish` call. Deterministic inbox rows are idempotently
persisted pending before the completed transition. Failed publish/state updates
are logged; completed replay publishes only inbox rows that already remain
pending. It does not re-read current subscriptions or notify late subscribers.
There is no periodic worker/DLQ: recovery uses automatic/manual complete replay,
with at-least-once delivery if SNS succeeded before state persistence failed.

Import `postman_collection.json` into Postman for ready-made requests, or replay
the JSON fixtures in `events/` with `curl` (see `events/README.md`).

## Run tests

```bash
python -m pytest tests/ -v
```

## Production adapter contract

The SAM template sets `REPO_BACKEND=dynamodb`, `STORAGE_BACKEND=lambda`, and
`TAG_DETECTOR_BACKEND=remote`. Storage deletion synchronously invokes Member B's
guarded-delete Lambda before metadata is removed. File queries accept only
JPEG/PNG/WebP up to 4,194,304 bytes, stage the image privately under
`query-inputs/`, give Member C a 120-second HTTPS GET URL, and call C with a
25-second no-redirect HTTP timeout inside a 30-second Lambda. Cleanup is
attempted after every S3 put attempt. Missing production configuration returns
503; it never falls back to a fake deletion or fake `dingo` result.

The query API code is unchanged — only the repository backend swaps
(`app/config.py` -> `_build_repository()` in `app/main.py`).

## Wiring to other members

- **Member C (ML):** `RemoteTagDetector` calls `/infer` using the private staged
  image contract. Output tags use the team short species name.
- **Member B (storage):** `LambdaStorageClient` invokes the guarded
  storage-delete Lambda with the owning user and S3 **keys**.
  Member B also writes metadata through the internal endpoints above, not by
  importing this repo directly.
- **Member A (auth):** `app/auth.py` resolves the verified API Gateway JWT
  `sub` in Lambda and verifies Cognito bearer tokens during local development;
  every public route already depends on it.
- **Member D (notifications):** the module already includes the DynamoDB inbox,
  trigger, SNS publisher, and subscription/notification endpoints. Member A's
  SAM deployment enables it with `NOTIFICATION_PUBLISHER=sns` and the exact
  `SNS_TOPIC_ARN`; an optional email endpoint can subscribe to the topic.
- **Member E (experience):** owns the frontend and in-app notification UX on
  top of Member D's API; E does not need to implement the SNS publisher.

## Cloud database setup (for the AWS operator)

Four DynamoDB tables (`PacificBioArchiveFiles`, `PacificBioArchiveUploadReservations`,
`PacificBioArchiveSubscriptions`, `PacificBioArchiveNotifications`), the SNS topic,
and the Lambda IAM role are declared in
[`infrastructure/member-d/dynamodb.yaml`](../../infrastructure/member-d/dynamodb.yaml).
The step-by-step operator guide (which file to read, what to run, and the env
vars to pass to the API) lives in
[`docs/member-d/database-setup.md`](../../docs/member-d/database-setup.md); the
failure-mode reference is
[`docs/member-d/troubleshooting.md`](../../docs/member-d/troubleshooting.md).
Existing retained file rows require Member A to pause every Files/Reservations
mutation (reserve, processing/complete/failed callbacks, and delete) during the
`migrate_reservations.py` verify/backfill/verify cutover; runtime fallback is not
a migration substitute.
