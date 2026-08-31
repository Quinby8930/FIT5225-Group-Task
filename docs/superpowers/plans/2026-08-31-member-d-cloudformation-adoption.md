# Member D CloudFormation Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed preparation tool and make the Member D SAM template
capable of adopting the existing Query Lambda, API Gateway integration, and
sixteen live routes without deleting or duplicating them.

**Architecture:** A pure Python core validates sanitized AWS snapshots and
generates a plain CloudFormation import-only template plus
`resources-to-import.json`. A thin AWS CLI adapter gathers only allowlisted
non-secret fields and backs up the exact live Lambda zip to a private artifact
bucket. After import, the maintained SAM template reuses the existing physical
Lambda name and stack-owned IAM role so the normal update occurs in place.

**Tech Stack:** Python 3.12 standard library, pytest, AWS CLI v2,
CloudFormation/SAM, YAML.

**Spec:**
`docs/superpowers/specs/2026-08-31-member-d-cloudformation-adoption-design.md`

## Global Constraints

- Engineering mode is Coursework; do not build a generic CloudFormation
  migration framework.
- The tool must never create, execute, or delete a CloudFormation change set.
- The tool must refuse AWS preparation unless the caller ARN is exactly the
  current account's `user/fit5225-cli-deployer` identity.
- Do not serialize or print `INTERNAL_API_KEY`, Cognito tokens, passwords,
  presigned Lambda download URLs, or Lambda environment secret values.
- Never select Member B's `/upload-url` or `/asset-urls` routes for import.
- Preserve physical Lambda name `PacificBioArchive-QueryLambda`, integration
  `fbjojun`, the sixteen audited route IDs, and the three existing DynamoDB
  tables.
- Preserve the stack-owned `QueryLambdaRole` in place. Audit its complete
  processed-template and live definition before editing it, and reject its
  removal or replacement in the normal update change set.
- Use `DeletionPolicy: Retain` and `UpdateReplacePolicy: Retain` for all
  imported resources.
- Require an explicitly approved same-account, same-region artifact bucket that
  is already private, encrypted, versioned, and readable for rollback. Never
  create a bucket or change a shared SAM bucket as an implicit preparation step.
- Generated, account-specific files live only under
  `infrastructure/member-d/import/.work/` and remain untracked.
- Do not execute AWS writes during implementation or verification.
- After committing this reviewed plan, use exactly three implementation
  commits. Together with design commit `8fe571d` and the plan commit, the
  adoption work remains within five commits.

---

## File structure

- Create `infrastructure/member-d/import/__init__.py` — package boundary.
- Create `infrastructure/member-d/import/adoption.py` — route contract,
  snapshot validation, import template generation, and resource identifier
  generation; no subprocess or network calls.
- Create `infrastructure/member-d/import/prepare_import.py` — AWS CLI adapter,
  sanitized snapshot collection, exact Lambda package backup, and command-line
  entry point.
- Create `infrastructure/member-d/import/test_adoption.py` — pure validation and
  generation tests.
- Create `infrastructure/member-d/import/test_prepare_import.py` — adapter,
  redaction, package-hash, and CLI behavior tests.
- Modify `infrastructure/member-d/dynamodb.yaml` — preserve existing Lambda and
  IAM role during the post-import update.
- Modify `infrastructure/member-d/test_template.py` — assert physical-resource
  reuse, explicit role policy, and retain policy.
- Create `docs/member-d/aws-resource-adoption.md` — exact non-Root audit,
  import-change-set review, update, and rollback runbook.
- Modify `docs/member-d/database-setup.md`,
  `docs/member-d/troubleshooting.md`, and `infrastructure/member-d/README.md` —
  route existing deployments through the adoption runbook.
- Modify `.gitignore` — exclude the `.work/` directory.

---

### Task 1: Pure adoption contract and import generation

**Files:**
- Create: `infrastructure/member-d/import/__init__.py`
- Create: `infrastructure/member-d/import/adoption.py`
- Test: `infrastructure/member-d/import/test_adoption.py`

**Interfaces:**
- Produces `ROUTES_BY_LOGICAL_ID: dict[str, RouteContract]` for exactly sixteen
  existing Member D routes.
- Produces `AdoptionError(ValueError)` for fail-closed validation errors.
- Produces `validate_snapshot(snapshot: Mapping[str, Any]) -> None`.
- Produces `build_import_template(snapshot: Mapping[str, Any], artifact:
  CodeArtifact) -> dict[str, Any]`.
- Produces `build_resources_to_import(snapshot: Mapping[str, Any]) ->
  list[dict[str, Any]]`.
- Produces `build_parameters_to_reuse(snapshot: Mapping[str, Any]) ->
  list[dict[str, Any]]`; every entry uses `UsePreviousValue: true` and no
  `ParameterValue`.
- Produces `assert_runtime_unchanged(before: Mapping[str, Any], after:
  Mapping[str, Any]) -> None` for post-import identity/configuration checks.
- Consumes no AWS SDK, subprocess, file-system, or network state.

- [ ] **Step 1: Write failing route-selection and validation tests**

Create `test_adoption.py` with a sanitized live-state builder and the first
behavioral tests:

