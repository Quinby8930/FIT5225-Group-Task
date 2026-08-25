# Task 1 Report — Secure Member D and repair its DynamoDB boundary

## Status

Implementation and verification complete. The required commit subject is
`fix: secure query metadata boundaries`; the report is included in that single
Task 1 commit.

## Implementation

- Preserved archive-wide reads and added an explicit cross-owner query test.
- Enforced authenticated Cognito `sub` ownership for tag edits and file deletes.
  Foreign and mixed-owner batches fail atomically with HTTP 403 and the required
  nested `FORBIDDEN_OWNER` detail; no repository or storage mutation occurs.
- Reordered file deletion so storage succeeds before metadata is removed.
- Removed public `user_id` inputs from subscription/notification operations.
  `SubscribeRequest` is species-only with extra fields forbidden, and all
  notification repository calls use the verified subject.
- Added `Settings.internal_api_key`, an overridable `get_settings` dependency,
  and one `X-Internal-Api-Key` header dependency using `hmac.compare_digest`.
  All four internal routes fail closed: absent server secret is 503 and a
  missing/wrong request header is 401.
- Validated transition payloads against the reserved record before mutation:
  processing checks owner/object key, completion checks owner/object key/file
  type, and failure checks owner. Conflicts return 409 with
  `detail.code == "METADATA_CONFLICT"` and preserve state.
- Normalized persisted edit tags, completion tag keys, detection species, and
  subscription species through `get_mapper().common_name()`; normalized tags
  are also used for notification matching.
- Repaired DynamoDB `by_keys` to use `FileRecord` attributes, added complete
  scan/query pagination using `LastEvaluatedKey`, and applied pagination to file,
  subscription, and notification reads.
- Added recursive float-to-`Decimal` conversion before DynamoDB writes and
  recursive `Decimal`-to-float conversion for reconstructed detections, while
  retaining integer tag counts.
- Updated `INTEGRATION.md` for owner-safe mutations, identity-free notification
  shapes, internal auth/fail-closed behavior, metadata conflicts, ordering, and
  normalization.

## Files

- `backend/lambdas/query/app/config.py`
- `backend/lambdas/query/app/schemas.py`
- `backend/lambdas/query/app/main.py`
- `backend/lambdas/query/app/repository/dynamodb_repo.py`
- `backend/lambdas/query/app/repository/notification_repo.py`
- `backend/lambdas/query/tests/test_queries.py`
- `backend/lambdas/query/tests/test_dynamodb_repository.py` (new)
- `backend/lambdas/query/INTEGRATION.md`
- `.superpowers/sdd/2026-08-26-cross-module-integration-hardening/task-1-report.md` (new)

## Baseline

Command (from `backend/lambdas/query`):

```powershell
& 'D:\Study\Monash\FIT5225\A2\.review-artifacts\query-venv-20260826\Scripts\python.exe' -m pytest -q
```

Output:

```text
..............................                                           [100%]
30 passed, 2 warnings in 1.31s
```

## RED/GREEN evidence

### Public ownership and identity-free notifications

RED command:

```powershell
& 'D:\Study\Monash\FIT5225\A2\.review-artifacts\query-venv-20260826\Scripts\python.exe' -m pytest -q tests/test_queries.py -k 'preserves_archive_wide or rejects_entire_request or subscribe_and_list or subscribe_rejects_public_user_id or unsubscribe_idempotent'
```

RED output:

```text
.FFFFFFF                                                                 [100%]
7 failed, 1 passed, 23 deselected, 3 warnings in 0.95s
```

The passing control was the archive-wide query. The failures showed edit/delete
returning 200 instead of 403, species-only subscribe returning 422, caller-owned
subscribe returning 201 instead of 422, and notification list/unsubscribe still
requiring public identity.

GREEN command: same command.

```text
........                                                                 [100%]
8 passed, 23 deselected, 2 warnings in 0.74s
```

### Internal auth and reservation metadata conflicts

RED command:

```powershell
& 'D:\Study\Monash\FIT5225\A2\.review-artifacts\query-venv-20260826\Scripts\python.exe' -m pytest -q tests/test_queries.py -k 'internal_routes_fail_closed or internal_transition_rejects_reserved_metadata_conflicts'
```

RED output:

```text
FFFFFFFFFFFF....FFFFFF                                                   [100%]
18 failed, 4 passed, 31 deselected, 3 warnings in 1.71s
```

All 12 absent-secret/missing-header/wrong-header cases incorrectly succeeded;
all six mismatched metadata cases returned 200 instead of 409. The four
correct-header controls preserved existing success behavior.

Normalization RED command:

