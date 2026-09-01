# Team Report source: Member D dual-stack ownership

> The repository currently contains no canonical Team Report `.docx`, `.pdf`,
> or other designated final report artifact. This file is report-ready source
> text for later integration; it does not claim that an external Team Report was
> edited.

## CloudFormation ownership design

Member D uses two CloudFormation stacks with disjoint ownership. The existing
`PacificBioArchive-Database` stack owns the three core DynamoDB tables
(`FilesTable`, `SubscriptionsTable`, and `NotificationsTable`) and the existing
`QueryLambdaRole`. The new `PacificBioArchive-QueryAdoption` stack is designed
to adopt the previously unmanaged query-service resources: the upload
reservations table, Query Lambda, one API Gateway V2 integration, and exactly 16
non-OPTIONS Member D routes. This split is an explicit resource-ownership
boundary, not a duplicate deployment; each live resource belongs to only one
stack.

The query stack receives the existing role ARN, HTTP API ID, JWT authorizer ID,
and core table names through ordinary non-sensitive parameters. A validator
compares these values with fresh read-only AWS evidence. The design does not add
CloudFormation exports, does not alter the database stack's Outputs, and does
not copy or reconcile live role drift into the query template.

## Safe adoption sequence

The initial import template contains only 19 existing resources and gives every
resource both `DeletionPolicy: Retain` and `UpdateReplacePolicy: Retain`. It has
no Outputs, SNS resources or internal API key. The 19 physical identifiers are
generated automatically from the audit, avoiding manual transcription. An
IMPORT preview is accepted only when CloudFormation reports exactly 19 Import
actions for the new stack and no Add, Modify, Remove or Replace action.

If import is later approved and executed, a mandatory evidence gate compares
the complete Lambda configuration, safe environment evidence, code hash,
revision, role, concurrency settings and resource policy with the pre-import
baseline. API Gateway integration and route properties are read directly from
the API because Integration drift detection is unavailable for this workflow.
Any difference blocks the later update.

The first normal update is a separate approval boundary. Its validator permits
only a non-replacing modification of `QueryFunction`, addition of ten OPTIONS
routes, and addition of 26 method/path-scoped Lambda permissions. It prohibits
resource removal or replacement and does not modify the database-owned role.
The update does not deploy SNS. Durable in-app notifications remain in
`NotificationsTable`; per-user email delivery requires a future, separately
approved Cognito-claims, IAM and SNS design.

## Secret and failure safety

The initial import never reads, stores or references `InternalApiKey`. If the
normal update is approved, the current value is entered only into the
CloudFormation Console `NoEcho` field. It is never passed through CLI arguments,
environment variables, files, logs, screenshots, source control or team chat.

The workflow has explicit handling for an absent target stack,
`REVIEW_IN_PROGRESS`, import preview failure, `IMPORT_ROLLBACK_COMPLETE`,
`IMPORT_ROLLBACK_FAILED`, `IMPORT_COMPLETE`, and
`UPDATE_ROLLBACK_COMPLETE`. Automation never deletes or replaces an application
resource. A proven-empty stack shell can be considered for deletion only under
a new, explicit approval. The stable recovery boundary after adoption is the
exact 19-resource `IMPORT_COMPLETE` state.

## Media and species organization

Original media, thumbnails and derived frames retain stable user/file-scoped S3
keys. Species classification is stored as tags and counts in DynamoDB. Querying
those tags creates a logical species directory without copying or moving S3
objects. This avoids duplicate objects for media containing multiple species,
keeps deletion consistent, and prevents previously issued object references
from becoming invalid merely because a tag changes.