```python
from copy import deepcopy

import pytest

from adoption import (
    AdoptionError,
    ROUTES_BY_LOGICAL_ID,
    assert_runtime_unchanged,
    validate_snapshot,
)


def valid_snapshot():
    routes = []
    for index, (logical_id, contract) in enumerate(
        ROUTES_BY_LOGICAL_ID.items(), start=1
    ):
        routes.append(
            {
                "RouteId": f"route{index:02d}",
                "RouteKey": contract.route_key,
                "Target": "integrations/fbjojun",
                "AuthorizationType": contract.authorization_type,
                "AuthorizerId": (
                    "7ir7fs" if contract.authorization_type == "JWT" else None
                ),
            }
        )
    return {
        "caller": {"Arn": "arn:aws:iam::111122223333:user/fit5225-cli-deployer"},
        "region": "ap-southeast-2",
        "stack": {
            "name": "PacificBioArchive-Database",
            "status": "UPDATE_ROLLBACK_COMPLETE",
            "template": {
                "AWSTemplateFormatVersion": "2010-09-09",
                "Resources": {
                    "FilesTable": {"Type": "AWS::DynamoDB::Table"},
                    "SubscriptionsTable": {"Type": "AWS::DynamoDB::Table"},
                    "NotificationsTable": {"Type": "AWS::DynamoDB::Table"},
                    "QueryLambdaRole": {"Type": "AWS::IAM::Role"},
                },
            },
            "managed": {
                "FilesTable": "PacificBioArchiveFiles",
                "SubscriptionsTable": "PacificBioArchiveSubscriptions",
                "NotificationsTable": "PacificBioArchiveNotifications",
                "QueryLambdaRole": "PacificBioArchive-QueryLambdaRole",
            },
        },
        "api": {
            "id": "2dd2aqb32j",
            "stage": {"StageName": "dev", "AutoDeploy": True},
            "authorizer": {"AuthorizerId": "7ir7fs", "AuthorizerType": "JWT"},
            "routes": routes,
        },
        "function": {
            "FunctionName": "PacificBioArchive-QueryLambda",
            "Runtime": "python3.12",
            "Handler": "lambda_function.handler",
            "Role": "arn:aws:iam::111122223333:role/PacificBioArchive-QueryLambdaRole",
            "Timeout": 30,
            "MemorySize": 512,
            "PackageType": "Zip",
            "Architectures": ["x86_64"],
            "Layers": [],
            "EphemeralStorage": {"Size": 512},
            "VpcConfig": {
                "SubnetIds": [],
                "SecurityGroupIds": [],
                "Ipv6AllowedForDualStack": False,
            },
            "FileSystemConfigs": [],
            "KmsKeyArn": None,
            "DeadLetterConfig": {},
            "TracingConfig": {"Mode": "PassThrough"},
            "LoggingConfig": {"LogFormat": "Text"},
            "CodeSigningConfigArn": None,
            "RuntimeManagementConfig": {"UpdateRuntimeOn": "Auto"},
            "ReservedConcurrentExecutions": None,
            "CodeSha256": "Zml4dHVyZS1kaWdlc3Q=",
            "environment_names": [
                "REPO_BACKEND",
                "DYNAMODB_TABLE",
                "SUBSCRIPTIONS_TABLE",
                "INTERNAL_API_KEY",
                "NOTIFICATIONS_TABLE",
                "CORS_ORIGINS",
                "TAG_DETECTOR_BACKEND",
            ],
            "safe_environment": {
                "REPO_BACKEND": "dynamodb",
                "DYNAMODB_TABLE": "PacificBioArchiveFiles",
                "SUBSCRIPTIONS_TABLE": "PacificBioArchiveSubscriptions",
                "NOTIFICATIONS_TABLE": "PacificBioArchiveNotifications",
                "CORS_ORIGINS": "http://localhost:3000",
                "TAG_DETECTOR_BACKEND": "remote",
            },
            "resource_policy": {
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "apigateway.amazonaws.com"},
                        "Action": "lambda:InvokeFunction",
                        "Resource": (
                            "arn:aws:lambda:ap-southeast-2:111122223333:"
                            "function:PacificBioArchive-QueryLambda"
                        ),
                    }
                ]
            },
        },
        "integration": {
            "IntegrationId": "fbjojun",
            "IntegrationType": "AWS_PROXY",
            "IntegrationMethod": "POST",
            "PayloadFormatVersion": "2.0",
            "IntegrationUri": (
                "arn:aws:apigateway:ap-southeast-2:lambda:path/2015-03-31/"
                "functions/arn:aws:lambda:ap-southeast-2:111122223333:"
                "function:PacificBioArchive-QueryLambda/invocations"
            ),
        },
        "type_schemas": {
            "AWS::Lambda::Function": ["/properties/FunctionName"],
            "AWS::ApiGatewayV2::Integration": [
                "/properties/ApiId",
                "/properties/IntegrationId",
            ],
            "AWS::ApiGatewayV2::Route": [
                "/properties/ApiId",
                "/properties/RouteId",
            ],
        },
        "owned_physical_ids": set(),
    }


def test_valid_snapshot_accepts_exact_sixteen_member_d_routes():
    snapshot = valid_snapshot()
    validate_snapshot(snapshot)
    assert len(ROUTES_BY_LOGICAL_ID) == 16


def test_root_caller_is_rejected():
    snapshot = valid_snapshot()
    snapshot["caller"]["Arn"] = "arn:aws:iam::111122223333:root"
    with pytest.raises(AdoptionError, match="Root"):
        validate_snapshot(snapshot)


def test_primary_identifier_schema_mismatch_is_rejected():
    snapshot = valid_snapshot()
    snapshot["type_schemas"]["AWS::ApiGatewayV2::Route"] = [
        "/properties/RouteKey"
    ]
    with pytest.raises(AdoptionError, match="primary identifier"):
        validate_snapshot(snapshot)


def test_post_import_runtime_comparison_rejects_route_or_function_change():
    before = valid_snapshot()
    after = deepcopy(before)
    after["api"]["routes"][0]["RouteId"] = "replacement-route"
    with pytest.raises(AdoptionError, match="runtime changed"):
        assert_runtime_unchanged(before, after)


def test_member_b_route_is_rejected_from_candidate_routes():
    snapshot = valid_snapshot()
    snapshot["api"]["routes"].append(
        {
            "RouteId": "broute",
            "RouteKey": "POST /upload-url",
            "Target": "integrations/media",
            "AuthorizationType": "JWT",
            "AuthorizerId": "7ir7fs",
        }
    )
    validate_snapshot(snapshot)
    assert "POST /upload-url" not in {
        contract.route_key for contract in ROUTES_BY_LOGICAL_ID.values()
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["api"]["routes"].pop(), "missing"),
        (
            lambda value: value["api"]["routes"][0].update(
                {"Target": "integrations/wrong"}
            ),
            "integration",
        ),
        (
            lambda value: value["integration"].update(
                {"PayloadFormatVersion": "1.0"}
            ),
            "payload",
        ),
        (
            lambda value: value["function"]["environment_names"].append(
                "UNEXPECTED_SECRET"
            ),
            "environment",
        ),
        (
            lambda value: value["function"].update({"PackageType": "Image"}),
            "package",
        ),
    ],
)
def test_snapshot_mismatch_fails_closed(mutation, message):
    snapshot = valid_snapshot()
    mutation(snapshot)
    with pytest.raises(AdoptionError, match=message):
        validate_snapshot(snapshot)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python -m pytest infrastructure/member-d/import/test_adoption.py -q
```

Expected: collection fails because `adoption` and its public interfaces do not
exist.

- [ ] **Step 3: Implement the minimal route contract and validator**

Create `__init__.py` empty and implement `adoption.py` with this public shape:

