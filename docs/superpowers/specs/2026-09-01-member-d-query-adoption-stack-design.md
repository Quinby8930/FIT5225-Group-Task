# Member D Dedicated Query Adoption Stack Design

**Status:** Conditionally approved for local TDD and implementation on 2026-09-01.

**AWS authority:** None. This design does not authorize creating, executing, or deleting a change set or stack.
**Source baseline:** `origin/main` at `0392bfa`.

## 1. Why the architecture changes

Two real CloudFormation IMPORT attempts against `PacificBioArchive-Database`
failed before a change set was created, even when the generated template's
entire non-`Resources` section and the existing four resources were byte-for-
byte equivalent to the live processed template. A one-resource import failed
the same way. Continuing to patch the original-stack import path would therefore
be guesswork against service-side stack history, not evidence-based debugging.

The approved boundary is a new dedicated stack. It leaves the original stack's
history and outputs untouched and imports only resources that are currently
unmanaged.

## 2. Stack ownership boundary

The exact target stack name is `PacificBioArchive-QueryAdoption`.

| Stack | Resources owned at the first stable boundary |
|---|---|
| `PacificBioArchive-Database` | `FilesTable`, `SubscriptionsTable`, `NotificationsTable`, `QueryLambdaRole` |
| `PacificBioArchive-QueryAdoption` | `ReservationsTable`, `QueryFunction`, `QueryIntegration`, and the 16 non-OPTIONS routes listed below |

The 16 imported routes are:

1. `AuthTestRoute`
2. `QueryByTagsRoute`
3. `QueryBySpeciesRoute`
4. `QueryByThumbnailRoute`
5. `QueryByFileRoute`
6. `EditTagsRoute`
7. `DeleteFilesRoute`
8. `SubscribeRoute`
9. `UnsubscribeRoute`
10. `SubscriptionsRoute`
11. `NotificationsRoute`
12. `ReserveUploadRoute`
13. `AcquireProcessingRoute`
14. `CompleteFileRoute`
15. `FailFileRoute`
16. `AuthorizeAssetsRoute`

The sets are disjoint. The query stack must never contain, import, or recreate
the original stack's four logical resources. Resources added by a later normal
UPDATE (OPTIONS routes and route-scoped Lambda permissions) are query-service
support resources, not imported resources.

## 3. Cross-stack inputs

The import template accepts only ordinary, non-sensitive `String` parameters:

- `ExistingQueryLambdaRoleArn`
- `ExistingHttpApiId`
- `ExistingJwtAuthorizerId`

The later query-service template additionally accepts the existing core table
names and other service configuration as ordinary parameters. It must not use a
`Ref` or `Fn::GetAtt` to any logical resource owned by the database stack.

The audit records the live role ARN, API ID, authorizer ID, table names, account,
and region. Validators require the parameter values to equal that evidence
exactly. No output or export is added to the database stack.

`InternalApiKey` is deliberately absent from the audit, snapshot, import
template, resources-to-import manifest, parameter artifacts, command line,
environment, and logs. It is introduced only in the separately reviewed normal
UPDATE template as `String`, `NoEcho: true`, `MinLength: 1`, and its value may be
entered only in the CloudFormation console password field.

## 4. Initial IMPORT artifact contract

The generated template:

- has no `Outputs`, `InternalApiKey`, `Metadata`, macros, or transforms;
- contains exactly the 19 import resources and no others;
- declares `DeletionPolicy: Retain` and `UpdateReplacePolicy: Retain` on every
  resource;
- represents `QueryFunction` as `AWS::Lambda::Function` and takes its role from
  `ExistingQueryLambdaRoleArn`;
- omits `Environment` because the existing secret value must never be read;
- uses the audited API and authorizer parameters for the integration/routes;
- is regenerated from a fresh read-only audit and never reused after a failed
  attempt.

`resources-to-import.json` is generated from audited physical identifiers. It
contains exactly one table, one function, one integration, and 16 routes. The
operator never types those identifiers.

CloudFormation IMPORT is treated as ownership registration only. The template
and validator cannot prove that the service made no runtime mutation. Therefore
execution, if separately approved later, is immediately followed by the
post-import evidence gate in section 6.

## 5. Pre-creation and IMPORT preview validators

Before a human may create an IMPORT preview, the artifact validator must prove:

- target stack name equals `PacificBioArchive-QueryAdoption` and does not equal
  `PacificBioArchive-Database`;
- the template has no original-stack resource, output, or secret parameter;
- the manifest contains exactly the 19 expected imports;
- all cross-stack parameter values exactly match the fresh audit;
- every physical resource is still unmanaged by every CloudFormation stack;
- the artifact digest/version and fresh runtime fingerprint match the approved
  preparation bundle.

After a separately approved preview is created, and before execution can even
be considered, the Change Set validator must additionally prove:

- the described stack name is exactly the target and the described change set
  type is exactly `IMPORT`;
- it contains exactly those 19 expected imports;
- every action is `Import`; there is no Add, Modify, Remove, replacement, or
  unknown logical ID/type;
