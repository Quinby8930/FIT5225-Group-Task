# Member D Existing AWS Resource Adoption Design

**Status:** Approved in chat on 2026-08-31  
**Engineering mode:** Coursework  
**Scope:** Adopt the existing Member D AWS resources into the existing
`PacificBioArchive-Database` stack, then update that stack to the repository's
current implementation without deleting live API routes.

## 1. Confirmed starting state

The live AWS account and `ap-southeast-2` region have been inspected with
read-only commands.

- `PacificBioArchive-Database` is `UPDATE_ROLLBACK_COMPLETE` and currently
  manages only `FilesTable`, `SubscriptionsTable`, `NotificationsTable`, and
  `QueryLambdaRole`.
- The live `PacificBioArchive-QueryLambda` function is not managed by that
  stack. It uses Python 3.12, `lambda_function.handler`, the stack-owned
  `PacificBioArchive-QueryLambdaRole`, a 30-second timeout, and 512 MiB memory.
- API Gateway integration `fbjojun` is an unmanaged `AWS_PROXY` integration
  using `POST`, payload format 2.0, and the live Query Lambda.
- Sixteen live Member D routes all target `integrations/fbjojun`. They are not
  managed by any listed CloudFormation stack. The ten Member D OPTIONS routes
  declared by the current repository template do not exist.
- A normal update with the current template failed because CloudFormation tried
  to create the sixteen occupied route keys and received `AlreadyExists`.
- The shared HTTP API is `2dd2aqb32j`, its `dev` stage has auto-deploy enabled,
  and JWT authorizer `7ir7fs` is present.
- IAM user `fit5225-cli-deployer` has `AdministratorAccess`, no inline or group
  policies, and no console login profile. The current CloudShell session is a
  Root session and must not perform deployment writes.

## 2. Goals

1. Preserve the current API URL, route keys, route IDs, Lambda name, data, and
   live availability during ownership migration.
2. Make the existing Query Lambda, integration, and sixteen Member D routes
   resources of `PacificBioArchive-Database` rather than leaving them as manual
   configuration.
3. Update the adopted stack to the repository's current Member D code and add
   the missing reservations table, SNS resources, explicit OPTIONS routes,
   Lambda permissions, policies, and environment configuration.
4. Make preparation repeatable and fail closed when AWS state differs from the
   audited state.
5. Keep credentials, the internal API key, Cognito tokens, presigned URLs, and
   Lambda environment secret values out of Git, generated evidence, terminal
   output, and chat.

## 3. Non-goals

- Do not delete or recreate the shared HTTP API, Cognito authorizer, existing
  DynamoDB tables, live Query Lambda, integration, or existing routes.
- Do not change Member B's media stack or Member C's Alibaba Function Compute
  deployment during the adoption operation.
- Do not implement per-user verified-email SNS delivery in this migration. The
  adoption deploys only the notification infrastructure represented by the
  current repository template; the previously selected per-user email upgrade
  remains a separate code and deployment change.
- Do not store deployment parameter values in `samconfig.toml`.

## 4. Chosen approach

Use a two-stage CloudFormation migration:

1. An `IMPORT` change set adopts the existing Query Lambda, integration, and
   sixteen routes without changing their live properties.
2. After import and drift checks, a normal `UPDATE` change set deploys the
   current application and infrastructure changes.

This is preferred over deleting routes or creating a parallel Query service.
Deleting routes creates avoidable downtime and a difficult rollback. A parallel
service followed by manual route retargeting would leave ownership split and
recreate the same drift problem.

## 5. Non-Root execution identity

Before any AWS write, the operator uses the IAM console to enable console access
for the existing `fit5225-cli-deployer` user with an automatically generated
one-time password and required password reset. The operator then signs out of
Root, signs in as that IAM user, opens a new CloudShell, and verifies that
`aws sts get-caller-identity` no longer ends in `:root`.

No Root access key is created. No access key, password, token, or session value
is copied into the repository, Codex, chat, or a shell script.

## 6. Preparation tool

Create a focused Python tool under `infrastructure/member-d/import/` with two
explicit modes.

### 6.1 `audit`

`audit` performs read-only AWS calls and writes only sanitized local JSON:

- current stack status, processed template, and managed resources;
- the Query Lambda's non-secret configuration and code SHA-256;
- integration type, method, payload version, URI, and ID;
- route IDs, keys, targets, authorization types, and authorizer IDs;
- HTTP API stage and authorizer identity.

The tool never serializes Lambda environment variable values. It validates the
expected resource set and exits non-zero when:

- the caller is Root;
- the stack is not in an import-safe stable state;
- a live D route is missing, duplicated, points away from `fbjojun`, or has the
  wrong authentication mode;
- a Member B route is selected for import;
- the integration no longer invokes `PacificBioArchive-QueryLambda`;
- any candidate resource is already owned by another stack.

### 6.2 `prepare`

`prepare` requires an explicit private SAM artifact bucket. It:

1. obtains the existing Lambda deployment package without printing its
   presigned download URL;
2. verifies the downloaded package against the live `CodeSha256`;
3. uploads the exact package under a content-addressed backup key;
4. generates an import-only template from the stack's processed template;
5. generates `resources-to-import.json` for exactly eighteen resources: one
   Lambda, one integration, and sixteen routes.

