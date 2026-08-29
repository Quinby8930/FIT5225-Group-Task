# Task 3 report: frontend state and repository route hardening

## Scope and files

Implemented Task 3 only, preserving the existing Cognito/PKCE flow, `apiClient`, API JSON contracts, cross-user preview of completed media, and server-provided owner-only mutation authority.

- Manage session/state: `frontend/src/App.jsx`, `frontend/src/features/manage/ManagePanel.jsx`, `frontend/src/lib/manageWorkflow.mjs`, `frontend/src/lib/manageWorkflow.test.mjs`, `frontend/src/lib/viewState.mjs`, `frontend/src/lib/viewState.test.mjs`
- Upload validation: `frontend/src/api/mediaApi.js`, `frontend/src/lib/uploadWorkflow.test.mjs`
- Notification state: `frontend/src/features/notifications/NotificationsPanel.jsx`, `frontend/src/lib/notificationState.mjs`, `frontend/src/lib/notificationState.test.mjs`
- Signed previews: `frontend/src/hooks/useSignedAssetUrls.js`, `frontend/src/lib/assetUrls.mjs`, `frontend/src/lib/assetUrls.test.mjs`, `frontend/src/lib/assetRequestCoordinator.mjs`, `frontend/src/lib/assetRequestCoordinator.test.mjs`, `frontend/src/lib/retryEpisodes.test.mjs`, `frontend/src/components/MediaCard.jsx`, `frontend/src/lib/mediaActions.mjs`, `frontend/src/lib/mediaActions.test.mjs`
- Raw-key presentation: `frontend/src/components/MediaCard.jsx`, `frontend/src/features/explore/ExplorePanel.jsx`, `frontend/src/features/notifications/NotificationsPanel.jsx`, `frontend/src/lib/queryResults.mjs`, `frontend/src/lib/queryResults.test.mjs`, `frontend/src/lib/notificationState.mjs`, `frontend/src/lib/notificationState.test.mjs`
- Member D IaC: `infrastructure/member-d/dynamodb.yaml`, `infrastructure/member-d/test_template.py`
- Report: `.superpowers/sdd/audit-code-hardening-plan/task-3-report.md`

## RED/GREEN evidence

Each behavior group was tested before its production change.

1. Manage: `node --test src/lib/manageWorkflow.test.mjs` failed because `canCommitManageEffect` and `removeManagedQueryItems` did not exist; after implementation, 4/4 passed.
2. Upload: `node --test src/lib/uploadWorkflow.test.mjs` failed because `validateUploadFile` did not exist and all four invalid files reached `arrayBuffer()`; after implementation, the focused suite and Member E contract test passed. Tests accept exact limits, reject limit + 1, reject empty/unknown MIME without filename inference, and prove `arrayBuffer`, `fetch`, and stage callbacks remain untouched.
3. Notifications: `node --test src/lib/notificationState.test.mjs` failed because refresh errors replaced prior items, refresh returned no explicit result, and there was no partial-success status; after implementation, 4/4 passed.
4. Signed URLs: `node --test src/lib/assetUrls.test.mjs src/lib/retryEpisodes.test.mjs` failed because the fourth dispatched retry failure remained `signing_failed`; after implementation, 27/27 passed, including initial/non-terminal failure, near-expiry preservation, terminal non-retryability, and manual reset.
5. Raw keys: `node --test src/lib/queryResults.test.mjs src/lib/notificationState.test.mjs` failed because notification normalization retained `object_key` and the strict owner display/legacy label helpers did not exist; after implementation, the focused suites and Member E contract test passed.
6. IaC: `python -m pytest infrastructure/member-d/test_template.py -q` failed with the exact route set missing `GET /auth-test` and `OPTIONS /auth-test`, and 24 permissions where 26 were required; after implementation, 8/8 passed.

## UX and state decisions

- Each observed login session receives a monotonic opaque identity distinct from the Cognito subject, so A1→B→A2 cannot reuse a fence. Upload, Explore, Manage, and Notifications are keyed by that identity. A Manage mutation captures the current non-empty identity before dispatch; `ManagePanel` checks it after every awaited mutation and inside local/final state updaters, while `App` independently checks it inside query/selection updaters and status/deletion callbacks.
- Deletion applies a functional query transition to the latest results. Query-state/status feedback is derived after that transition, preventing a stale render closure from restoring old results.
- Upload validation runs before any progress stage, hashing, reservation request, or storage upload. User-facing errors state the exact 12 MiB image or 250 MiB video limit.
- Notification refresh returns `{ ok: true, ... }` or `{ ok: false, error }`. State changes only from a complete snapshot; refresh failure retains existing subscriptions and notification items. A successful subscribe/unsubscribe followed by refresh failure reports an error that explicitly says the mutation succeeded but refresh failed.
- `SIGNING_FAILED` and `UNAVAILABLE` are transient. A still-valid URL remains usable through retries; failure after automatic retry dispatch four adds an exhaustion flag, removes retryability, and excludes that key from proactive refresh. Every dispatched request also marks its keys in flight, excluding them from retry, proactive, expiry, focus, and visibility dispatches until the response settles. Expiry converts an exhausted URL locally to `retry_exhausted` without a fifth request. Without a usable URL, retry four becomes terminal immediately. Manual Refresh intentionally supersedes an older request and clears counters and exhaustion before a fresh request. A synchronous latest-state coordinator is the source for all hook transitions, so queued expiry/focus work cannot overwrite a newer response. Only the terminal no-URL message is a polite live status.
- Structured raw keys remain normalized and available for signing/full-size preview/mutation. A media card renders a raw key only for `can_manage === true`. Valid notification object keys remain in internal normalized data, while an explicit presentation projection omits them from rendering; legacy rows use neutral numbered labels.
- Member D owns explicit JWT `GET /auth-test` and unauthenticated `OPTIONS /auth-test` routes through the existing Query integration, with exact method-scoped Lambda permissions.