- a fresh audit still finds all 19 physical resources unmanaged;
- the processed template and parameters still match the preparation bundle.

`audit`, validation and recovery reporting are read-only. `prepare` performs the
explicitly disclosed versioned S3 backup only after separate write approval.
The tool never creates, executes or deletes a CloudFormation change set or
stack.

## 6. Post-import evidence gate

The query stack is accepted only in `IMPORT_COMPLETE` with exactly the 19
logical/physical/type mappings. A fresh read-only snapshot is compared with the
pre-import baseline.

For the Lambda, compare:

- function identity, role, runtime, handler, timeout, memory, description,
  package type, architecture, layers, ephemeral storage, VPC/file-system/dead-
  letter/tracing/logging/runtime-management configuration and tags;
- complete environment-variable name set and every non-secret value;
- `CodeSha256`, `RevisionId`, reserved/provisioned concurrency evidence;
- the complete resource policy and its revision.

For API Gateway, compare the integration ID, type, method, URI, payload version,
timeout/TLS/credentials/request parameters where present, plus every route ID,
key, target, authorization type, authorizer ID and API-key requirement. This
explicit comparison is mandatory because `AWS::ApiGatewayV2::Integration` does
not provide usable CloudFormation drift evidence for this workflow.

Any difference blocks the normal UPDATE. No tolerance or auto-repair is
permitted.

## 7. Normal UPDATE gate

The normal UPDATE is a separate future operation with its own artifact,
CloudFormation preview, validator and human approval. Its template keeps all imported
resources retained, adds only explicitly listed query-support resources, and
modifies only explicitly listed imported resources without replacement.

The first normal UPDATE allowlist is fixed at exactly 37 actions:

- one `Modify` with `Replacement: False`: `QueryFunction`, for reviewed code
  and complete environment registration;
- ten `Add` actions: the OPTIONS routes;
- 26 `Add` actions: the method/path-scoped `AWS::Lambda::Permission`
  resources.

No Remove, replacement, database-stack resource, wildcard Lambda permission, or
unlisted action is allowed. The update validator also checks that the three
audited historical Lambda permissions are preserved until the separately
approved, revision-safe cleanup sequence.

The first UPDATE contains no SNS Topic or Subscription and does not modify or
reconcile the database-owned role. Durable in-app notifications remain in
`NotificationsTable`. Per-user email activation requires a separately approved
cross-stack IAM/SNS design using Cognito-verified claims. This task does not copy
live role drift into a template and does not change that role.

## 8. State machine and recovery

| State | Ownership expectation | Permitted action before the next approval | Recovery rule |
|---|---|---|---|
| target absent | all 19 unmanaged | audit/recovery report; `prepare` only after separate S3-write approval | regenerate from a fresh audit |
| `REVIEW_IN_PROGRESS` | normally zero managed resources | inspect stack/change sets/resources | do not retry; an empty shell may be deleted only after a separate approval |
| IMPORT change-set creation failed | zero or unknown | re-audit target and all 19 owners | discard all artifacts; no retry until cause is reviewed |
| `IMPORT_IN_PROGRESS` | transition | observe only | wait; never create another operation |
| `IMPORT_COMPLETE` | exactly 19 owned by query stack | run post-import evidence gate | stable rollback boundary |
| `IMPORT_ROLLBACK_COMPLETE` | must be re-audited | recovery report only | if empty, separately approve shell cleanup; if any resource is owned, stop |
| `IMPORT_ROLLBACK_FAILED` | unknown/unsafe | evidence collection only | freeze automation and escalate to AWS Support/manual recovery review |
| `UPDATE_IN_PROGRESS` | exactly 19 plus approved additions | observe only | rely on CloudFormation rollback, never overlap operations |
| `UPDATE_ROLLBACK_COMPLETE` | imported 19 must remain owned | full ownership/runtime/API verification | accept only if equivalent to the `IMPORT_COMPLETE` boundary; otherwise stop |
| empty query stack | zero resources | generate cleanup checklist | deletion requires a separate explicit approval |

No recovery path deletes, replaces, edits, or disowns a real application
resource. Cleanup applies only to a proven-empty stack shell.

## 9. Safety invariants

At every stage:

- no real resource is deleted or replaced;
- no business data is read or modified by the tooling;
- no secret is read, printed, recorded, snapshotted, or stored;
- the key appears only in the future normal UPDATE console NoEcho field;
- each AWS write would require a fresh validator result and explicit human
  approval (none is granted by this design);
- failed-attempt artifacts are never reused;
- live drift is not copied into a maintainable template as a shortcut;
- the original database stack's outputs and resources remain untouched;
- S3 objects remain at stable `user/file`-scoped keys; species navigation is a
  DynamoDB-tag logical directory, not an S3 copy/move hierarchy.

## 10. Local implementation deliverables

The implementation must provide pure/template validators and mocked CLI tests
for every state above, a dedicated import/query template path, automatic
identifier generation, post-import comparison, a final-update allowlist, the
operator runbook, and report-ready ownership text. All Member D tests and an
independent code review must complete before any push or AWS instruction is
authorized.