```python
from dataclasses import dataclass
from typing import Any, Mapping


class AdoptionError(ValueError):
    pass


@dataclass(frozen=True)
class RouteContract:
    route_key: str
    authorization_type: str


ROUTES_BY_LOGICAL_ID = {
    "AuthTestRoute": RouteContract("GET /auth-test", "JWT"),
    "QueryByTagsRoute": RouteContract("POST /query/by-tags", "JWT"),
    "QueryBySpeciesRoute": RouteContract("POST /query/by-species", "JWT"),
    "QueryByThumbnailRoute": RouteContract("GET /query/by-thumbnail", "JWT"),
    "QueryByFileRoute": RouteContract("POST /query/by-file", "JWT"),
    "EditTagsRoute": RouteContract("POST /tags/edit", "JWT"),
    "DeleteFilesRoute": RouteContract("POST /files/delete", "JWT"),
    "SubscribeRoute": RouteContract("POST /notifications/subscribe", "JWT"),
    "UnsubscribeRoute": RouteContract("DELETE /notifications/subscribe", "JWT"),
    "SubscriptionsRoute": RouteContract(
        "GET /notifications/subscriptions", "JWT"
    ),
    "NotificationsRoute": RouteContract("GET /notifications", "JWT"),
    "ReserveUploadRoute": RouteContract(
        "POST /internal/uploads/reserve", "NONE"
    ),
    "AcquireProcessingRoute": RouteContract(
        "POST /internal/files/{file_id}/processing", "NONE"
    ),
    "CompleteFileRoute": RouteContract(
        "PUT /internal/files/{file_id}/complete", "NONE"
    ),
    "FailFileRoute": RouteContract(
        "PUT /internal/files/{file_id}/failed", "NONE"
    ),
    "AuthorizeAssetsRoute": RouteContract(
        "POST /internal/assets/authorize", "NONE"
    ),
}


def validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Reject any state that cannot be imported without changing live traffic."""
    # Validate non-Root caller, stable stack, exact four managed logical IDs,
    # exact function name/configuration/environment-key set/resource policy,
    # integration identity/configuration, one live route for every contract,
    # shared target, JWT/NONE boundaries, and absence of foreign ownership.
```

Implement only the checks exercised by the tests, using route-key lookup rather
than list position. Extra non-D routes remain allowed but are never selected.
Reject an alias-qualified integration URI and any live Lambda property that the
import builder does not explicitly reproduce. Require the caller ARN to end in
`user/fit5225-cli-deployer`, not merely to be non-Root.

- [ ] **Step 4: Run the validator tests and verify GREEN**

Run the Task 1 test command. Expected: all current tests pass.

- [ ] **Step 5: Write failing import-generation tests**

Extend `test_adoption.py`:

```python
from adoption import (
    CodeArtifact,
    build_import_template,
    build_resources_to_import,
)


def test_import_manifest_contains_lambda_integration_and_sixteen_routes():
    manifest = build_resources_to_import(valid_snapshot())
    assert len(manifest) == 18
    assert {item["LogicalResourceId"] for item in manifest} == {
        "QueryFunction",
        "QueryIntegration",
        *ROUTES_BY_LOGICAL_ID,
    }
    assert all("OPTIONS" not in str(item) for item in manifest)
    assert all("upload-url" not in str(item) for item in manifest)
    assert all("asset-urls" not in str(item) for item in manifest)


def test_import_template_retains_every_imported_resource_without_secret_value():
    template = build_import_template(
        valid_snapshot(),
        CodeArtifact(
            bucket="private-artifacts",
            key="backups/code.zip",
            version_id="version-1",
        ),
    )
    imported = {"QueryFunction", "QueryIntegration", *ROUTES_BY_LOGICAL_ID}
    for logical_id in imported:
        assert template["Resources"][logical_id]["DeletionPolicy"] == "Retain"
        assert template["Resources"][logical_id]["UpdateReplacePolicy"] == "Retain"
    assert template["Parameters"]["InternalApiKey"] == {
        "Type": "String",
        "NoEcho": True,
        "MinLength": 1,
    }
    rendered = str(template)
    assert "fixture-secret" not in rendered
    assert "POST /upload-url" not in rendered


def test_import_template_keeps_exact_live_lambda_rollback_package():
    template = build_import_template(
        valid_snapshot(),
        CodeArtifact("private-artifacts", "backups/code.zip", "version-1"),
    )
    function = template["Resources"]["QueryFunction"]
    assert function["Type"] == "AWS::Lambda::Function"
    assert function["Properties"]["FunctionName"] == "PacificBioArchive-QueryLambda"
    assert function["Properties"]["Code"] == {
        "S3Bucket": "private-artifacts",
        "S3Key": "backups/code.zip",
        "S3ObjectVersion": "version-1",
    }
    assert function["Properties"]["Environment"]["Variables"][
        "INTERNAL_API_KEY"
    ] == {"Ref": "InternalApiKey"}


def test_import_parameters_reuse_existing_internal_key_without_reading_it():
    parameters = build_parameters_to_reuse(valid_snapshot())
    assert {
        "ParameterKey": "InternalApiKey",
        "UsePreviousValue": True,
    } in parameters
    assert all("ParameterValue" not in item for item in parameters)
```

- [ ] **Step 6: Run the generation tests and verify RED**

Expected: imports fail because `CodeArtifact` and both builder functions are
missing.

- [ ] **Step 7: Implement import template and manifest generation**

Add:

```python
@dataclass(frozen=True)
class CodeArtifact:
    bucket: str
    key: str
    version_id: str


def build_resources_to_import(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_snapshot(snapshot)
    # Return QueryFunction by FunctionName, QueryIntegration by ApiId and
    # IntegrationId, then routes in deterministic logical-ID order by ApiId and
    # RouteId.


def build_import_template(
    snapshot: Mapping[str, Any], artifact: CodeArtifact
) -> dict[str, Any]:
    validate_snapshot(snapshot)
    # Deep-copy the processed stack template. Add InternalApiKey as NoEcho.
    # Add the exact live Lambda, integration, and sixteen routes. Add Retain and
    # UpdateReplacePolicy Retain to every imported logical resource.


def build_parameters_to_reuse(
    snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    # Require InternalApiKey to be an existing stack parameter. Return every
    # current parameter key with UsePreviousValue=True and no value.
```

