# Report Section — Database & Query API (Member D)

## Contribution table entry (≤100 words)

> **Database & Query API.** Designed the file-metadata schema (file ID, user ID,
> type, S3 object/thumbnail keys, species tags with counts, detections, checksum,
> processing status, upload time) behind a repository abstraction that runs on
> SQLite locally and DynamoDB in the cloud. Built the query layer (tag AND queries
> with minimum counts, species lookup, thumbnail-to-original mapping, file-based
> querying, bulk tag edit, bulk delete) plus the internal metadata state machine
> (reserve / processing lease / complete / failed) consumed by Member B, and the
> subscription/notification model (subscribe/unsubscribe/list + a notification
> trigger that fires when a completed file matches a user's subscribed species).
> Wrote 30 passing tests and a Postman collection.

## Technical description

### Database schema

One record per media file (DynamoDB table `PacificBioArchiveFiles`, partition
key `file_id`; mirrored locally in SQLite for no-AWS development):

| Field | Type | Notes |
|-------|------|-------|
| `file_id` | String (PK) | UUID generated at ingestion |
| `user_id` | String | Owner, from Cognito `sub` |
| `file_type` | String | `image` or `video` |
| `object_key` | String | Full-size object S3 key |
| `thumbnail_key` | String | Present for images only |
| `tags` | Map(String→Number) | Species short name → count, e.g. `{"dingo": 2}` |
| `detections` | List | `[{"species": "wombat", "confidence": 0.94}]` |
| `checksum` | String | SHA-256; `(user_id, checksum)` unique — dedup guard |
| `status` | String | `pending_upload` / `processing` / `completed` / `failed` |
| `upload_time` | String (ISO-8601) | Ingestion timestamp |

Species names are stored as the **team short name — the last word of the
`labels.txt` common name** (e.g. `common wombat` → `wombat`, `australian magpie`
→ `magpie`, `eastern gray kangaroo` → `kangaroo`). The ML pipeline (SpeciesNet)
emits scientific names (`Canis_familiaris`, `Vombatus_ursinus`) which are
converted via the shared `SpeciesMapper` (`app/species.py`) before writing, so
Member C and the database can never disagree on a tag string.

### Query design

Tag queries are a `Scan` followed by an in-memory filter, identical on SQLite and
DynamoDB, because neither backend can natively express "map contains key with
value ≥ N" across multiple keys. The AND filter is:

```python
all(record.tags.get(species, 0) >= count for species, count in min_counts.items())
```

For production scale this would move to a GSI (`tag` → `file_id`); Scan is
correct and sufficient for this dataset.

### Metadata state machine

Member B's ingestion boundary drives a four-step lifecycle over HTTP (defined in
`docs/member-b/api-contracts.md`, implemented here):

1. `POST /internal/uploads/reserve` — reserve a unique `(user_id, checksum)`
   (`201`) or return the existing file (`409`).
2. `POST /internal/files/{id}/processing` — lease acquisition; returns
   `{"should_process": false}` for completed files or an active unexpired lease.
3. `PUT /internal/files/{id}/complete` — idempotent completion with tags,
   detections, and thumbnail key.
4. `PUT /internal/files/{id}/failed` — idempotent bounded failure (message
   truncated to 240 chars).

### Subscription & notification trigger

Member D owns the subscription data model, notification *trigger*, Dynamo inbox,
and SNS publisher; Member E owns the frontend/in-app UX. A user subscribes to a species
short name; when a newly completed file's `tags` contain that species (`count >= 1`),
the trigger writes a `Notification` record and hands it to a `NotificationPublisher`.

- `subscriptions` table — `(user_id, species)`, idempotent subscribe/unsubscribe.
- `notifications` table — one row per (subscribed user, matched species), pointing
  at the triggering file.
- Initial `complete` deterministically ensures inbox rows before the completed
  transition. A completed replay only retries existing pending rows and does
  not create historical notifications for late subscribers.
- The production `NotificationPublisher` is the included SNS implementation;
  Member E consumes the notification API for the UI.

### Cross-module boundaries

- **ML (C):** `TagDetector` interface; C provides a MegaDetector + SpeciesNet adapter.
- **Storage (B):** `StorageClient` interface; B provides the guarded storage-delete
  Lambda (deletes S3 keys, enforced per-owner via `delete(user_id, keys)`).
- **Notifications (D/E):** D provides Dynamo inbox + SNS publishing; E provides
  frontend/in-app notification experience.
- **Auth (A):** `get_current_user` dependency already applied to every public route.