## Verification

- Frontend complete suite: `npm test` — 120 passed, 0 failed after fix round 2.
- Production build: `npm run build -- --base=/FIT5225-Group-Task/` — Vite 7.3.6, 70 modules transformed, exit 0.
- Member D IaC: `python -m pytest infrastructure/member-d/test_template.py -q -p no:cacheprovider` — 8 passed, 0 failed.
- Dependency preparation: the isolated worktree initially had no `frontend/node_modules`; `npm ci --offline` materialized the existing lockfile without a dependency upgrade or source change.
- Diff hygiene: `git diff --check` passed before report creation; a final fresh check is required immediately before commit.

## Limitations and deployment warning

- No browser/DevTools session was available, so this report makes no browser-rendering claim. Unit tests and the production build cover the repository changes.
- No AWS deployment was performed. Before a future CloudFormation/SAM deployment, check whether a pre-existing manually created route already owns `GET /auth-test` or `OPTIONS /auth-test`; reconcile/import/remove that manual route as appropriate before applying the repository template to avoid duplicate route-key failure.
- No secrets, dependency upgrades, push, deployment, or merge were performed.

## Hashes

- Required base: `b0069a5b41475ab71007a83c24be55cbe8eef153`.
- Final Task 3 commit: this report is part of that commit, so the containing commit hash is reported in the task handoff (`git rev-parse HEAD`) rather than self-referenced here.

## Fix round 1 RED/GREEN evidence

Review fixes were implemented as separate focused TDD cycles:

1. Signed URL lifecycle: `node --test src/lib/assetUrls.test.mjs src/lib/retryEpisodes.test.mjs` failed because retry-four retained `retryable`, `SIGNING_FAILED` discarded a usable URL, and scheduling/expiry helpers were absent. The deterministic retry1→retry4→expiry→manual-reset lifecycle is GREEN, including local exhausted expiry and no fifth automatic request.
2. Mixed-key scheduling: a follow-up `assetRefreshSchedule` test failed because another key's proactive refresh had no way to exclude an exhausted valid URL. It is GREEN with an exact healthy-only key plan.
3. Terminal accessibility: `node --test src/lib/mediaActions.test.mjs` failed because terminal-only live semantics did not exist. It is GREEN; loading and transient failures remain non-live.
4. Session identity: `node --test src/lib/viewState.test.mjs src/lib/manageWorkflow.test.mjs src/lib/uploadWorkflow.test.mjs` failed because the monotonic identity and guarded updater/finalizer helpers did not exist. It is GREEN for A1→B→A2, rejecting every A1 callback, query/selection updater, Upload effect, and Manage finalizer in A2.
5. Notification data/presentation: `node --test src/lib/notificationState.test.mjs` failed because normalization omitted `object_key`, the safe presentation projection was missing, and failure copy could not distinguish initial load from preserved data. The focused suite and Member E contract test are GREEN.

## Fix round 2 RED/GREEN evidence

The signed-URL lifecycle race was reproduced and fixed in one focused TDD cycle:

1. `node --test src/lib/assetRequestCoordinator.test.mjs` was RED at 5 passed / 3 failed because the hook had no latest-state coordinator shared with testable scheduling policy. The regressions defer automatic retry dispatch four, exercise proactive/expiry/focus decisions while it remains unresolved, resolve it separately with success and failure, and queue expiry/focus work before a newer manual-refresh response.
2. After wiring `createLatestAssetStateCoordinator` into every hook state transition and adding per-key `requestInFlight` policy, `node --test src/lib/assetRequestCoordinator.test.mjs src/lib/assetUrls.test.mjs` was GREEN at 35/35. Both deferred retry-four branches dispatch exactly four automatic requests; in-flight keys produce no proactive/expiry/focus request keys, success clears in-flight state, failure terminalizes after expiry, and queued stale transitions preserve the newer manual response.
3. Fresh verification remained GREEN: `npm test` 120/120, repository-base production build completed with 70 modules transformed, and Member D IaC tests passed 8/8.