The generated Query Lambda resource references the exact backup object, uses
the existing IAM role, handler, runtime, timeout, memory, and function name, and
uses a `NoEcho` parameter for the existing internal API key. Generated files
contain no secret value. The tool does not create or execute a CloudFormation
change set.

Generated snapshots and templates live in
`infrastructure/member-d/import/.work/` and are ignored by Git.

## 7. Imported route mapping

Only the following existing routes are imported:

| Logical ID | Live route key |
|---|---|
| `AuthTestRoute` | `GET /auth-test` |
| `QueryByTagsRoute` | `POST /query/by-tags` |
| `QueryBySpeciesRoute` | `POST /query/by-species` |
| `QueryByThumbnailRoute` | `GET /query/by-thumbnail` |
| `QueryByFileRoute` | `POST /query/by-file` |
| `EditTagsRoute` | `POST /tags/edit` |
| `DeleteFilesRoute` | `POST /files/delete` |
| `SubscribeRoute` | `POST /notifications/subscribe` |
| `UnsubscribeRoute` | `DELETE /notifications/subscribe` |
| `SubscriptionsRoute` | `GET /notifications/subscriptions` |
| `NotificationsRoute` | `GET /notifications` |
| `ReserveUploadRoute` | `POST /internal/uploads/reserve` |
| `AcquireProcessingRoute` | `POST /internal/files/{file_id}/processing` |
| `CompleteFileRoute` | `PUT /internal/files/{file_id}/complete` |
| `FailFileRoute` | `PUT /internal/files/{file_id}/failed` |
| `AuthorizeAssetsRoute` | `POST /internal/assets/authorize` |

The four Member B routes (`POST`/`OPTIONS` for `/upload-url` and
`/asset-urls`) are explicitly excluded. The ten absent Member D OPTIONS routes
are created only by the later normal update, never by the import change set.

Every imported resource receives `DeletionPolicy: Retain` and
`UpdateReplacePolicy: Retain` in both the import-only and maintained templates.

## 8. Maintained SAM template changes

The repository's Member D template is adjusted so the normal update preserves
the adopted physical resources.

- Keep logical resource `QueryLambdaRole` and move the current function policy
  statements onto that explicit role.
- Set `QueryFunction.FunctionName` to `PacificBioArchive-QueryLambda` and set
  its `Role` to `QueryLambdaRole`; do not let SAM generate a second role or a
  second function.
- Keep logical resource `QueryIntegration` and all sixteen route logical IDs
  identical to the import template.
- Add the reservations table, notification topic/subscription, ten OPTIONS
  routes, and method-scoped Lambda permissions through the normal update.
- Update the existing function in place to 1024 MiB and the current repository
  code and environment contract.
- Resolve the media bucket and storage-delete function from
  `PacificBioArchive-Media` outputs. Use Member C's HTTPS base URL without an
  `/infer` suffix. Supply the shared internal key only as a `NoEcho` deployment
  parameter.
- Keep `AllowLegacyProcessingCallbacks=false` in the stable deployment because
  the current Member B code forwards lease tokens.
- Leave `NotificationEmailEndpoint` empty for this ownership migration.

## 9. Change-set execution and validation

The generated import template and resource identifiers are first used to create
an `IMPORT` change set. The operator reviews it and confirms that it contains
only eighteen `Import` actions and no create, update, delete, or replacement.
Execution remains a separate manual approval.

After `IMPORT_COMPLETE`:

1. run stack drift detection;
2. verify all imported physical IDs match their pre-import IDs;
3. call an existing authenticated read-only query as a smoke test;
4. build and validate the maintained SAM template;
5. create a normal `UPDATE` change set and review every create/update/delete;
6. execute only after a second manual approval;
7. verify authentication, uploads, processing callbacks, queries, preview URL
   authorization, tag editing, deletion, subscriptions, and notifications.

## 10. Rollback and failure handling

- Import failure must leave the live resources unmanaged but unchanged. The
  import template's retain policies prevent accidental deletion.
- The exact pre-import Lambda package is retained in the private artifact
  bucket so a subsequent update rollback references runnable code rather than a
  placeholder package.
- The import template represents the current function name, role, runtime,
  handler, timeout, memory, and non-secret environment contract. The internal
  key is supplied through a `NoEcho` parameter, so rollback does not clear it.
- If drift detection reports a mismatch, stop before the normal update and
  correct the generated import description; do not manually edit the live
  route or integration.
- If the normal update rolls back, keep the retained imported resources and
  inspect stack events before any retry. Never fix a rollback by deleting a
  route, table, integration, or function.

## 11. Automated verification

Tests use sanitized fixtures and exercise real generator behavior without AWS
writes. They cover:

- exact selection and logical-ID mapping of all sixteen D routes;
- exclusion of Member B and absent OPTIONS routes;
- JWT on public routes and `NONE` on internal routes;
- shared integration and Lambda identity validation;
- refusal to operate for Root, unstable stack state, unexpected ownership, or
  mismatched resource configuration;
- generation of exactly eighteen import records;
- retain policies on all imported resources;
- absence of secret values and account-specific generated files from Git;
- maintained SAM template reuse of the existing function and role;
- existing Member D template tests, query tests, `sam validate`, and
  `git diff --check`.