Use the safe environment allowlist from the snapshot and add only
`INTERNAL_API_KEY: {Ref: InternalApiKey}`. Use the stack-owned role through
`Fn::GetAtt: [QueryLambdaRole, Arn]`. Use live route IDs only in the manifest,
not as unsupported properties in the route template. Reproduce every supported
live Lambda property from the sanitized snapshot, including architectures,
layers, ephemeral storage, VPC/file-system configuration, KMS, dead-letter,
tracing, logging, code signing, runtime management, and reserved concurrency.
If a property cannot be represented exactly, `validate_snapshot` must reject
the state instead of omitting it.

If `InternalApiKey` is not already a stack parameter, fail closed. Do not ask
the tool to read it and do not generate a parameter file containing it. The
runbook records this as a separate manual blocker requiring the exact C-shared
value and explicit approval.

- [ ] **Step 8: Run all Task 1 tests and verify GREEN**

Run:

```bash
python -m pytest infrastructure/member-d/import/test_adoption.py -q
```

Expected: all tests pass.

- [ ] **Step 9: Review the Task 1 checkpoint without committing**

```bash
git diff --check
git diff -- infrastructure/member-d/import
```

Expected: only the pure generator and its tests are present. Commit Tasks 1 and
2 together after the AWS adapter passes, preserving the three-implementation-
commit budget.

---

### Task 2: Safe AWS CLI audit and exact Lambda backup

**Files:**
- Create: `infrastructure/member-d/import/prepare_import.py`
- Test: `infrastructure/member-d/import/test_prepare_import.py`

**Interfaces:**
- Consumes Task 1 `validate_snapshot`, `build_import_template`,
  `build_resources_to_import`, and `CodeArtifact`.
- Produces `AwsCli.json(*args: str) -> Any` with captured, non-echoed output.
- Produces `collect_snapshot(cli: AwsCli, config: AuditConfig) -> dict`.
- Produces `backup_function_package(cli: AwsCli, code: Mapping[str, str],
  artifact_bucket: str, downloader: Callable[[str, Path], None]) ->
  CodeArtifact`.
- Produces CLI subcommands `audit`, `prepare`, and `validate-change-set`; none
  creates or executes a CloudFormation change set.

- [ ] **Step 1: Write failing adapter and redaction tests**

Create a `FakeAwsCli` that records argument tuples and returns sanitized fixture
responses. Test that `collect_snapshot`:

```python
def test_collection_queries_only_allowlisted_environment_values(tmp_path):
    cli = FakeAwsCli()
    snapshot = collect_snapshot(cli, fixture_config(tmp_path))
    commands = [" ".join(call) for call in cli.calls]
    configuration_calls = [
        call for call in commands if "get-function-configuration" in call
    ]
    assert len(configuration_calls) == 1
    assert "INTERNAL_API_KEY" not in configuration_calls[0]
    assert "Environment.Variables.REPO_BACKEND" in configuration_calls[0]
    assert "Environment.Variables.CORS_ORIGINS" in configuration_calls[0]
    assert "internal-secret" not in str(snapshot)


def test_audit_refuses_root_before_writing_snapshot(tmp_path):
    cli = FakeAwsCli(caller_arn="arn:aws:iam::111122223333:root")
    with pytest.raises(AdoptionError, match="Root"):
        run_audit(cli, fixture_config(tmp_path))
    assert not (tmp_path / "snapshot.json").exists()


def test_generated_snapshot_contains_no_download_url_or_secret(tmp_path):
    cli = FakeAwsCli()
    path = run_audit(cli, fixture_config(tmp_path))
    text = path.read_text(encoding="utf-8")
    assert "X-Amz-Signature" not in text
    assert "internal-secret" not in text
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
python -m pytest infrastructure/member-d/import/test_prepare_import.py -q
```

Expected: collection fails because `prepare_import` does not exist.

- [ ] **Step 3: Implement the AWS CLI adapter and sanitized audit**

Use an argument-list subprocess wrapper, never `shell=True`:

```python
class AwsCli:
    def json(self, *args: str):
        completed = subprocess.run(
            ["aws", *args, "--output", "json", "--no-cli-pager"],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)
```

For Lambda configuration, AWS returns `Environment.Variables` as part of the
configuration response. Hold the raw response only in memory, derive the exact
key-name set and explicitly allowlisted non-secret values, then discard it.
Never print, log, persist, return, or interpolate the raw map or secret value
into an exception. Tests must prove stdout, stderr, exceptions, and snapshots
do not contain the injected secret. Obtain the code location only in memory
from `lambda get-function --query Code`; never include it in the snapshot or an
error message.

Collect and preserve or reject the complete function shape: package type,
architectures, layers, ephemeral storage, VPC, file-system configurations, KMS,
dead-letter configuration, tracing, logging, code-signing configuration,
runtime-management configuration, reserved concurrency, execution role, and
Lambda resource policy. Read the existing role's trust policy, attached
policies, inline policies, path, permission boundary, and tags. Compare them
with the stack's processed `QueryLambdaRole` definition and retain a sanitized
canonical representation for Task 3; stop if the two views cannot be
reconciled exactly.

Collect active stacks and their resource summaries so Task 1 can reject a
candidate physical ID owned by another stack. Collect the resource-type schemas
for Lambda Function, API Gateway V2 Integration, and Route, and verify their
primary identifier fields before generating the manifest.

- [ ] **Step 4: Run redaction tests and verify GREEN**

Run the Task 2 test command. Expected: redaction and Root guard tests pass.

- [ ] **Step 5: Write failing exact-package backup tests**

Add tests using an injected downloader and temporary bytes:

