# Sample events — Member D API

These are ready-to-send request bodies for every endpoint Member D exposes.
They double as contract fixtures: Member B can replay the metadata state machine
against a running instance, and Member E can smoke-test the query / subscription
surface before the frontend is wired up.

Start the API locally first (`python -m uvicorn app.main:app --port 8000`), then
pipe any file into `curl`:

```bash
curl -s -X POST http://localhost:8000/internal/uploads/reserve \
  -H "Content-Type: application/json" -d @events/reserve.json

curl -s -X PUT  http://localhost:8000/internal/files/11111111-2222-4333-8444-555555555555/complete \
  -H "Content-Type: application/json" -d @events/complete.json
```

| File | Endpoint | Method |
|------|----------|--------|
| `reserve.json` | `/internal/uploads/reserve` | POST |
| `processing.json` | `/internal/files/{file_id}/processing` | POST |
| `complete.json` | `/internal/files/{file_id}/complete` | PUT |
| `failed.json` | `/internal/files/{file_id}/failed` | PUT |
| `query-by-tags.json` | `/query/by-tags` | POST |
| `query-by-species.json` | `/query/by-species` | POST |
| `tags-edit.json` | `/tags/edit` | POST |
| `files-delete.json` | `/files/delete` | POST |
| `subscribe.json` | `/notifications/subscribe` | POST |

The `{file_id}` in the metadata URLs is the `file_id` you supplied in
`reserve.json` (`11111111-2222-4333-8444-555555555555`).