```powershell
& 'D:\Study\Monash\FIT5225\A2\.review-artifacts\query-venv-20260826\Scripts\python.exe' -m pytest -q tests/test_queries.py -k 'normalizes_scientific or normalizes_tags_and_detection'
```

```text
FF                                                                       [100%]
2 failed, 53 deselected, 3 warnings in 0.54s
```

Focused GREEN command:

```powershell
& 'D:\Study\Monash\FIT5225\A2\.review-artifacts\query-venv-20260826\Scripts\python.exe' -m pytest -q tests/test_queries.py -k 'internal_routes_fail_closed or internal_transition_rejects_reserved_metadata_conflicts or normalizes_scientific or normalizes_tags_and_detection'
```

```text
........................                                                 [100%]
24 passed, 31 deselected, 2 warnings in 1.53s
```

Subscription normalization was then added as a separate cycle:

```powershell
& 'D:\Study\Monash\FIT5225\A2\.review-artifacts\query-venv-20260826\Scripts\python.exe' -m pytest -q tests/test_queries.py::TestSubscriptionAndNotification::test_subscription_species_is_normalized_before_persistence
```

RED was `1 failed` (`Vombatus_ursinus` observed instead of `wombat`); GREEN was
`1 passed, 2 warnings in 0.38s`.

### DynamoDB boundary

RED command:

```powershell
& 'D:\Study\Monash\FIT5225\A2\.review-artifacts\query-venv-20260826\Scripts\python.exe' -m pytest -q tests/test_dynamodb_repository.py
```

RED output:

```text
FFFFF                                                                    [100%]
5 failed, 2 warnings in 0.27s
```

The failures were the `FileRecord.get` attribute error, first-page-only file and
notification reads, unchanged nested floats at the write boundary, and leaked
nested `Decimal` values on reconstruction.

GREEN command: same command.

```text
.....                                                                    [100%]
5 passed, 1 warning in 0.11s
```

### Mutation-order and notification-owner proofs

Storage-first RED was observed by temporarily restoring metadata-first deletion:

```powershell
& 'D:\Study\Monash\FIT5225\A2\.review-artifacts\query-venv-20260826\Scripts\python.exe' -m pytest -q tests/test_queries.py::TestEndpoints::test_delete_keeps_metadata_when_storage_deletion_fails
```

```text
F                                                                        [100%]
FAILED ... assert client.repo.get("f1") is not None
1 failed, 3 warnings in 0.52s
```

After restoring storage-first ordering, the same command produced
`1 passed, 2 warnings in 0.36s`.

Notification-owner RED was observed by temporarily restoring caller-controlled
query parameters:

```powershell
& 'D:\Study\Monash\FIT5225\A2\.review-artifacts\query-venv-20260826\Scripts\python.exe' -m pytest -q tests/test_queries.py -k 'list_ignores_caller_supplied_user_id'
```

```text
FF                                                                       [100%]
2 failed, 57 deselected, 3 warnings in 0.69s
```

After restoring verified-subject lookups, the same command produced
`2 passed, 57 deselected, 2 warnings in 0.42s`.

## Final verification

Exact required full-suite command (from `backend/lambdas/query`):

```powershell
& 'D:\Study\Monash\FIT5225\A2\.review-artifacts\query-venv-20260826\Scripts\python.exe' -m pytest -q
```

Output:

```text
.....................................................................    [100%]
69 passed, 2 warnings in 3.45s
```

`git diff --check` returned no whitespace errors.

## Self-review

- Confirmed global query routes still call `repo_.all()` without owner filtering.
- Confirmed owner validation occurs after resolution but before every mutation.
- Confirmed mixed-owner requests cannot partially edit/delete owned records.
- Confirmed storage deletion precedes metadata deletion and has a failure test.
- Confirmed public notification routes never use caller-supplied identity.
- Confirmed the single internal header dependency is attached to reserve,
  processing, complete, and failed, with fail-closed ordering.
- Confirmed metadata comparisons happen before completed/idempotent branches and
  before any repository or notification mutation.
- Confirmed mapper output, rather than raw payload tags, drives persistence and
  notification matching.
- Confirmed every file scan and every notification scan/query follows pagination.
- Confirmed AWS numeric conversion is recursive, uses `Decimal(str(float))`, and
  does not coerce integer tag counts to floats.
- Confirmed changes are limited to the brief's Member D files plus this report.

## Concerns

No functional blocker. The mandated environment emits two non-failing warnings:
an existing Starlette `httpx` deprecation warning and a pytest cache warning
because the sandbox cannot write `.pytest_cache`. Neither affects test behavior
or repository output.