```python
from io import BytesIO
from unittest.mock import ANY
from zipfile import ZipFile


def lambda_zip_bytes():
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("lambda_function.py", "def handler(event, context): pass\n")
    return buffer.getvalue()


def test_backup_verifies_live_sha_and_uploads_content_addressed_key(tmp_path):
    package = lambda_zip_bytes()
    code_sha = base64.b64encode(hashlib.sha256(package).digest()).decode()
    cli = FakeAwsCli(version_id="version-1", uploaded_checksum=code_sha)

    def downloader(_url, destination):
        destination.write_bytes(package)

    artifact = backup_function_package(
        cli,
        {"Location": "https://signed.invalid", "CodeSha256": code_sha},
        "private-artifacts",
        downloader,
    )

    assert artifact.key == f"member-d/adoption/{hashlib.sha256(package).hexdigest()}.zip"
    assert artifact.version_id == "version-1"
    assert cli.put_objects == [
        {
            "source": ANY,
            "bucket": "private-artifacts",
            "key": artifact.key,
            "checksum_sha256": code_sha,
            "server_side_encryption": "AES256",
        }
    ]


def test_backup_rejects_hash_mismatch_without_upload():
    with pytest.raises(AdoptionError, match="SHA-256"):
        backup_function_package(
            FakeAwsCli(),
            {"Location": "https://signed.invalid", "CodeSha256": "wrong"},
            "private-artifacts",
            lambda _url, path: path.write_bytes(lambda_zip_bytes()),
        )


@pytest.mark.parametrize(
    "bucket_state",
    ["public", "unencrypted", "wrong-account", "wrong-region", "unversioned", "unreadable"],
)
def test_prepare_rejects_unsafe_artifact_bucket(bucket_state):
    with pytest.raises(AdoptionError, match="artifact bucket"):
        verify_artifact_bucket(FakeAwsCli(bucket_state=bucket_state), "artifacts")


def test_backup_rejects_non_zip():
    digest = base64.b64encode(hashlib.sha256(b"not-a-zip").digest()).decode()
    with pytest.raises(AdoptionError, match="zip"):
        backup_function_package(
            FakeAwsCli(),
            {"Location": "https://signed.invalid", "CodeSha256": digest},
            "private-artifacts",
            lambda _url, path: path.write_bytes(b"not-a-zip"),
        )


def test_backup_rejects_uploaded_checksum_mismatch():
    package = lambda_zip_bytes()
    digest = base64.b64encode(hashlib.sha256(package).digest()).decode()
    cli = FakeAwsCli(version_id="version-1", uploaded_checksum="wrong")
    with pytest.raises(AdoptionError, match="uploaded checksum"):
        backup_function_package(
            cli,
            {"Location": "https://signed.invalid", "CodeSha256": digest},
            "private-artifacts",
            lambda _url, path: path.write_bytes(package),
        )
```

- [ ] **Step 6: Run package tests and verify RED**

Expected: package tests fail because backup behavior is missing.

- [ ] **Step 7: Implement exact package backup and `prepare`**

Before download, verify `get-bucket-location`, `get-public-access-block`,
`get-bucket-encryption`, `get-bucket-versioning`, bucket ownership controls,
bucket policy/status, and current-account access. Require the current
account/region, all four public-access blocks, server-side encryption,
`Status=Enabled` versioning, and a verified read path for the uploaded object.
Treat the bucket name as an explicit operator input. If any prerequisite fails,
stop; do not create a bucket, enable a setting, or modify a shared SAM bucket.

Use `TemporaryDirectory`, stream the signed URL into a temporary zip without
logging the URL, validate it with `ZipFile.testzip()`, compute both raw SHA-256
and base64 SHA-256, and compare with the live value using
`hmac.compare_digest`. Upload with `s3api put-object`, an explicit SHA-256
checksum, AES256 encryption, and the content-addressed key:

```python
cli.run(
    "s3api",
    "put-object",
    "--bucket", artifact_bucket,
    "--key", artifact_key,
    "--body", str(package_path),
    "--checksum-algorithm", "SHA256",
    "--checksum-sha256", base64_digest,
    "--server-side-encryption", "AES256",
)
```

Require a non-empty upload `VersionId`, then call `head-object` with
`--version-id` and `--checksum-mode ENABLED`. Reject a missing or mismatched
checksum. Store the version ID in `CodeArtifact` so the import template uses
`Code.S3ObjectVersion`.

`prepare` writes deterministic UTF-8 JSON files with sorted keys to `.work/`:

- `sanitized-snapshot.json`
- `import-template.json`
- `resources-to-import.json`
- `import-parameters.json`, containing only `UsePreviousValue: true` entries
  and no literal values

It prints only these file paths, the artifact `s3://` URI, and a statement that
no CloudFormation change set was created. It never prints the signed URL or any
environment value not in the allowlist.

- [ ] **Step 8: Add CLI parser tests**

Test that `audit` and `prepare` require explicit region, stack, API, authorizer,
integration, and function arguments; `prepare` additionally requires
`--artifact-bucket`. `audit --baseline .work/sanitized-snapshot.json` compares the
live runtime fingerprint with the saved pre-import state while allowing only
the expected CloudFormation ownership change. `validate-change-set` requires region, stack, and change-set
name plus `--expected-type IMPORT|UPDATE`. Assert there is no `execute`,
`deploy`, `delete`, or `create-change-set` subcommand.

- [ ] **Step 9: Write and implement an import-only change-set validator**

First add a failing pure behavior test:

```python
def test_change_set_must_contain_exactly_eighteen_imports():
    expected = build_resources_to_import(valid_snapshot())
    changes = [
        {
            "ResourceChange": {
                "Action": "Import",
                "LogicalResourceId": item["LogicalResourceId"],
                "ResourceType": item["ResourceType"],
                "Replacement": "False",
            }
        }
        for item in expected
    ]
    validate_import_change_set(changes, expected)


@pytest.mark.parametrize("action", ["Add", "Modify", "Remove", "Dynamic"])
def test_change_set_rejects_every_non_import_action(action):
    expected = build_resources_to_import(valid_snapshot())
    changes = [
        {
            "ResourceChange": {
                "Action": action,
                "LogicalResourceId": "QueryFunction",
                "ResourceType": "AWS::Lambda::Function",
                "Replacement": "False",
            }
        }
    ]
    with pytest.raises(AdoptionError, match="18 Import"):
        validate_import_change_set(changes, expected)


def test_processed_update_reuses_function_and_has_no_implicit_role():
    processed = {
        "Resources": {
            "QueryLambdaRole": {"Type": "AWS::IAM::Role"},
            "QueryFunction": {
                "Type": "AWS::Lambda::Function",
                "Properties": {
                    "FunctionName": "PacificBioArchive-QueryLambda",
                    "Role": {"Fn::GetAtt": ["QueryLambdaRole", "Arn"]},
                },
            },
            "QueryIntegration": {"Type": "AWS::ApiGatewayV2::Integration"},
        }
    }
    changes = [
        {
            "ResourceChange": {
                "Action": "Modify",
                "LogicalResourceId": "QueryFunction",
                "ResourceType": "AWS::Lambda::Function",
                "Replacement": "False",
            }
        }
    ]
    validate_update_change_set(changes, processed)


def test_processed_update_rejects_implicit_role_or_adopted_replacement():
    processed = {
        "Resources": {
            "QueryFunctionRole": {"Type": "AWS::IAM::Role"},
            "QueryFunction": {"Type": "AWS::Lambda::Function"},
        }
    }
    changes = [
        {
            "ResourceChange": {
                "Action": "Modify",
                "LogicalResourceId": "QueryFunction",
                "ResourceType": "AWS::Lambda::Function",
                "Replacement": "True",
            }
        }
    ]
    with pytest.raises(AdoptionError, match="replacement|implicit role"):
        validate_update_change_set(changes, processed)


def test_processed_update_rejects_stack_owned_role_replacement_or_removal():
    processed = {
        "Resources": {
            "QueryLambdaRole": {"Type": "AWS::IAM::Role"},
            "QueryFunction": {"Type": "AWS::Lambda::Function"},
            "QueryIntegration": {"Type": "AWS::ApiGatewayV2::Integration"},
        }
    }
    changes = [
        {
            "ResourceChange": {
                "Action": "Modify",
                "LogicalResourceId": "QueryLambdaRole",
                "ResourceType": "AWS::IAM::Role",
                "Replacement": "True",
            }
        }
    ]
    with pytest.raises(AdoptionError, match="QueryLambdaRole|replacement"):
        validate_update_change_set(changes, processed)
```

