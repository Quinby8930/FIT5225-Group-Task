# Member D Dedicated Query Adoption Stack Implementation Plan

> This plan is local-only. Do not call AWS write APIs, create change sets, push,
> or ask the operator to perform AWS actions while executing it.

**Goal:** Replace the failed original-stack import workflow with a validator-
guarded dedicated query adoption stack that imports exactly 19 unmanaged
resources and preserves the database stack's four-resource ownership boundary.

**Baseline:** `0392bfa`; 305 focused Member D tests pass.

## Task 1: Freeze ownership and parameter contracts with failing tests

**Files:**

- Modify: `infrastructure/member-d/import/test_adoption.py`
- Modify: `infrastructure/member-d/import/test_prepare_import.py`

Add tests that require the exact source/target stack names, exact disjoint
resource sets, exact three import parameters, and strict rejection of an
original-stack logical resource, output, secret, foreign owner, parameter
mismatch, or non-IMPORT action. Add state tests for target absent,
`REVIEW_IN_PROGRESS`, `IMPORT_COMPLETE`, rollback states and empty-shell cleanup
classification.

Run the new tests and verify they fail for the intended missing behavior.

## Task 2: Implement the dedicated import model

**Files:**

- Modify: `infrastructure/member-d/import/adoption.py`
- Modify: `infrastructure/member-d/import/prepare_import.py`
- Modify: tests from Task 1

Introduce named source/target contracts and phase-specific validators. Build a
standalone import template rather than copying the original stack template.
Generate the exact 19-resource manifest from fresh evidence. Add exact
cross-stack parameter binding and stack ownership checks. Do not add any
CloudFormation write operation; the existing `prepare` S3 backup remains an
explicit, separately approved write.

Run Task 1 tests until green, then the complete adoption and preparation suites.

## Task 3: Add post-import runtime/API verification and recovery states

**Files:**

- Modify: `infrastructure/member-d/import/adoption.py`
- Modify: `infrastructure/member-d/import/prepare_import.py`
- Modify: `infrastructure/member-d/import/test_adoption.py`
- Modify: `infrastructure/member-d/import/test_prepare_import.py`

First add failing tests for every required Lambda field, safe environment
values/names, code/revision/concurrency/policy evidence, Integration fields, all
Route fields, exact post-import ownership, and each rollback state. Implement a
dedicated post-import verification operation and a pure recovery classifier.
Errors must fail closed and must never suggest a retry with stale artifacts.

## Task 4: Split the CloudFormation source boundary

**Files:**

- Modify: `infrastructure/member-d/dynamodb.yaml`
- Add: `infrastructure/member-d/query-adoption.yaml`
- Modify: `infrastructure/member-d/test_template.py`

Write failing template tests first. Keep the original stack template limited to
the three core tables and `QueryLambdaRole`. Put the reservation table, query
Lambda, integration and 16 imported routes in the dedicated query template,
then add exactly ten OPTIONS routes and 26 scoped Lambda permissions for the
first normal UPDATE. That UPDATE contains no SNS resources and does not modify
the database-owned role. Replace all cross-stack `Ref`/`GetAtt` links with
ordinary parameters and keep `InternalApiKey` solely in the normal-update query
template. No exports are introduced.

The final-update validator must accept only the documented additions and a
non-replacing `QueryFunction` modification, and reject every Remove/Replace or
database-stack resource.

## Task 5: Update operator and report documentation

**Files:**

- Modify: `docs/member-d/aws-resource-adoption.md`
- Modify: `docs/member-d/database-setup.md`
- Modify: `infrastructure/member-d/README.md`
- Modify: `README.md`
- Add: `docs/member-d/team-report-contribution.md`

Document the two-stack ownership table, stable S3 object paths, logical species
directory, exact recovery state machine, console-only future secret rule, and a
first IMPORT preview procedure clearly marked as awaiting separate AWS-write
approval. Disclose that `prepare` uploads a versioned S3 backup, and stop the
procedure before IMPORT execution. Do not touch the user's modified Member B
files.

## Task 6: Verify and independently review

Run:

```text
python -m pytest infrastructure/member-d/import/test_adoption.py infrastructure/member-d/import/test_prepare_import.py infrastructure/member-d/test_template.py -q
python -m pytest -q
```

Inspect the diff for secrets, AWS write calls, unsafe stack names, ownership
leakage, deletion/replacement paths, and accidental Member B changes. Delegate
an independent reviewer to check the implementation against the approved 11
conditions. Address findings with new failing tests first.

Deliver the design, modified-file list, full test output, review result,
ownership table and exact first-preview instructions. Stop before push or any
AWS action.

## Task 7: Close final-review evidence gaps with TDD

**Files:**

- Modify: `infrastructure/member-d/import/adoption.py`
- Modify: `infrastructure/member-d/import/prepare_import.py`
- Modify: both import test modules
- Modify: the design, adoption runbook, database setup, Member D README,
  troubleshooting and Team Report source

Record focused failing tests before implementation. Introduce a distinct
IMPORT-preview evidence phase that accepts only an empty
`REVIEW_IN_PROGRESS` target and compares it with the original absent-target
baseline. Recollect fresh `IMPORT_COMPLETE` evidence before validating the
final UPDATE and use it for existing role/API/authorizer/core-table inputs;
keep query-input bucket, storage-delete function and inference URL as explicit
operator-reviewed new inputs rather than expanding scope to a Media Stack.

Add an executable read-only `verify-update-rollback` gate that compares exact
`UPDATE_ROLLBACK_COMPLETE` ownership and complete sanitized Lambda/API evidence
with the saved `IMPORT_COMPLETE` baseline. Keep `verify-post-import` strict to
`IMPORT_COMPLETE`. Tighten Change Set wire validation so `QueryFunction`
requires the exact string `Replacement: "False"`, while Add actions permit
only that string or an omitted field.

Finally, document the actual Lambda secret boundary: the trusted AWS CLI child
receives the complete service response and applies JMESPath `--query` before
stdout reaches Python. Prove fail-closed behavior with a sentinel secret and
retain the requirement for explicit user acceptance of this trust boundary
before any future AWS step.