Implement `validate_import_change_set` and `validate_update_change_set` in
`adoption.py`. `validate_update_change_set` must reject replacement/removal of
the adopted Lambda, integration, sixteen routes, or stack-owned
`QueryLambdaRole`, and reject any processed template containing
`QueryFunctionRole`. A `QueryLambdaRole` change must be either absent/no-op or
an in-place modify whose processed definition matches the audited role
continuity rules. The CLI subcommand accepts
`--expected-type IMPORT` or `--expected-type UPDATE`, calls only
`cloudformation describe-change-set` and `get-template --template-stage
Processed`, invokes the matching pure validator, and prints a sanitized success
summary. It must not execute the change set.

- [ ] **Step 10: Run Task 1 and Task 2 tests**

```bash
python -m pytest infrastructure/member-d/import/test_adoption.py \
  infrastructure/member-d/import/test_prepare_import.py -q
```

Expected: all pass and no network request is made.

- [ ] **Step 11: Commit Tasks 1 and 2 as one implementation commit**

```bash
git add infrastructure/member-d/import/prepare_import.py \
  infrastructure/member-d/import/test_prepare_import.py \
  infrastructure/member-d/import/adoption.py \
  infrastructure/member-d/import/test_adoption.py \
  infrastructure/member-d/import/__init__.py
git commit -m "feat: prepare member d adoption safely"
```

---

### Task 3: Reuse the existing Lambda and IAM role in the maintained SAM stack

**Files:**
- Modify: `infrastructure/member-d/dynamodb.yaml`
- Modify: `infrastructure/member-d/test_template.py`

**Interfaces:**
- Consumes the import logical IDs `QueryFunction`, `QueryIntegration`, and the
  sixteen route logical IDs from Task 1.
- Preserves physical function name `PacificBioArchive-QueryLambda` and existing
  role logical ID `QueryLambdaRole`.
- Consumes the sanitized, reconciled processed-template/live role contract from
  Task 2. Do not edit the role in the maintained template until that contract
  exists and passes validation.
- Produces the same application environment variables and least-privilege
  permissions currently asserted by the template tests.

- [ ] **Step 1: Write failing physical-reuse and retain-policy tests**

Add to `test_template.py`:

```python
IMPORTED_ROUTE_LOGICAL_IDS = {
    "AuthTestRoute",
    "QueryByTagsRoute",
    "QueryBySpeciesRoute",
    "QueryByThumbnailRoute",
    "QueryByFileRoute",
    "EditTagsRoute",
    "DeleteFilesRoute",
    "SubscribeRoute",
    "UnsubscribeRoute",
    "SubscriptionsRoute",
    "NotificationsRoute",
    "ReserveUploadRoute",
    "AcquireProcessingRoute",
    "CompleteFileRoute",
    "FailFileRoute",
    "AuthorizeAssetsRoute",
}


def test_query_function_reuses_imported_name_and_stack_owned_role(template):
    function = template["Resources"]["QueryFunction"]
    assert function["DeletionPolicy"] == "Retain"
    assert function["UpdateReplacePolicy"] == "Retain"
    assert function["Properties"]["FunctionName"] == "PacificBioArchive-QueryLambda"
    assert function["Properties"]["Role"] == {
        "Fn::GetAtt": "QueryLambdaRole.Arn"
    }
    assert "Policies" not in function["Properties"]

    role = template["Resources"]["QueryLambdaRole"]
    assert role["Type"] == "AWS::IAM::Role"
    assert role["DeletionPolicy"] == "Retain"
    assert role["UpdateReplacePolicy"] == "Retain"
    assert role["Properties"]["RoleName"] == "PacificBioArchive-QueryLambdaRole"


def test_imported_integration_and_routes_are_retained(template):
    resources = template["Resources"]
    for logical_id in {"QueryIntegration", *IMPORTED_ROUTE_LOGICAL_IDS}:
        assert resources[logical_id]["DeletionPolicy"] == "Retain"
        assert resources[logical_id]["UpdateReplacePolicy"] == "Retain"
```

Replace `test_query_function_policies_are_least_privilege` with an assertion
against an explicit `QueryLambdaRole` resource, keeping the exact action and
resource expectations.

- [ ] **Step 2: Run template tests and verify RED**

```bash
python -m pytest infrastructure/member-d/test_template.py -q
```

Expected: new assertions fail because the template currently generates a new
role and unnamed function and lacks retain policies.

- [ ] **Step 3: Reproduce and extend the explicit existing IAM role**

Start from the exact `QueryLambdaRole` properties produced by Task 2's
processed-template/live reconciliation. Preserve `RoleName`, path, trust
policy, permissions boundary, managed policy ARNs, existing inline policy
names/documents, tags, and every other supported property after canonical
normalization. Add `DeletionPolicy: Retain` and `UpdateReplacePolicy: Retain`
at the resource level.

Move the current DynamoDB, S3, storage-delete Lambda, and SNS statements from
`QueryFunction.Properties.Policies` into an existing compatible inline policy,
or append one deterministically named `QueryServiceAccess` policy when no
compatible policy exists. Keep the exact action/resource assertions already in
`test_template.py`; do not widen any resource to `*`, remove any pre-existing
permission, change the trust relationship, or remove a boundary. If the live
and processed-template role definitions disagree, stop instead of modifying
the SAM template.

- [ ] **Step 4: Update QueryFunction in place**

Set:

```yaml
QueryFunction:
  Type: AWS::Serverless::Function
  DeletionPolicy: Retain
  UpdateReplacePolicy: Retain
  Properties:
    FunctionName: PacificBioArchive-QueryLambda
    Role: !GetAtt QueryLambdaRole.Arn
```

Keep its CodeUri, handler, runtime, timeout, 1024 MiB memory, and complete
environment contract unchanged. Remove its `Policies` property.

- [ ] **Step 5: Add retain policies to adopted API resources**

Add `DeletionPolicy: Retain` and `UpdateReplacePolicy: Retain` to
`QueryIntegration` and only the sixteen imported route logical IDs. Do not mark
the ten newly created OPTIONS routes as imported. The ten include
`AuthTestOptionsRoute`; the POST and DELETE subscription methods share
`SubscribeOptionsRoute`.

- [ ] **Step 6: Run template tests and verify GREEN**

Run the Task 3 pytest command. Expected: all pass.

- [ ] **Step 7: Build and validate the maintained SAM template**

```bash
sam validate --template-file infrastructure/member-d/dynamodb.yaml --lint
sam build --template-file infrastructure/member-d/dynamodb.yaml
```

Expected: valid SAM/CloudFormation template and a successful build. Source tests
prove the explicit role binding. The adoption runbook additionally requires
`validate-change-set --expected-type UPDATE` against the non-executed normal
update change set; that validator inspects CloudFormation's processed template
to prove there is no generated `QueryFunctionRole` and no replacement/removal
of an adopted resource or `QueryLambdaRole`. Any role change must be an in-place
modify whose processed properties pass the role continuity comparison. If
local SAM CLI is unavailable, record that fact and run both
commands in non-Root CloudShell before creating either change set.

- [ ] **Step 8: Commit Task 3**

```bash
git add infrastructure/member-d/dynamodb.yaml \
  infrastructure/member-d/test_template.py
git commit -m "fix: preserve adopted member d resources"
```

---

### Task 4: Operator runbook, ignore rules, and complete verification

**Files:**
- Modify: `.gitignore`
- Create: `docs/member-d/aws-resource-adoption.md`
- Modify: `docs/member-d/database-setup.md`
- Modify: `docs/member-d/troubleshooting.md`
- Modify: `infrastructure/member-d/README.md`

**Interfaces:**
- Consumes the `audit` and `prepare` commands from Task 2.
- Produces a human-operated two-approval workflow: IMPORT review/execution,
  followed by UPDATE review/execution.
- Does not contain a real key, password, token, account ID, presigned URL, or
  generated route ID.

- [ ] **Step 1: Ignore generated adoption state**

Add exactly:

```gitignore
infrastructure/member-d/import/.work/
```

- [ ] **Step 2: Write the adoption runbook**

Create `docs/member-d/aws-resource-adoption.md` with these exact phases:

1. Root console only: enable console access for existing
   `fit5225-cli-deployer` using an autogenerated one-time password and required
   reset; sign out of Root.
2. New IAM-user CloudShell: verify the caller ARN is exactly the current
   account's `user/fit5225-cli-deployer`, not merely a non-Root principal.
3. Clone/pull the repository and run local import-tool tests.
4. Run `audit` with the audited stack/API/authorizer/integration/function IDs.
5. Ask the operator for an explicitly approved artifact bucket. Run `prepare`
   only after the tool proves it is same-account, same-region, private,
   encrypted, versioned, and readable. Never enable settings or modify the
   shared `aws-sam-cli-managed-default` bucket as part of this procedure.
6. Require `import-parameters.json` to reuse the existing `InternalApiKey`
   parameter with `UsePreviousValue: true`. If the parameter is absent, stop;
   obtain the exact C-shared value and a separate approval for a secure
   interactive procedure. Never place the key in argv, history, `.work/`,
   `samconfig.toml`, a parameter file, output, or screenshots.
7. Create an IMPORT change set using the generated template, manifest, and
   reuse-only parameter file. Describe it, run `validate-change-set`, require
   exactly eighteen Import actions, and stop for user approval before execute.
8. After approval, execute the IMPORT change set. Run drift detection, rerun
   `audit --baseline` against the saved pre-import snapshot, and verify the
   Lambda resource policy, role, integration, and route matrix are unchanged.
9. Package the maintained SAM template, then create but do not execute the
   normal UPDATE change set with the exact non-secret parameters and
   `InternalApiKey` reuse shown below. Validate the processed template and stop
   for a second approval.
10. After approval, execute UPDATE, verify endpoints, and run the reservations
    verify/backfill/verify sequence only while all Files/Reservations mutations
    are paused.

The runbook must include these concrete CloudShell commands, with every
state-changing command labeled `WRITE`:

```bash
set +x
export AWS_REGION=ap-southeast-2
STACK_NAME=PacificBioArchive-Database
API_ID=2dd2aqb32j
AUTHORIZER_ID=7ir7fs
INTEGRATION_ID=fbjojun
FUNCTION_NAME=PacificBioArchive-QueryLambda
WORK_DIR=infrastructure/member-d/import/.work
IMPORT_CHANGE_SET=member-d-adopt-existing
UPDATE_CHANGE_SET=member-d-deploy-current

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
CALLER_ARN=$(aws sts get-caller-identity --query Arn --output text)
test "$CALLER_ARN" = "arn:aws:iam::$ACCOUNT_ID:user/fit5225-cli-deployer"

python infrastructure/member-d/import/prepare_import.py audit \
  --region "$AWS_REGION" --stack "$STACK_NAME" --api-id "$API_ID" \
  --authorizer-id "$AUTHORIZER_ID" --integration-id "$INTEGRATION_ID" \
  --function-name "$FUNCTION_NAME"

read -r -p "Approved private, encrypted, versioned artifact bucket: " ARTIFACT_BUCKET
python infrastructure/member-d/import/prepare_import.py prepare \
  --region "$AWS_REGION" --stack "$STACK_NAME" --api-id "$API_ID" \
  --authorizer-id "$AUTHORIZER_ID" --integration-id "$INTEGRATION_ID" \
  --function-name "$FUNCTION_NAME" --artifact-bucket "$ARTIFACT_BUCKET"

# WRITE — create only; do not execute.
aws cloudformation create-change-set \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$IMPORT_CHANGE_SET" --change-set-type IMPORT \
  --template-body "file://$WORK_DIR/import-template.json" \
  --resources-to-import "file://$WORK_DIR/resources-to-import.json" \
  --parameters "file://$WORK_DIR/import-parameters.json" \
  --capabilities CAPABILITY_NAMED_IAM

python infrastructure/member-d/import/prepare_import.py validate-change-set \
  --region "$AWS_REGION" --stack "$STACK_NAME" \
  --change-set-name "$IMPORT_CHANGE_SET" --expected-type IMPORT

# STOP: first explicit user approval is required here.
# WRITE — run only after that approval.
aws cloudformation execute-change-set \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$IMPORT_CHANGE_SET"
aws cloudformation wait stack-import-complete \
  --region "$AWS_REGION" --stack-name "$STACK_NAME"

DRIFT_ID=$(aws cloudformation detect-stack-drift \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --query StackDriftDetectionId --output text)
aws cloudformation wait stack-drift-detection-complete \
  --region "$AWS_REGION" --stack-drift-detection-id "$DRIFT_ID"
aws cloudformation describe-stack-drift-detection-status \
  --region "$AWS_REGION" --stack-drift-detection-id "$DRIFT_ID" \
  --output table --no-cli-pager

python infrastructure/member-d/import/prepare_import.py audit \
  --region "$AWS_REGION" --stack "$STACK_NAME" --api-id "$API_ID" \
  --authorizer-id "$AUTHORIZER_ID" --integration-id "$INTEGRATION_ID" \
  --function-name "$FUNCTION_NAME" \
  --baseline "$WORK_DIR/sanitized-snapshot.json"

MEDIA_BUCKET=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" --stack-name PacificBioArchive-Media \
  --query "Stacks[0].Outputs[?OutputKey=='MediaBucketName'].OutputValue | [0]" \
  --output text)
STORAGE_DELETE_ARN=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" --stack-name PacificBioArchive-Media \
  --query "Stacks[0].Outputs[?OutputKey=='StorageDeleteFunctionArn'].OutputValue | [0]" \
  --output text)
STORAGE_DELETE_FUNCTION_NAME=$(aws lambda get-function-configuration \
  --region "$AWS_REGION" --function-name "$STORAGE_DELETE_ARN" \
  --query FunctionName --output text)

# WRITE — uploads a packaged template/code object; it does not update the stack.
sam package --region "$AWS_REGION" \
  --template-file infrastructure/member-d/dynamodb.yaml \
  --s3-bucket "$ARTIFACT_BUCKET" --s3-prefix member-d/current \
  --output-template-file "$WORK_DIR/packaged-template.yaml"

# WRITE — create only; do not execute.
aws cloudformation create-change-set \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$UPDATE_CHANGE_SET" --change-set-type UPDATE \
  --template-body "file://$WORK_DIR/packaged-template.yaml" \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --parameters \
    ParameterKey=ExistingHttpApiId,ParameterValue="$API_ID" \
    ParameterKey=ExistingJwtAuthorizerId,ParameterValue="$AUTHORIZER_ID" \
    ParameterKey=QueryInputBucketName,ParameterValue="$MEDIA_BUCKET" \
    ParameterKey=StorageDeleteFunctionName,ParameterValue="$STORAGE_DELETE_FUNCTION_NAME" \
    ParameterKey=InferenceApiBaseUrl,ParameterValue=https://pacificchive-ml-chidpnuwue.ap-southeast-1.fcapp.run \
    ParameterKey=InternalApiKey,UsePreviousValue=true

python infrastructure/member-d/import/prepare_import.py validate-change-set \
  --region "$AWS_REGION" --stack "$STACK_NAME" \
  --change-set-name "$UPDATE_CHANGE_SET" --expected-type UPDATE

# STOP: second explicit user approval is required here.
# WRITE — run only after that approval.
aws cloudformation execute-change-set \
  --region "$AWS_REGION" --stack-name "$STACK_NAME" \
  --change-set-name "$UPDATE_CHANGE_SET"
aws cloudformation wait stack-update-complete \
  --region "$AWS_REGION" --stack-name "$STACK_NAME"
```

Every AWS command that can modify state must be clearly labeled. The tool's own
commands stop before change-set creation. Include explicit prohibitions against
`delete-route`, `delete-integration`, `delete-function`, `delete-stack`, and
Root deployment.

- [ ] **Step 3: Correct the existing deployment documentation**

Update the three existing docs so they distinguish:

- fresh AWS account with no Member D resources: normal SAM deployment;
- this existing account with unmanaged live resources: complete the adoption
  runbook first;
- `UPDATE_ROLLBACK_COMPLETE` caused by `RouteKey AlreadyExists`: never retry the
  same template or delete the live routes.

Remove any instruction implying that rerunning `sam deploy --guided` is safe for
the current account before adoption.

- [ ] **Step 4: Run focused tests**

```bash
python -m pytest infrastructure/member-d/import/test_adoption.py \
  infrastructure/member-d/import/test_prepare_import.py \
  infrastructure/member-d/test_template.py -q
```

Expected: all pass.

- [ ] **Step 5: Run Member D application tests**

```bash
python -m pytest backend/lambdas/query/tests -q
```

Expected: all pass.

- [ ] **Step 6: Run repository safety checks**

```bash
git diff --check
git check-ignore infrastructure/member-d/import/.work/sanitized-snapshot.json
rg -n "INTERNAL_API_KEY=.*|aws_secret_access_key|aws_session_token|X-Amz-Signature" \
  infrastructure/member-d/import docs/member-d infrastructure/member-d
git status --short --untracked-files=all
```

Expected: `git check-ignore` prints the `.work` path; no secret value, generated
`.work` file, zip, JSON snapshot, whitespace error, or unexpected user file is
staged. References to the variable name
`INTERNAL_API_KEY` are allowed; assignments containing a value are not.

- [ ] **Step 7: Request code review**

Use `superpowers:requesting-code-review` against the four implementation tasks.
Require the reviewer to check import identifiers, rollback package fidelity,
secret handling, Root refusal, exact route scope, SAM logical-ID continuity,
and absence of AWS writes.

- [ ] **Step 8: Commit Task 4**

```bash
git add .gitignore docs/member-d/aws-resource-adoption.md \
  docs/member-d/database-setup.md docs/member-d/troubleshooting.md \
  infrastructure/member-d/README.md
git commit -m "docs: add member d adoption runbook"
```

- [ ] **Step 9: Final local verification**

Rerun Tasks 4 Steps 4–6 from the committed tree. Confirm the two pre-existing
uncommitted Member B documentation changes remain separate and unstaged.

---

## Execution handoff

No command in this plan performs an AWS import or deployment. Implementation
produces tested preparation artifacts and a runbook. The AWS `IMPORT` and normal
`UPDATE` change sets each require a later explicit user approval after their
contents are displayed.
