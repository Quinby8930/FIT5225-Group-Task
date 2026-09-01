import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import traceback
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from unittest.mock import ANY
from urllib.parse import quote
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import adoption

from adoption import (
    AdoptionError,
    CodeArtifact,
    build_import_template,
    build_resources_to_import,
    validate_import_change_set,
    validate_update_change_set,
)
from prepare_import import (
    AuditConfig,
    AwsCli,
    backup_function_package,
    collect_snapshot,
    run_prepare,
    run_audit,
    verify_artifact_bucket,
    _parse_processed_template,
)
from test_adoption import (
    _EXPECTED_IMPORT_RESOURCES,
    _maintained_role_target,
    _route_scoped_lambda_policy,
    _update_processed,
    approved_role_drift_snapshot,
    valid_snapshot,
)


_FIXTURE_CODE_SHA256 = "APsUW+8+ymZvVYmfkaKba20+sWzR3PMJPDimXIiqoIY="
_FIXTURE_ARTIFACT_KEY = (
    "member-d/adoption/"
    "00fb145bef3eca666f55899f91a29b6b6d3eb16cd1dcf3093c38a65c88aaa086.zip"
)
_EXPECTED_COMMIT = "a" * 40


def test_aws_cli_json_accepts_successful_empty_response(monkeypatch):
    def successful_empty_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr("prepare_import.subprocess.run", successful_empty_run)

    assert AwsCli().json("lambda", "get-function-concurrency") == {}


def test_aws_cli_optional_json_accepts_only_the_named_aws_error(monkeypatch):
    def policy_not_found_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=254,
            stdout="",
            stderr=(
                "An error occurred (PolicyNotFoundException) when calling "
                "the GetResourcePolicy operation"
            ),
        )

    monkeypatch.setattr("prepare_import.subprocess.run", policy_not_found_run)

    assert AwsCli().optional_json(
        "PolicyNotFoundException",
        "dynamodb",
        "get-resource-policy",
    ) is None


def test_aws_cli_optional_json_rejects_a_different_aws_error(monkeypatch):
    def access_denied_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=254,
            stdout="",
            stderr="An error occurred (AccessDeniedException)",
        )

    monkeypatch.setattr("prepare_import.subprocess.run", access_denied_run)

    with pytest.raises(AdoptionError, match="AWS CLI query failed"):
        AwsCli().optional_json(
            "PolicyNotFoundException",
            "dynamodb",
            "get-resource-policy",
        )


class FakeAwsCli:
    def __init__(self, caller_arn="arn:aws:iam::111122223333:user/fit5225-cli-deployer", version_id="version-1", uploaded_checksum=None, bucket_state="safe", code_sha=None, template_body=None):
        self.calls = []
        self.put_objects = []
        self.caller_arn = caller_arn
        self.version_id = version_id
        self.uploaded_checksum = uploaded_checksum
        self.bucket_state = bucket_state
        self.code_sha = code_sha
        self.template_body = template_body

    def json(self, *args):
        self.calls.append(args)
        command = " ".join(args)
        if "get-caller-identity" in command:
            return {"Arn": self.caller_arn, "Account": "111122223333"}
        if "describe-stacks" in command:
            if "--query" in args:
                return {
                    "StackName": "PacificBioArchive-Database",
                    "ParameterNames": ["InternalApiKey"],
                }
            return {"Stacks": [{"StackName": "PacificBioArchive-Database", "StackStatus": "UPDATE_ROLLBACK_COMPLETE", "Parameters": [{"ParameterKey": "InternalApiKey", "ParameterValue": "internal-secret"}]}]}
        if "get-template" in command:
            return {
                "TemplateBody": (
                    self.template_body
                    if self.template_body is not None
                    else valid_snapshot()["stack"]["template"]
                )
            }
        if "list-stack-resources" in command:
            return {"StackResourceSummaries": [{"LogicalResourceId": logical_id, "PhysicalResourceId": physical_id} for logical_id, physical_id in valid_snapshot()["stack"]["managed"].items()]}
        if "list-stacks" in command:
            return {"StackSummaries": [{"StackName": "PacificBioArchive-Database", "StackStatus": "UPDATE_ROLLBACK_COMPLETE"}]}
        if "detect-stack-resource-drift" in command:
            return {
                "StackResourceDrift": {
                    "StackResourceDriftStatus": "IN_SYNC",
                    "PropertyDifferences": [],
                }
            }
        if "describe-type" in command:
            type_name = args[args.index("--type-name") + 1]
            return {"Schema": json.dumps({"primaryIdentifier": valid_snapshot()["type_schemas"][type_name]})}
        if "get-function-configuration" in command:
            configuration = {
                **valid_snapshot()["function"],
                "Environment": {
                    "Names": deepcopy(
                        valid_snapshot()["function"]["environment_names"]
                    ),
                    "Variables": deepcopy(
                        valid_snapshot()["function"]["safe_environment"]
                    ),
                },
            }
            if self.code_sha:
                configuration["CodeSha256"] = self.code_sha
            return configuration
        if "get-function" in command and "get-function-" not in command:
            return {
                "Location": (
                    "https://signed.invalid/"
                    "?X-Amz-Signature=should-not-leak"
                )
            }
        if "lambda list-tags" in command:
            return {"Tags": {}}
        if "get-policy" in command:
            return {"Policy": json.dumps(valid_snapshot()["function"]["resource_policy"])}
        if "get-function-concurrency" in command:
            return {}
        if "get-runtime-management-config" in command:
            return valid_snapshot()["function"]["RuntimeManagementConfig"]
        if "get-role" in command:
            return {"Role": {"Path": "/", "RoleName": "PacificBioArchive-QueryLambdaRole", "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": []}, "PermissionsBoundary": None}}
        if "list-attached-role-policies" in command:
            return {"AttachedPolicies": []}
        if "list-role-policies" in command:
            return {"PolicyNames": []}
        if "list-role-tags" in command:
            return {"Tags": []}
        if "dynamodb describe-table" in command:
            table = valid_snapshot()["reservations_table"]
            return {
                "Table": {
                    key: deepcopy(table[key])
                    for key in (
                        "TableName",
                        "TableStatus",
                        "TableArn",
                        "AttributeDefinitions",
                        "KeySchema",
                        "GlobalSecondaryIndexes",
                        "LocalSecondaryIndexes",
                        "StreamSpecification",
                        "DeletionProtectionEnabled",
                        "Replicas",
                        "VectorIndexes",
                        "GlobalTableWitnesses",
                    )
                }
                | {
                    "BillingModeSummary": {
                        "BillingMode": table["BillingMode"]
                    },
                    "TableClassSummary": {"TableClass": table["TableClass"]},
                }
            }
        if "dynamodb describe-time-to-live" in command:
            return {
                "TimeToLiveDescription": {
                    "TimeToLiveStatus": "DISABLED"
                }
            }
        if "dynamodb describe-continuous-backups" in command:
            return {
                "ContinuousBackupsDescription": {
                    "PointInTimeRecoveryDescription": {
                        "PointInTimeRecoveryStatus": "DISABLED"
                    }
                }
            }
        if "dynamodb list-tags-of-resource" in command:
            return {"Tags": []}
        if "dynamodb get-resource-policy" in command:
            return {}
        if "dynamodb describe-kinesis-streaming-destination" in command:
            return {"KinesisDataStreamDestinations": []}
        if "dynamodb describe-contributor-insights" in command:
            return {"ContributorInsightsStatus": "DISABLED"}
        if "get-integration" in command:
            return valid_snapshot()["integration"]
        if "get-routes" in command:
            return {"Items": valid_snapshot()["api"]["routes"]}
        if "get-stage" in command:
            return valid_snapshot()["api"]["stage"]
        if "get-authorizers" in command:
            return {"Items": [valid_snapshot()["api"]["authorizer"]]}
        if "get-bucket-location" in command:
            if self.bucket_state == "null-region":
                return {"LocationConstraint": None}
            return {"LocationConstraint": "ap-southeast-2" if self.bucket_state != "wrong-region" else "us-east-1"}
        if "list-buckets" in command:
            foreign = self.bucket_state in {"wrong-account", "authorized-cross-account"}
            return {"Buckets": [] if foreign else [{"Name": "private-artifacts"}, {"Name": "artifacts"}]}
        if "get-public-access-block" in command:
            return {"PublicAccessBlockConfiguration": {"BlockPublicAcls": self.bucket_state != "public", "IgnorePublicAcls": self.bucket_state != "public", "BlockPublicPolicy": self.bucket_state != "public", "RestrictPublicBuckets": self.bucket_state != "public"}}
        if "get-bucket-encryption" in command:
            return {} if self.bucket_state == "unencrypted" else {"ServerSideEncryptionConfiguration": {"Rules": [{}]}}
        if "get-bucket-versioning" in command:
            return {"Status": "Enabled" if self.bucket_state != "unversioned" else "Suspended"}
        if "get-bucket-ownership-controls" in command:
            return {"OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}}
        if "get-bucket-policy-status" in command:
            return {"PolicyStatus": {"IsPublic": self.bucket_state == "public"}}
        if "head-bucket" in command:
            if self.bucket_state in {"wrong-account", "unreadable"}:
                raise RuntimeError("bucket access unavailable")
            return {}
        if "head-object" in command:
            checksum = self.uploaded_checksum
            if checksum == "from-put-object":
                checksum = self.put_objects[-1]["checksum_sha256"]
            return {"VersionId": self.version_id, "ChecksumSHA256": checksum}
        raise AssertionError(f"unexpected fake AWS call: {args}")

    def optional_json(self, _ignored_error_code, *args):
        result = self.json(*args)
        return result or None

    def pause(self, seconds):
        self.calls.append(("pause", seconds))

    def run(self, *args):
        self.calls.append(args)
        command = " ".join(args)
        if "put-object" not in command:
            raise AssertionError(f"unexpected fake AWS command: {args}")
        values = dict(zip(args[::2], args[1::2]))
        self.put_objects.append({"source": ANY, "bucket": values["--bucket"], "key": values["--key"], "checksum_sha256": values["--checksum-sha256"], "server_side_encryption": values["--server-side-encryption"]})
        return {"VersionId": self.version_id}


def fixture_config(tmp_path):
    return AuditConfig(region="ap-southeast-2", stack="PacificBioArchive-Database", api="2dd2aqb32j", authorizer="7ir7fs", integration="fbjojun", function="PacificBioArchive-QueryLambda", workdir=tmp_path)


_EXPECTED_UPDATE_PARAMETER_VALUES = {
    "ExistingHttpApiId": "2dd2aqb32j",
    "ExistingJwtAuthorizerId": "7ir7fs",
    "QueryInputBucketName": "pacificbioarchive-media-test",
    "StorageDeleteFunctionName": "PacificBioArchive-StorageDeleteFunction",
    "InferenceApiBaseUrl": (
        "https://pacificchive-ml-chidpnuwue.ap-southeast-1.fcapp.run"
    ),
    "AllowLegacyProcessingCallbacks": "false",
}


def _maintained_source_template():
    source_path = (
        Path(__file__).parents[3]
        / "infrastructure"
        / "member-d"
        / "dynamodb.yaml"
    )
    return deepcopy(
        _parse_processed_template(source_path.read_text(encoding="utf-8"))
    )


def _packaged_update_template():
    template = _maintained_source_template()
    template["Resources"]["QueryFunction"]["Properties"]["CodeUri"] = {
        "Bucket": "aws-sam-cli-managed-default-samclisourcebucket",
        "Key": "member-d/current/query-function.zip",
        "Version": "packaged-version-42",
    }
    return template


def _update_artifact():
    return CodeArtifact(
        "aws-sam-cli-managed-default-samclisourcebucket",
        "member-d/current/query-function.zip",
        "packaged-version-42",
    )


def _built_update_template():
    template = _maintained_source_template()
    template["Resources"]["QueryFunction"]["Properties"]["CodeUri"] = (
        "QueryFunction"
    )
    return template


def _processed_update_template():
    maintained = _maintained_source_template()
    processed = _update_processed(_maintained_role_target())
    for key, value in maintained.items():
        if key not in {"Resources", "Transform"}:
            processed[key] = deepcopy(value)
    processed["Resources"]["QueryFunction"]["Properties"]["Code"] = {
        "S3Bucket": "aws-sam-cli-managed-default-samclisourcebucket",
        "S3Key": "member-d/current/query-function.zip",
        "S3ObjectVersion": "packaged-version-42",
    }
    return processed


def _explicit_update_parameters():
    parameters = [
        {"ParameterKey": key, "ParameterValue": value}
        for key, value in _EXPECTED_UPDATE_PARAMETER_VALUES.items()
    ]
    parameters.append(
        {"ParameterKey": "InternalApiKey", "UsePreviousValue": True}
    )
    return parameters


def _first_update_parameters(mask="*****"):
    parameters = _explicit_update_parameters()
    parameters[-1] = {
        "ParameterKey": "InternalApiKey",
        "ParameterValue": mask,
    }
    return parameters


def _current_hardening_parameters():
    return [
        {
            "ParameterKey": key,
            "ParameterValue": (
                "true" if key == "AllowLegacyProcessingCallbacks" else value
            ),
        }
        for key, value in _EXPECTED_UPDATE_PARAMETER_VALUES.items()
    ] + [
        {"ParameterKey": "InternalApiKey", "ParameterValue": "not-exposed"}
    ]


def _hardening_runtime_evidence():
    import prepare_import

    processed = {"Resources": {"QueryFunction": {"Type": "AWS::Lambda::Function"}}}
    checksum = base64.b64encode(b"h" * 32).decode()
    variables = {
        name: (
            "true"
            if name == "ALLOW_LEGACY_PROCESSING_CALLBACKS"
            else f"fixture-{name.lower()}"
        )
        for name in prepare_import._HARDENING_ENVIRONMENT_NAMES
    }
    live_function = {
        "FunctionName": "PacificBioArchive-QueryLambda",
        "Runtime": "python3.12",
        "Timeout": 30,
        "CodeSha256": checksum,
        "Environment": {
            "Names": sorted(set(variables) | {"INTERNAL_API_KEY"}),
            "Variables": variables,
        },
    }
    drift = {
        "LogicalResourceId": "QueryFunction",
        "Status": "IN_SYNC",
        "Differences": [],
    }
    artifact = CodeArtifact(
        "private-artifacts",
        "member-d/update/fixture.zip",
        "version-1",
        checksum,
    )
    return processed, drift, live_function, artifact


def test_hardening_parameter_transition_accepts_only_callback_disable():
    adoption.validate_hardening_parameter_transition(
        _current_hardening_parameters(),
        _explicit_update_parameters(),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda parameters: next(
            item for item in parameters if item["ParameterKey"] == "ExistingHttpApiId"
        ).update({"ParameterValue": "attacker-api"}),
        lambda parameters: next(
            item
            for item in parameters
            if item["ParameterKey"] == "AllowLegacyProcessingCallbacks"
        ).update({"ParameterValue": "true"}),
        lambda parameters: next(
            item for item in parameters if item["ParameterKey"] == "InternalApiKey"
        ).update({"UsePreviousValue": False, "ParameterValue": "rotated"}),
    ],
    ids=("non-callback", "callback-not-disabled", "secret-not-reused"),
)
def test_hardening_parameter_transition_rejects_any_other_change(mutation):
    candidate = _explicit_update_parameters()
    mutation(candidate)

    with pytest.raises(AdoptionError, match="hardening|parameter|callback|reuse"):
        adoption.validate_hardening_parameter_transition(
            _current_hardening_parameters(),
            candidate,
        )


def test_hardening_runtime_evidence_accepts_bound_in_sync_transition():
    import prepare_import

    processed, drift, live_function, artifact = _hardening_runtime_evidence()
    prepare_import.validate_hardening_runtime_evidence(
        processed,
        deepcopy(processed),
        _current_hardening_parameters(),
        _explicit_update_parameters(),
        drift,
        live_function,
        artifact,
        expected_callback="false",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda current, candidate, parameters, drift, function, artifact: candidate.update(
            {"Outputs": {"Injected": {"Value": "bad"}}}
        ),
        lambda current, candidate, parameters, drift, function, artifact: next(
            item for item in parameters if item["ParameterKey"] == "ExistingHttpApiId"
        ).update({"ParameterValue": "attacker-api"}),
        lambda current, candidate, parameters, drift, function, artifact: drift.update(
            {"Status": "MODIFIED"}
        ),
        lambda current, candidate, parameters, drift, function, artifact: function.update(
            {"CodeSha256": base64.b64encode(b"x" * 32).decode()}
        ),
        lambda current, candidate, parameters, drift, function, artifact: function[
            "Environment"
        ]["Names"].append("UNEXPECTED_SECRET"),
    ],
    ids=(
        "template-change",
        "non-callback-parameter",
        "drift-not-in-sync",
        "code-sha-mismatch",
        "environment-name-anomaly",
    ),
)
def test_hardening_runtime_evidence_rejects_unbound_or_extra_change(mutation):
    import prepare_import

    current, drift, live_function, artifact = _hardening_runtime_evidence()
    candidate = deepcopy(current)
    parameters = _explicit_update_parameters()
    mutation(current, candidate, parameters, drift, live_function, artifact)

    with pytest.raises(
        AdoptionError,
        match="hardening|template|parameter|IN_SYNC|code|environment",
    ):
        prepare_import.validate_hardening_runtime_evidence(
            current,
            candidate,
            _current_hardening_parameters(),
            parameters,
            drift,
            live_function,
            artifact,
            expected_callback="false",
        )


def _update_changes():
    return [{
        "ResourceChange": {
            "Action": "Modify",
            "LogicalResourceId": "QueryLambdaRole",
            "ResourceType": "AWS::IAM::Role",
            "Replacement": "False",
        }
    }]


def _import_changes(snapshot):
    return [
        {
            "ResourceChange": {
                "Action": "Import",
                "LogicalResourceId": item["LogicalResourceId"],
                "ResourceType": item["ResourceType"],
                "Replacement": "False",
            }
        }
        for item in build_resources_to_import(snapshot)
    ]


def _json_safe_snapshot(snapshot):
    result = deepcopy(snapshot)
    result["owned_physical_ids"] = sorted(result["owned_physical_ids"])
    return result


def _write_change_set_validation_files(workdir):
    import prepare_import

    workdir.mkdir(parents=True, exist_ok=True)
    _write_built_code(workdir / "built-code")
    _write_source_code(workdir / "source-code")
    snapshot = approved_role_drift_snapshot()
    import_template = build_import_template(
        snapshot,
        CodeArtifact(
            "private-artifacts",
            _FIXTURE_ARTIFACT_KEY,
            "import-version-1",
        ),
    )
    import_parameters = [
        {"ParameterKey": "InternalApiKey", "UsePreviousValue": True}
    ]
    built = _built_update_template()
    packaging_cli = FakeAwsCli(uploaded_checksum="from-put-object")
    artifact = prepare_import.package_update_function(
        packaging_cli,
        built_code_dir=workdir / "built-code",
        source_code_dir=workdir / "source-code",
        artifact_bucket="private-artifacts",
        output_template=workdir / "packaged-template.yaml",
        built_template=built,
        maintained_template=_maintained_source_template(),
        region="ap-southeast-2",
        trusted_dependency_manifest=_trusted_dependency_manifest(),
    )
    packaged = _parse_processed_template(
        (workdir / "packaged-template.yaml").read_text(encoding="utf-8")
    )
    processed_update = _processed_update_template()
    processed_update["Resources"]["QueryFunction"]["Properties"]["Code"] = {
        "S3Bucket": artifact.bucket,
        "S3Key": artifact.key,
        "S3ObjectVersion": artifact.version_id,
    }
    (workdir / "sanitized-snapshot.json").write_text(
        json.dumps(_json_safe_snapshot(snapshot)),
        encoding="utf-8",
    )
    (workdir / "import-template.json").write_text(
        json.dumps(import_template),
        encoding="utf-8",
    )
    (workdir / "import-parameters.json").write_text(
        json.dumps(import_parameters),
        encoding="utf-8",
    )
    (workdir / "built-template.yaml").write_text(
        json.dumps(built),
        encoding="utf-8",
    )
    (workdir / "dependency-manifest.json").write_text(
        json.dumps(_trusted_dependency_manifest()),
        encoding="utf-8",
    )
    return {
        "snapshot": snapshot,
        "import_template": import_template,
        "import_parameters": import_parameters,
        "packaged": packaged,
        "built": built,
        "processed_update": processed_update,
        "update_artifact": artifact,
        "update_checksum": packaging_cli.put_objects[-1]["checksum_sha256"],
    }


def _use_stored_snapshot_as_fresh(monkeypatch, prepare_import, files):
    monkeypatch.setattr(
        prepare_import,
        "collect_snapshot",
        lambda _cli, _config: deepcopy(files["snapshot"]),
    )


def _use_packaged_update_artifact(monkeypatch, prepare_import, files):
    monkeypatch.setattr(
        prepare_import,
        "_verify_committed_file",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        prepare_import,
        "verify_update_artifact",
        lambda *_args: files["update_artifact"],
    )


class CandidateChangeSetCli(FakeAwsCli):
    def __init__(
        self,
        *,
        change_set_type,
        changes,
        parameters,
        processed,
        status="CREATE_COMPLETE",
        execution_status="AVAILABLE",
        artifact_checksum=_FIXTURE_CODE_SHA256,
        artifact_version="import-version-1",
        stack_parameter_names=None,
    ):
        super().__init__(
            uploaded_checksum=artifact_checksum,
            code_sha=_FIXTURE_CODE_SHA256,
        )
        self.change_set_type = change_set_type
        self.changes = deepcopy(changes)
        self.parameters = deepcopy(parameters)
        self.processed = deepcopy(processed)
        self.status = status
        self.execution_status = execution_status
        self.artifact_checksum = artifact_checksum
        self.artifact_version = artifact_version
        self.stack_parameter_names = (
            ["InternalApiKey"]
            if stack_parameter_names is None
            else list(stack_parameter_names)
        )
        self.calls = []

    def json(self, *args):
        self.calls.append(args)
        if args[:2] == ("cloudformation", "describe-stacks") and "--query" in args:
            query = args[args.index("--query") + 1]
            if query == (
                "Stacks[0].{StackName:StackName,"
                "ParameterNames:Parameters[].ParameterKey}"
            ):
                return {
                    "StackName": "PacificBioArchive-Database",
                    "ParameterNames": deepcopy(self.stack_parameter_names) or None,
                }
            if query == "Stacks[0].Parameters":
                return _current_hardening_parameters()
        if args[:2] == ("cloudformation", "describe-change-set"):
            return {
                # DescribeChangeSet does not return ChangeSetType. The requested
                # validation mode is the only trustworthy type contract here.
                "Status": self.status,
                "ExecutionStatus": self.execution_status,
                "Changes": deepcopy(self.changes),
                "Parameters": deepcopy(self.parameters),
            }
        if (
            args[:2] == ("cloudformation", "get-template")
            and "--change-set-name" in args
        ):
            return {"TemplateBody": deepcopy(self.processed)}
        if args[:2] == ("s3api", "head-object"):
            return {
                "ChecksumSHA256": self.artifact_checksum,
                "VersionId": self.artifact_version,
            }
        return super().json(*args)


def _validate_change_set_args(
    workdir,
    expected_type,
    *,
    expect_role_reconciliation="true",
):
    args = [
        "validate-change-set",
        "--region",
        "ap-southeast-2",
        "--stack",
        "PacificBioArchive-Database",
        "--change-set",
        f"member-d-{expected_type.lower()}",
        "--api",
        "2dd2aqb32j",
        "--authorizer",
        "7ir7fs",
        "--integration",
        "fbjojun",
        "--function",
        "PacificBioArchive-QueryLambda",
        "--expected-type",
        expected_type,
        "--workdir",
        str(workdir),
    ]
    if expected_type == "UPDATE":
        args.extend(
            [
                "--packaged-template",
                str(workdir / "packaged-template.yaml"),
                "--built-template",
                str(workdir / "built-template.yaml"),
                "--built-code-dir",
                str(workdir / "source-code"),
                "--source-code-dir",
                str(workdir / "built-code"),
                "--dependency-manifest",
                str(workdir / "dependency-manifest.json"),
                "--artifact-bucket",
                "private-artifacts",
                "--expected-commit",
                _EXPECTED_COMMIT,
                "--expected-http-api-id",
                _EXPECTED_UPDATE_PARAMETER_VALUES["ExistingHttpApiId"],
                "--expected-jwt-authorizer-id",
                _EXPECTED_UPDATE_PARAMETER_VALUES[
                    "ExistingJwtAuthorizerId"
                ],
                "--expected-query-input-bucket",
                _EXPECTED_UPDATE_PARAMETER_VALUES["QueryInputBucketName"],
                "--expected-storage-delete-function",
                _EXPECTED_UPDATE_PARAMETER_VALUES[
                    "StorageDeleteFunctionName"
                ],
                "--expected-inference-api-base-url",
                _EXPECTED_UPDATE_PARAMETER_VALUES["InferenceApiBaseUrl"],
                "--expected-allow-legacy-processing-callbacks",
                _EXPECTED_UPDATE_PARAMETER_VALUES[
                    "AllowLegacyProcessingCallbacks"
                ],
                "--expect-role-reconciliation",
                expect_role_reconciliation,
            ]
        )
    else:
        args.extend(["--artifact-bucket", "private-artifacts"])
    return args


@pytest.mark.parametrize(
    ("status", "managed", "action", "cleanup_candidate"),
    [
        (None, set(), "prepare", False),
        (None, {"QueryFunction"}, "stop", False),
        ("REVIEW_IN_PROGRESS", set(), "inspect", True),
        (
            "IMPORT_COMPLETE",
            _EXPECTED_IMPORT_RESOURCES,
            "post-import-evidence",
            False,
        ),
        (
            "IMPORT_COMPLETE",
            _EXPECTED_IMPORT_RESOURCES - {"QueryFunction"},
            "stop",
            False,
        ),
        (
            "IMPORT_COMPLETE",
            _EXPECTED_IMPORT_RESOURCES | {"UnexpectedLogicalId"},
            "stop",
            False,
        ),
        (
            "IMPORT_ROLLBACK_COMPLETE",
            set(),
            "recovery-report",
            True,
        ),
        (
            "IMPORT_ROLLBACK_COMPLETE",
            {"QueryFunction"},
            "stop",
            False,
        ),
        (
            "IMPORT_ROLLBACK_FAILED",
            set(),
            "freeze",
            False,
        ),
        (
            "UPDATE_ROLLBACK_COMPLETE",
            _EXPECTED_IMPORT_RESOURCES,
            "verify-runtime-and-ownership",
            False,
        ),
        (
            "UPDATE_ROLLBACK_COMPLETE",
            _EXPECTED_IMPORT_RESOURCES - {"ReservationsTable"},
            "stop",
            False,
        ),
        (
            "UPDATE_ROLLBACK_COMPLETE",
            _EXPECTED_IMPORT_RESOURCES | {"UnexpectedLogicalId"},
            "stop",
            False,
        ),
    ],
    ids=(
        "target-absent",
        "target-absent-with-managed-resource",
        "review-empty-shell",
        "import-complete",
        "import-complete-partial-ownership",
        "import-complete-extra-ownership",
        "import-rollback-empty-shell",
        "import-rollback-owned-resource",
        "import-rollback-failed",
        "update-rollback-complete",
        "update-rollback-complete-partial-ownership",
        "update-rollback-complete-extra-ownership",
    ),
)
def test_query_adoption_contract_classifies_target_stack_recovery_state(
    status,
    managed,
    action,
    cleanup_candidate,
):
    classification = adoption.classify_recovery_state(status, set(managed))

    assert classification == {
        "action": action,
        "empty_shell_cleanup_candidate": cleanup_candidate,
        "deletion_requires_separate_approval": cleanup_candidate,
    }


@pytest.mark.parametrize(
    ("status", "managed"),
    [
        (None, set()),
        ("REVIEW_IN_PROGRESS", {"QueryFunction"}),
        ("IMPORT_ROLLBACK_COMPLETE", {"ReservationsTable"}),
        ("IMPORT_ROLLBACK_FAILED", set()),
    ],
    ids=(
        "absent-is-not-a-shell",
        "review-with-owned-resource",
        "rollback-with-owned-resource",
        "failed-rollback-never-cleanup",
    ),
)
def test_query_adoption_contract_never_classifies_unsafe_state_as_empty_shell(
    status,
    managed,
):
    classification = adoption.classify_recovery_state(status, set(managed))

    assert classification["empty_shell_cleanup_candidate"] is False


def test_update_contracts_match_the_maintained_template_source():
    source_path = (
        Path(__file__).parents[3]
        / "infrastructure"
        / "member-d"
        / "dynamodb.yaml"
    )
    template = _parse_processed_template(source_path.read_text(encoding="utf-8"))

    assert template["Resources"]["ReservationsTable"] == (
        adoption._maintained_reservations_table_target()
    )
    assert template["Resources"]["QueryLambdaRole"] == (
        adoption._maintained_role_target()
    )
    plain_targets = adoption._maintained_plain_resource_targets()
    assert set(template["Resources"]) == set(plain_targets) | {"QueryFunction"}
    assert {
        logical_id: template["Resources"][logical_id]
        for logical_id in plain_targets
    } == plain_targets


def test_collection_queries_only_allowlisted_environment_values(tmp_path):
    cli = FakeAwsCli()
    snapshot = collect_snapshot(cli, fixture_config(tmp_path))
    configuration_calls = [" ".join(call) for call in cli.calls if "get-function-configuration" in call]
    assert len(configuration_calls) == 1
    assert "INTERNAL_API_KEY" not in configuration_calls[0]
    assert "Environment: Environment" not in configuration_calls[0]
    assert "Names: keys(Environment.Variables)" in configuration_calls[0]
    assert "Environment.Variables.REPO_BACKEND" in configuration_calls[0]
    assert "Environment.Variables.CORS_ORIGINS" in configuration_calls[0]
    assert "FunctionName: FunctionName" in configuration_calls[0]
    assert "RevisionId: RevisionId" in configuration_calls[0]
    assert "internal-secret" not in str(snapshot)


def test_audit_refuses_root_before_writing_snapshot(tmp_path):
    with pytest.raises(AdoptionError, match="Root"):
        run_audit(FakeAwsCli(caller_arn="arn:aws:iam::111122223333:root"), fixture_config(tmp_path))
    assert not (tmp_path / "sanitized-snapshot.json").exists()


@pytest.mark.parametrize("principal", [
    "arn:aws:sts::111122223333:federated-user/fit5225-cli-deployer",
    "arn:aws:sts::111122223333:assumed-role/team/fit5225-cli-deployer",
])
def test_collection_refuses_non_iam_deployer_principal(tmp_path, principal):
    with pytest.raises(AdoptionError, match="exact IAM user"):
        run_audit(FakeAwsCli(caller_arn=principal), fixture_config(tmp_path))
    assert not (tmp_path / "sanitized-snapshot.json").exists()


def test_generated_snapshot_contains_no_download_url_or_secret(tmp_path):
    path = run_audit(FakeAwsCli(), fixture_config(tmp_path))
    text = path.read_text(encoding="utf-8")
    assert "X-Amz-Signature" not in text
    assert "internal-secret" not in text


def lambda_zip_bytes():
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("lambda_function.py", "def handler(event, context): pass\n")
    return buffer.getvalue()


_TEST_DEPENDENCY_PATH = "locked_dependency/__init__.py"
_TEST_DEPENDENCY_BYTES = b"VERSION = 'fixture'\n"


def _write_source_code(directory, *, reverse=False, mtime=1_700_000_000):
    files = [
        ("lambda_function.py", b"def handler(event, context):\n    return {'ok': True}\n"),
        ("lib/config.json", b'{"mode":"hardening"}\n'),
    ]
    if reverse:
        files.reverse()
    for relative, contents in files:
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        os.utime(path, (mtime, mtime))


def _write_built_code(directory, *, reverse=False, mtime=1_700_000_000):
    _write_source_code(directory, reverse=reverse, mtime=mtime)
    dependency = directory / _TEST_DEPENDENCY_PATH
    dependency.parent.mkdir(parents=True, exist_ok=True)
    dependency.write_bytes(_TEST_DEPENDENCY_BYTES)
    os.utime(dependency, (mtime, mtime))


def test_package_update_uploads_deterministic_content_addressed_zip(tmp_path):
    import prepare_import

    package_update = getattr(prepare_import, "package_update_function")
    built_template_value = _built_update_template()
    built_template = tmp_path / "built-template.yaml"
    built_template.write_text(
        json.dumps(built_template_value),
        encoding="utf-8",
    )
    first_code = tmp_path / "built-code-first"
    second_code = tmp_path / "built-code-second"
    source_code = tmp_path / "source-code"
    _write_source_code(source_code)
    _write_built_code(first_code, mtime=1_600_000_000)
    _write_built_code(second_code, reverse=True, mtime=1_750_000_000)

    results = []
    uploads = []
    for index, code_dir in enumerate((first_code, second_code), start=1):
        output = tmp_path / f"packaged-{index}.yaml"
        cli = FakeAwsCli(uploaded_checksum="from-put-object")
        artifact = package_update(
            cli,
            built_code_dir=code_dir,
            source_code_dir=source_code,
            artifact_bucket="private-artifacts",
            output_template=output,
            built_template=built_template_value,
            maintained_template=_maintained_source_template(),
            region="ap-southeast-2",
            trusted_dependency_manifest=_trusted_dependency_manifest(),
        )
        packaged = _parse_processed_template(output.read_text(encoding="utf-8"))
        code_uri = packaged["Resources"]["QueryFunction"]["Properties"][
            "CodeUri"
        ]
        assert code_uri == {
            "Bucket": artifact.bucket,
            "Key": artifact.key,
            "Version": artifact.version_id,
        }
        assert artifact.bucket == "private-artifacts"
        assert artifact.version_id == "version-1"
        assert artifact.key.startswith("member-d/update/")
        assert artifact.key.endswith(".zip")
        upload = cli.put_objects[-1]
        key_digest = bytes.fromhex(
            artifact.key.removeprefix("member-d/update/").removesuffix(".zip")
        )
        assert upload["checksum_sha256"] == base64.b64encode(
            key_digest
        ).decode()
        assert upload["server_side_encryption"] == "AES256"
        assert any(
            call[:2] == ("s3api", "head-object") for call in cli.calls
        )
        results.append((artifact.key, upload["checksum_sha256"]))
        uploads.append(upload)

    assert results[0] == results[1]


def test_package_update_cli_writes_pinned_packaged_template(
    tmp_path,
    monkeypatch,
):
    import prepare_import

    built_code = tmp_path / "built-code"
    _write_built_code(built_code)
    source_code = tmp_path / "source-code"
    _write_source_code(source_code)
    built_template = tmp_path / "built-template.yaml"
    built_template.write_text(
        json.dumps(_built_update_template()),
        encoding="utf-8",
    )
    output = tmp_path / "packaged-template.yaml"
    dependency_manifest = tmp_path / "dependency-manifest.json"
    dependency_manifest.write_text(
        json.dumps(_trusted_dependency_manifest()),
        encoding="utf-8",
    )
    cli = FakeAwsCli(uploaded_checksum="from-put-object")
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    monkeypatch.setattr(
        prepare_import,
        "_verify_committed_file",
        lambda *_args: None,
    )
    real_package_update = prepare_import.package_update_function

    def package_without_git_check(
        cli_arg,
        built_code_dir,
        artifact_bucket,
        output_template,
        built_template_value,
        region,
        maintained_template,
        source_code_dir,
        _expected_commit,
        trusted_dependency_manifest,
    ):
        return real_package_update(
            cli_arg,
            built_code_dir,
            artifact_bucket,
            output_template,
            built_template_value,
            region,
            maintained_template,
            source_code_dir=source_code_dir,
            expected_commit=None,
            trusted_dependency_manifest=trusted_dependency_manifest,
        )

    monkeypatch.setattr(
        prepare_import,
        "package_update_function",
        package_without_git_check,
    )

    assert prepare_import.main([
        "package-update",
        "--region", "ap-southeast-2",
        "--artifact-bucket", "private-artifacts",
        "--built-template", str(built_template),
        "--built-code-dir", str(built_code),
        "--source-code-dir", str(source_code),
        "--dependency-manifest", str(dependency_manifest),
        "--expected-commit", _EXPECTED_COMMIT,
        "--output-template", str(output),
    ]) == 0
    code_uri = _parse_processed_template(
        output.read_text(encoding="utf-8")
    )["Resources"]["QueryFunction"]["Properties"]["CodeUri"]
    assert isinstance(code_uri, dict)
    assert set(code_uri) == {"Bucket", "Key", "Version"}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda code_uri: "s3://private-artifacts/member-d/update/unpinned.zip",
        lambda code_uri: {**code_uri, "Bucket": "attacker-bucket"},
        lambda code_uri: {**code_uri, "Key": "member-d/update/wrong.zip"},
        lambda code_uri: {**code_uri, "Version": "wrong-version"},
    ],
    ids=("string-s3-uri", "wrong-bucket", "wrong-key", "wrong-version"),
)
def test_update_artifact_binding_rejects_every_unpinned_code_uri(
    tmp_path,
    mutation,
):
    import prepare_import

    package_update = getattr(prepare_import, "package_update_function")
    built_code = tmp_path / "built-code"
    _write_built_code(built_code)
    source_code = tmp_path / "source-code"
    _write_source_code(source_code)
    built_template_path = tmp_path / "built-template.yaml"
    built_template = _built_update_template()
    built_template_path.write_text(
        json.dumps(built_template),
        encoding="utf-8",
    )
    packaged_path = tmp_path / "packaged-template.yaml"
    package_update(
        FakeAwsCli(uploaded_checksum="from-put-object"),
        built_code_dir=built_code,
        source_code_dir=source_code,
        artifact_bucket="private-artifacts",
        output_template=packaged_path,
        built_template=built_template,
        maintained_template=_maintained_source_template(),
        region="ap-southeast-2",
        trusted_dependency_manifest=_trusted_dependency_manifest(),
    )
    packaged = _parse_processed_template(
        packaged_path.read_text(encoding="utf-8")
    )
    original_code_uri = packaged["Resources"]["QueryFunction"]["Properties"][
        "CodeUri"
    ]
    expected_checksum = base64.b64encode(
        bytes.fromhex(
            original_code_uri["Key"]
            .removeprefix("member-d/update/")
            .removesuffix(".zip")
        )
    ).decode()
    packaged["Resources"]["QueryFunction"]["Properties"]["CodeUri"] = (
        mutation(original_code_uri)
    )
    with pytest.raises(
        AdoptionError,
        match="artifact|Bucket|Key|Version|CodeUri|content|digest|package",
    ):
        prepare_import.verify_update_artifact(
            FakeAwsCli(uploaded_checksum=expected_checksum),
            built_code,
            packaged,
            "private-artifacts",
            "ap-southeast-2",
            source_code_dir=source_code,
            trusted_dependency_manifest=_trusted_dependency_manifest(),
        )


def test_update_artifact_binding_rejects_wrong_live_checksum(tmp_path):
    import prepare_import

    built_code = tmp_path / "built-code"
    _write_built_code(built_code)
    source_code = tmp_path / "source-code"
    _write_source_code(source_code)
    built_template = _built_update_template()
    packaged_path = tmp_path / "packaged-template.yaml"
    prepare_import.package_update_function(
        FakeAwsCli(uploaded_checksum="from-put-object"),
        built_code_dir=built_code,
        source_code_dir=source_code,
        artifact_bucket="private-artifacts",
        output_template=packaged_path,
        built_template=built_template,
        maintained_template=_maintained_source_template(),
        region="ap-southeast-2",
        trusted_dependency_manifest=_trusted_dependency_manifest(),
    )
    packaged = _parse_processed_template(
        packaged_path.read_text(encoding="utf-8")
    )

    with pytest.raises(AdoptionError, match="checksum|version|artifact"):
        prepare_import.verify_update_artifact(
            FakeAwsCli(uploaded_checksum=base64.b64encode(b"x" * 32).decode()),
            built_code,
            packaged,
            "private-artifacts",
            "ap-southeast-2",
            source_code_dir=source_code,
            trusted_dependency_manifest=_trusted_dependency_manifest(),
        )


@pytest.mark.parametrize("tamper", ["modify-source-file", "unrecorded-extra"])
def test_package_update_rejects_build_not_bound_to_first_party_source(
    tmp_path,
    tamper,
):
    import prepare_import

    source_code = tmp_path / "source-code"
    built_code = tmp_path / "built-code"
    _write_source_code(source_code)
    _write_built_code(built_code)
    if tamper == "modify-source-file":
        (built_code / "lambda_function.py").write_text(
            "def handler(event, context):\n    return {'tampered': True}\n",
            encoding="utf-8",
        )
    else:
        (built_code / "sitecustomize.py").write_text(
            "raise RuntimeError('unexpected build injection')\n",
            encoding="utf-8",
        )

    with pytest.raises(
        AdoptionError,
        match="source|built|unrecorded|unexpected|sitecustomize|differ",
    ):
        prepare_import.package_update_function(
            FakeAwsCli(uploaded_checksum="from-put-object"),
            built_code_dir=built_code,
            source_code_dir=source_code,
            artifact_bucket="private-artifacts",
            output_template=tmp_path / "packaged-template.yaml",
            built_template=_built_update_template(),
            maintained_template=_maintained_source_template(),
            region="ap-southeast-2",
            trusted_dependency_manifest=_trusted_dependency_manifest(),
        )


def _record_row(relative, contents):
    digest = base64.urlsafe_b64encode(
        hashlib.sha256(contents).digest()
    ).decode().rstrip("=")
    return f"{relative},sha256={digest},{len(contents)}\n"


def _trusted_dependency_manifest(files=None):
    if files is None:
        files = {_TEST_DEPENDENCY_PATH: _TEST_DEPENDENCY_BYTES}
    return {
        "schema": 1,
        "runtime": "python3.12",
        "architecture": "x86_64",
        "generated_files_excluded": [
            "bin/**",
            "*.dist-info/RECORD",
            "*.dist-info/INSTALLER",
            "*.dist-info/REQUESTED",
            "**/__pycache__/**",
            "**/*.pyc",
            "**/*.pyo",
            ".pytest_cache/**",
            "data/pacific_bioarchive.db",
        ],
        "files": {
            relative: {
                "sha256": hashlib.sha256(contents).hexdigest(),
                "size": len(contents),
            }
            for relative, contents in files.items()
        },
    }


def test_committed_dependency_lock_has_exact_runtime_schema_and_file_digests():
    lock = json.loads(
        Path(__file__).with_name("member-d-query-build.lock.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(lock) == {
        "schema",
        "runtime",
        "architecture",
        "generated_files_excluded",
        "files",
    }
    assert lock["schema"] == 1
    assert lock["runtime"] == "python3.12"
    assert lock["architecture"] == "x86_64"
    assert lock["generated_files_excluded"] == [
        "bin/**",
        "*.dist-info/RECORD",
        "*.dist-info/INSTALLER",
        "*.dist-info/REQUESTED",
        "**/__pycache__/**",
        "**/*.pyc",
        "**/*.pyo",
        ".pytest_cache/**",
        "data/pacific_bioarchive.db",
    ]
    assert lock["files"]
    assert all(
        isinstance(relative, str)
        and relative
        and set(metadata) == {"sha256", "size"}
        and re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
        and isinstance(metadata["size"], int)
        and metadata["size"] >= 0
        for relative, metadata in lock["files"].items()
    )
    requirements = (
        Path(__file__).parents[3]
        / "backend"
        / "lambdas"
        / "query"
        / "requirements.txt"
    ).read_text(encoding="utf-8")
    requirement_lines = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert requirement_lines
    assert all("==" in line and " --hash=sha256:" in line for line in requirement_lines)


def test_generated_runtime_artifacts_are_neither_returned_nor_packaged(
    tmp_path,
):
    import prepare_import

    source = tmp_path / "source"
    built = tmp_path / "built"
    _write_source_code(source)
    _write_built_code(built)
    excluded = {
        "package/__pycache__/module.cpython-312.pyc": b"cached bytecode",
        "package/module.pyc": b"bytecode",
        "package/module.pyo": b"optimized bytecode",
        ".pytest_cache/v/cache/nodeids": b"[]",
        "data/pacific_bioarchive.db": b"runtime sqlite state",
    }
    for relative, contents in excluded.items():
        path = built / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)

    package_files = prepare_import.validate_built_code_tree(
        source,
        built,
        trusted_dependency_manifest=_trusted_dependency_manifest(),
    )
    assert not (set(package_files) & set(excluded))

    archive_path = tmp_path / "function.zip"
    prepare_import._write_deterministic_zip(
        built,
        archive_path,
        package_files,
    )
    with ZipFile(archive_path) as archive:
        assert not (set(archive.namelist()) & set(excluded))


@pytest.mark.parametrize(
    "relative",
    [
        "ignored.log",
        "data/attacker.db",
        "cache/payload.bin",
        "package/__pycache__/payload.txt",
    ],
)
def test_any_other_untracked_or_ignored_payload_is_rejected(
    tmp_path,
    relative,
):
    import prepare_import

    source = tmp_path / "source"
    built = tmp_path / "built"
    _write_source_code(source)
    _write_built_code(built)
    payload = built / relative
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"not an approved generated artifact")

    with pytest.raises(AdoptionError, match="dependency|lock|differs"):
        prepare_import.validate_built_code_tree(
            source,
            built,
            trusted_dependency_manifest=_trusted_dependency_manifest(),
        )


def test_dependency_cannot_self_authorize_with_forged_dist_info_record(
    tmp_path,
):
    import prepare_import

    source = tmp_path / "source"
    built = tmp_path / "built"
    _write_built_code(source)
    _write_built_code(built)
    payload = b"raise RuntimeError('forged dependency executed')\n"
    (built / "sitecustomize.py").write_bytes(payload)
    record = built / "fake-1.0.dist-info" / "RECORD"
    record.parent.mkdir(parents=True)
    record.write_text(
        _record_row("sitecustomize.py", payload)
        + "fake-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )

    with pytest.raises(
        AdoptionError,
        match="dependency|manifest|trusted|fake|unapproved|unrecorded",
    ):
        prepare_import.validate_built_code_tree(
            source,
            built,
            trusted_dependency_manifest=_trusted_dependency_manifest({}),
        )


def test_dependency_record_and_payload_cannot_be_synchronously_tampered(
    tmp_path,
):
    import prepare_import

    source = tmp_path / "source"
    built = tmp_path / "built"
    _write_built_code(source)
    _write_built_code(built)
    original_payload = b"VALUE = 'trusted'\n"
    trusted_manifest = _trusted_dependency_manifest({
        "realpkg/__init__.py": original_payload,
    })

    tampered_payload = b"VALUE = 'attacker'\n"
    (built / "realpkg").mkdir()
    (built / "realpkg" / "__init__.py").write_bytes(tampered_payload)
    tampered_record_path = built / "realpkg-1.0.dist-info" / "RECORD"
    tampered_record_path.parent.mkdir()
    tampered_record_path.write_text(
        _record_row("realpkg/__init__.py", tampered_payload)
        + "realpkg-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )

    with pytest.raises(
        AdoptionError,
        match="dependency|manifest|trusted|digest|RECORD",
    ):
        prepare_import.validate_built_code_tree(
            source,
            built,
            trusted_dependency_manifest=trusted_manifest,
        )


def test_backup_verifies_live_sha_and_uploads_content_addressed_key():
    package = lambda_zip_bytes()
    digest = base64.b64encode(hashlib.sha256(package).digest()).decode()
    cli = FakeAwsCli(uploaded_checksum=digest)
    artifact = backup_function_package(cli, {"Location": "https://signed.invalid", "CodeSha256": digest}, "private-artifacts", lambda _url, destination: destination.write_bytes(package))
    assert artifact.key == f"member-d/adoption/{hashlib.sha256(package).hexdigest()}.zip"
    assert artifact.version_id == "version-1"
    assert any("get-bucket-location" in call for call in cli.calls)
    assert cli.put_objects == [{"source": ANY, "bucket": "private-artifacts", "key": artifact.key, "checksum_sha256": digest, "server_side_encryption": "AES256"}]


def test_backup_rejects_hash_mismatch_without_upload():
    with pytest.raises(AdoptionError, match="SHA-256"):
        backup_function_package(FakeAwsCli(), {"Location": "https://signed.invalid", "CodeSha256": "wrong"}, "private-artifacts", lambda _url, path: path.write_bytes(lambda_zip_bytes()))


@pytest.mark.parametrize("bucket_state", ["public", "unencrypted", "wrong-account", "authorized-cross-account", "wrong-region", "null-region", "unversioned", "unreadable"])
def test_prepare_rejects_unsafe_artifact_bucket(bucket_state):
    with pytest.raises(AdoptionError, match="artifact bucket"):
        verify_artifact_bucket(FakeAwsCli(bucket_state=bucket_state), "artifacts", "ap-southeast-2")


class ArtifactBucketPolicyCli(FakeAwsCli):
    def __init__(self, policy_status=None, policy_error=None):
        super().__init__()
        self.policy_status = policy_status
        self.policy_error = policy_error
        self.policy_status_calls = 0

    def json(self, *args):
        if "get-bucket-policy-status" in " ".join(args):
            raise AssertionError(
                "bucket policy status must use optional_json"
            )
        return super().json(*args)

    def optional_json(self, expected_error_code, *args):
        assert expected_error_code == "NoSuchBucketPolicy"
        assert "get-bucket-policy-status" in " ".join(args)
        self.policy_status_calls += 1
        if self.policy_error is not None:
            raise self.policy_error
        return self.policy_status


def test_artifact_bucket_without_policy_is_accepted_when_other_controls_are_safe():
    cli = ArtifactBucketPolicyCli(policy_status=None)

    verify_artifact_bucket(cli, "private-artifacts", "ap-southeast-2")

    assert cli.policy_status_calls == 1


def test_artifact_bucket_with_public_policy_status_is_rejected():
    cli = ArtifactBucketPolicyCli(
        policy_status={"PolicyStatus": {"IsPublic": True}}
    )

    with pytest.raises(AdoptionError, match="artifact bucket"):
        verify_artifact_bucket(
            cli,
            "private-artifacts",
            "ap-southeast-2",
        )

    assert cli.policy_status_calls == 1


def test_artifact_bucket_policy_query_fails_closed_for_other_aws_errors():
    cli = ArtifactBucketPolicyCli(
        policy_error=AdoptionError("AWS CLI query failed")
    )

    with pytest.raises(AdoptionError, match="AWS CLI query failed"):
        verify_artifact_bucket(
            cli,
            "private-artifacts",
            "ap-southeast-2",
        )

    assert cli.policy_status_calls == 1


def test_backup_rejects_non_zip():
    digest = base64.b64encode(hashlib.sha256(b"not-a-zip").digest()).decode()
    with pytest.raises(AdoptionError, match="zip"):
        backup_function_package(FakeAwsCli(), {"Location": "https://signed.invalid", "CodeSha256": digest}, "private-artifacts", lambda _url, path: path.write_bytes(b"not-a-zip"))


def test_backup_rejects_uploaded_checksum_mismatch():
    package = lambda_zip_bytes()
    digest = base64.b64encode(hashlib.sha256(package).digest()).decode()
    with pytest.raises(AdoptionError, match="uploaded checksum"):
        backup_function_package(FakeAwsCli(uploaded_checksum="wrong"), {"Location": "https://signed.invalid", "CodeSha256": digest}, "private-artifacts", lambda _url, path: path.write_bytes(package))


def test_change_set_must_contain_exactly_nineteen_imports():
    expected = build_resources_to_import(valid_snapshot())
    changes = [{"ResourceChange": {"Action": "Import", "LogicalResourceId": item["LogicalResourceId"], "ResourceType": item["ResourceType"], "Replacement": "False"}} for item in expected]
    validate_import_change_set(changes, expected)


@pytest.mark.parametrize("action", ["Add", "Modify", "Remove", "Dynamic"])
def test_change_set_rejects_every_non_import_action(action):
    expected = build_resources_to_import(valid_snapshot())
    with pytest.raises(AdoptionError, match="19 Import"):
        validate_import_change_set([{"ResourceChange": {"Action": action, "LogicalResourceId": "QueryFunction", "ResourceType": "AWS::Lambda::Function", "Replacement": "False"}}], expected)


def test_processed_update_reuses_function_and_has_no_implicit_role():
    route_import_ids = {
        item["LogicalResourceId"]
        for item in build_resources_to_import(valid_snapshot())
        if item["ResourceType"] == "AWS::ApiGatewayV2::Route"
    }
    assert route_import_ids == set(adoption.ROUTES_BY_LOGICAL_ID)
    assert "QueryIntegration" not in route_import_ids
    processed = _update_processed(_maintained_role_target())
    validate_update_change_set([{"ResourceChange": {"Action": "Modify", "LogicalResourceId": "QueryFunction", "ResourceType": "AWS::Lambda::Function", "Replacement": "False"}}], processed)


def test_processed_update_rejects_implicit_role_or_adopted_replacement():
    with pytest.raises(AdoptionError, match="replacement|implicit role"):
        validate_update_change_set([{"ResourceChange": {"Action": "Modify", "LogicalResourceId": "QueryFunction", "ResourceType": "AWS::Lambda::Function", "Replacement": "True"}}], {"Resources": {"QueryFunctionRole": {"Type": "AWS::IAM::Role"}, "QueryFunction": {"Type": "AWS::Lambda::Function"}}})


def test_collection_orchestrates_live_read_only_state_without_secrets(tmp_path):
    cli = FakeAwsCli()
    snapshot = collect_snapshot(cli, fixture_config(tmp_path))
    commands = {call[:2] for call in cli.calls}
    assert {
        ("cloudformation", "describe-stacks"),
        ("cloudformation", "get-template"),
        ("cloudformation", "list-stack-resources"),
        ("cloudformation", "list-stacks"),
        ("cloudformation", "detect-stack-resource-drift"),
        ("dynamodb", "describe-table"),
        ("dynamodb", "describe-time-to-live"),
        ("dynamodb", "describe-continuous-backups"),
        ("dynamodb", "list-tags-of-resource"),
        ("dynamodb", "get-resource-policy"),
        ("dynamodb", "describe-kinesis-streaming-destination"),
        ("dynamodb", "describe-contributor-insights"),
        ("lambda", "get-policy"),
        ("lambda", "get-function-concurrency"),
        ("lambda", "get-runtime-management-config"),
        ("iam", "get-role"),
        ("iam", "list-attached-role-policies"),
        ("iam", "list-role-policies"),
        ("iam", "list-role-tags"),
        ("apigatewayv2", "get-integration"),
        ("apigatewayv2", "get-routes"),
        ("apigatewayv2", "get-stage"),
        ("apigatewayv2", "get-authorizers"),
        ("cloudformation", "describe-type"),
    } <= commands
    assert snapshot["stack"]["status"] == "UPDATE_ROLLBACK_COMPLETE"
    assert snapshot["role"]["path"] == "/"
    assert snapshot["reservations_table"] == valid_snapshot()[
        "reservations_table"
    ]
    assert "internal-secret" not in str(snapshot)
    assert "X-Amz-Signature" not in str(snapshot)


class ApprovedRoleDriftCli(FakeAwsCli):
    def json(self, *args):
        command = " ".join(args)
        approved = approved_role_drift_snapshot()["role"]
        if "detect-stack-resource-drift" in command:
            differences = []
            for item in approved["drift"]["differences"]:
                actual = deepcopy(item["actual"])
                if item["path"] == "/Policies/1":
                    actual["PolicyDocument"] = json.dumps(
                        actual["PolicyDocument"]
                    )
                differences.append({
                    "PropertyPath": item["path"],
                    "DifferenceType": item["type"],
                    "ExpectedValue": (
                        None
                        if item["expected"] is None
                        else json.dumps(item["expected"])
                    ),
                    "ActualValue": json.dumps(actual),
                })
            return {
                "StackResourceDrift": {
                    "StackResourceDriftStatus": "MODIFIED",
                    "PropertyDifferences": differences,
                }
            }
        if "list-role-policies" in command:
            return {"PolicyNames": sorted(approved["inline_policies"])}
        if "get-role-policy" in command:
            name = args[args.index("--policy-name") + 1]
            return deepcopy(approved["inline_policies"][name])
        if args[:2] == ("iam", "get-role"):
            return {
                "Role": {
                    "Path": "/",
                    "RoleName": "PacificBioArchive-QueryLambdaRole",
                    "AssumeRolePolicyDocument": deepcopy(
                        approved["trust_policy"]
                    ),
                    "PermissionsBoundary": None,
                }
            }
        if "list-attached-role-policies" in command:
            return {"AttachedPolicies": deepcopy(approved["managed_policies"])}
        if "get-template" in command:
            return {
                "TemplateBody": deepcopy(
                    approved_role_drift_snapshot()["stack"]["template"]
                )
            }
        return super().json(*args)


def test_collection_accepts_and_normalizes_only_exact_known_role_drift(tmp_path):
    snapshot = collect_snapshot(ApprovedRoleDriftCli(), fixture_config(tmp_path))

    assert snapshot["role"]["drift"] == approved_role_drift_snapshot()["role"][
        "drift"
    ]
    assert set(snapshot["role"]["inline_policies"]) == {
        "DynamoDBFilesAccess",
        "UploadReservationsAccess",
    }


def test_collection_decodes_url_encoded_iam_policy_documents(tmp_path):
    class UrlEncodedRolePolicyCli(ApprovedRoleDriftCli):
        def json(self, *args):
            result = super().json(*args)
            if "get-role-policy" in " ".join(args):
                result["PolicyDocument"] = quote(
                    json.dumps(result["PolicyDocument"]),
                    safe="",
                )
            return result

    snapshot = collect_snapshot(
        UrlEncodedRolePolicyCli(),
        fixture_config(tmp_path),
    )

    assert snapshot["role"]["drift"]["status"] == "MODIFIED"


def test_collection_decodes_url_encoded_assume_role_policy_document(tmp_path):
    class UrlEncodedTrustPolicyCli(ApprovedRoleDriftCli):
        def json(self, *args):
            result = super().json(*args)
            if args[:2] == ("iam", "get-role"):
                result["Role"]["AssumeRolePolicyDocument"] = quote(
                    json.dumps(
                        result["Role"]["AssumeRolePolicyDocument"]
                    ),
                    safe="",
                )
            return result

    snapshot = collect_snapshot(
        UrlEncodedTrustPolicyCli(),
        fixture_config(tmp_path),
    )

    assert snapshot["role"]["trust_policy"] == (
        approved_role_drift_snapshot()["role"]["trust_policy"]
    )


def test_collection_rejects_any_extra_permission_in_known_role_drift(tmp_path):
    class UnsafeRoleDriftCli(ApprovedRoleDriftCli):
        def json(self, *args):
            result = super().json(*args)
            if "detect-stack-resource-drift" in " ".join(args):
                document = json.loads(
                    result["StackResourceDrift"]["PropertyDifferences"][0][
                        "ActualValue"
                    ]
                )
                document["Statement"][0]["Action"].append(
                    "dynamodb:CreateTable"
                )
                result["StackResourceDrift"]["PropertyDifferences"][0][
                    "ActualValue"
                ] = json.dumps(document)
            return result

    with pytest.raises(AdoptionError, match="QueryLambdaRole|drift"):
        collect_snapshot(UnsafeRoleDriftCli(), fixture_config(tmp_path))


def test_collection_rejects_reservations_table_owned_by_another_stack(tmp_path):
    class OtherStackOwnsTableCli(FakeAwsCli):
        def json(self, *args):
            command = " ".join(args)
            if "list-stacks" in command:
                return {
                    "StackSummaries": [
                        {"StackName": "PacificBioArchive-Database"},
                        {"StackName": "OtherStack"},
                    ]
                }
            if "list-stack-resources" in command and "OtherStack" in args:
                return {
                    "StackResourceSummaries": [{
                        "LogicalResourceId": "ForeignTable",
                        "PhysicalResourceId": (
                            "PacificBioArchiveUploadReservations"
                        ),
                    }]
                }
            return super().json(*args)

    with pytest.raises(AdoptionError, match="already owned"):
        collect_snapshot(OtherStackOwnsTableCli(), fixture_config(tmp_path))


@pytest.mark.parametrize(
    ("field", "configured_value"),
    [
        (
            "OnDemandThroughput",
            {
                "MaxReadRequestUnits": 100,
                "MaxWriteRequestUnits": 50,
            },
        ),
        (
            "WarmThroughput",
            {
                "ReadUnitsPerSecond": 1000,
                "WriteUnitsPerSecond": 500,
                "Status": "ACTIVE",
            },
        ),
    ],
)
def test_collection_rejects_non_default_reservations_table_throughput(
    tmp_path,
    field,
    configured_value,
):
    class ConfiguredThroughputCli(FakeAwsCli):
        def json(self, *args):
            result = super().json(*args)
            if args[:2] == ("dynamodb", "describe-table"):
                result["Table"][field] = deepcopy(configured_value)
            return result

    with pytest.raises(AdoptionError, match="ReservationsTable|throughput"):
        collect_snapshot(
            ConfiguredThroughputCli(),
            fixture_config(tmp_path),
        )


def test_collection_rejects_reservations_table_resource_policy(tmp_path):
    class ResourcePolicyCli(FakeAwsCli):
        def json(self, *args):
            if args[:2] == ("dynamodb", "get-resource-policy"):
                return {
                    "Policy": json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [{
                                "Effect": "Allow",
                                "Principal": "*",
                                "Action": "dynamodb:*",
                                "Resource": "*",
                            }],
                        }
                    )
                }
            return super().json(*args)

    with pytest.raises(
        AdoptionError,
        match="ReservationsTable|resource policy|ResourcePolicy",
    ):
        collect_snapshot(ResourcePolicyCli(), fixture_config(tmp_path))


def test_collection_confirms_missing_reservations_policy_three_times(
    tmp_path,
):
    cli = FakeAwsCli()

    collect_snapshot(cli, fixture_config(tmp_path))

    policy_checks = [
        call
        for call in cli.calls
        if call[:2] == ("dynamodb", "get-resource-policy")
    ]
    assert len(policy_checks) == 3
    assert [
        call[1] for call in cli.calls if call[:1] == ("pause",)
    ] == [15.0, 15.0]


def test_collection_rejects_policy_that_appears_on_second_confirmation(
    tmp_path,
):
    class PolicyAppearsCli(FakeAwsCli):
        def __init__(self):
            super().__init__()
            self.policy_checks = 0

        def optional_json(self, expected_error_code, *args):
            assert expected_error_code == "PolicyNotFoundException"
            assert args[:2] == ("dynamodb", "get-resource-policy")
            self.calls.append(args)
            self.policy_checks += 1
            if self.policy_checks == 1:
                return None
            return {
                "Policy": json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": "dynamodb:*",
                        "Resource": "*",
                    }],
                })
            }

    cli = PolicyAppearsCli()
    with pytest.raises(
        AdoptionError,
        match="ReservationsTable|resource policy|ResourcePolicy",
    ):
        collect_snapshot(cli, fixture_config(tmp_path))

    assert cli.policy_checks == 2


@pytest.mark.parametrize("revision_id", [None, ""])
def test_lambda_policy_revision_output_requires_nonempty_revision(
    tmp_path,
    monkeypatch,
    revision_id,
):
    import prepare_import

    snapshot = valid_snapshot()
    (tmp_path / "sanitized-snapshot.json").write_text(
        json.dumps(_json_safe_snapshot(snapshot)),
        encoding="utf-8",
    )

    class PolicyCli:
        def json(self, *args):
            assert args[:2] == ("lambda", "get-policy")
            return {
                "Policy": json.dumps(
                    _route_scoped_lambda_policy(
                        snapshot,
                        removed_legacy_count=0,
                    )
                ),
                "RevisionId": revision_id,
            }

    monkeypatch.setattr(prepare_import, "AwsCli", lambda: PolicyCli())

    with pytest.raises(AdoptionError, match="RevisionId|revision"):
        prepare_import.main([
            "validate-lambda-policy",
            "--region", "ap-southeast-2",
            "--function", "PacificBioArchive-QueryLambda",
            "--workdir", str(tmp_path),
            "--removed-legacy-count", "0",
            "--emit-revision",
        ])


def test_lambda_policy_revision_is_emitted_from_same_validated_response(
    tmp_path,
    monkeypatch,
    capsys,
):
    import prepare_import

    snapshot = valid_snapshot()
    (tmp_path / "sanitized-snapshot.json").write_text(
        json.dumps(_json_safe_snapshot(snapshot)),
        encoding="utf-8",
    )
    calls = []

    class PolicyCli:
        def json(self, *args):
            calls.append(args)
            return {
                "Policy": json.dumps(
                    _route_scoped_lambda_policy(
                        snapshot,
                        removed_legacy_count=0,
                    )
                ),
                "RevisionId": "policy-revision-42",
            }

    monkeypatch.setattr(prepare_import, "AwsCli", lambda: PolicyCli())
    assert prepare_import.main([
        "validate-lambda-policy",
        "--region", "ap-southeast-2",
        "--function", "PacificBioArchive-QueryLambda",
        "--workdir", str(tmp_path),
        "--removed-legacy-count", "0",
        "--emit-revision",
    ]) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted == {
        "next_legacy_sid": "apigateway-query-lambda",
        "revision_id": "policy-revision-42",
    }
    assert len(calls) == 1


def test_each_legacy_cleanup_guard_rereads_policy_and_emits_fresh_revision(
    tmp_path,
    monkeypatch,
    capsys,
):
    import prepare_import

    snapshot = valid_snapshot()
    (tmp_path / "sanitized-snapshot.json").write_text(
        json.dumps(_json_safe_snapshot(snapshot)),
        encoding="utf-8",
    )

    class SequentialPolicyCli:
        def __init__(self):
            self.calls = 0

        def json(self, *args):
            assert args[:2] == ("lambda", "get-policy")
            removed = self.calls
            self.calls += 1
            return {
                "Policy": json.dumps(
                    _route_scoped_lambda_policy(
                        snapshot,
                        removed_legacy_count=removed,
                    )
                ),
                "RevisionId": f"policy-revision-{removed + 1}",
            }

    cli = SequentialPolicyCli()
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    expected_sids = [
        "apigateway-query-lambda",
        "AllowAuthTestInvoke",
        "AllowApiGatewayInvokeAllRoutes-20260829030023",
    ]

    for removed, expected_sid in enumerate(expected_sids):
        assert prepare_import.main([
            "validate-lambda-policy",
            "--region", "ap-southeast-2",
            "--function", "PacificBioArchive-QueryLambda",
            "--workdir", str(tmp_path),
            "--removed-legacy-count", str(removed),
            "--emit-revision",
        ]) == 0
        assert json.loads(capsys.readouterr().out) == {
            "next_legacy_sid": expected_sid,
            "revision_id": f"policy-revision-{removed + 1}",
        }

    assert prepare_import.main([
        "validate-lambda-policy",
        "--region", "ap-southeast-2",
        "--function", "PacificBioArchive-QueryLambda",
        "--workdir", str(tmp_path),
        "--removed-legacy-count", "3",
    ]) == 0
    assert "all route-scoped permissions" in capsys.readouterr().out
    assert cli.calls == 4


def test_collection_rejects_reservations_table_kinesis_destination(tmp_path):
    class KinesisDestinationCli(FakeAwsCli):
        def json(self, *args):
            if args[:2] == (
                "dynamodb",
                "describe-kinesis-streaming-destination",
            ):
                return {
                    "KinesisDataStreamDestinations": [{
                        "StreamArn": (
                            "arn:aws:kinesis:ap-southeast-2:111122223333:"
                            "stream/reservations-exfiltration"
                        ),
                        "DestinationStatus": "ACTIVE",
                    }]
                }
            return super().json(*args)

    with pytest.raises(
        AdoptionError,
        match="ReservationsTable|Kinesis|streaming destination",
    ):
        collect_snapshot(KinesisDestinationCli(), fixture_config(tmp_path))


def test_collection_rejects_enabled_reservations_contributor_insights(
    tmp_path,
):
    class ContributorInsightsCli(FakeAwsCli):
        def json(self, *args):
            if args[:2] == ("dynamodb", "describe-contributor-insights"):
                return {"ContributorInsightsStatus": "ENABLED"}
            return super().json(*args)

    with pytest.raises(
        AdoptionError,
        match="ReservationsTable|Contributor Insights|ContributorInsights",
    ):
        collect_snapshot(ContributorInsightsCli(), fixture_config(tmp_path))


def test_collection_accepts_yaml_processed_template_with_intrinsic_tags(tmp_path):
    yaml_template = """
AWSTemplateFormatVersion: '2010-09-09'
Parameters:
  InternalApiKey:
    Type: String
    NoEcho: true
    MinLength: 1
Resources:
  FilesTable:
    Type: AWS::DynamoDB::Table
  SubscriptionsTable:
    Type: AWS::DynamoDB::Table
  NotificationsTable:
    Type: AWS::DynamoDB::Table
  QueryLambdaRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: PacificBioArchive-QueryLambdaRole
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement: []
Outputs:
  FilesTableReference:
    Value: !Ref FilesTable
  QueryLambdaRoleArn:
    Value: !GetAtt QueryLambdaRole.Arn
"""

    snapshot = collect_snapshot(
        FakeAwsCli(template_body=yaml_template),
        fixture_config(tmp_path),
    )

    assert snapshot["stack"]["template"]["Outputs"]["FilesTableReference"] == {
        "Value": {"Ref": "FilesTable"}
    }
    assert snapshot["stack"]["template"]["Outputs"]["QueryLambdaRoleArn"] == {
        "Value": {"Fn::GetAtt": ["QueryLambdaRole", "Arn"]}
    }


def test_audit_baseline_compares_runtime_and_output_stays_sanitized(tmp_path, capsys):
    config = fixture_config(tmp_path)
    first = run_audit(FakeAwsCli(), config)
    second = run_audit(FakeAwsCli(), config, baseline=first)
    assert second.read_bytes() == first.read_bytes()
    captured = capsys.readouterr()
    assert "internal-secret" not in captured.out + captured.err
    assert "X-Amz-Signature" not in captured.out + captured.err


def test_prepare_accepts_direct_code_query_and_writes_sanitized_artifacts(
    tmp_path,
    capsys,
):
    package = lambda_zip_bytes()
    digest = base64.b64encode(hashlib.sha256(package).digest()).decode()
    config = fixture_config(tmp_path)
    cli = FakeAwsCli(uploaded_checksum=digest, code_sha=digest)
    assert cli.json(
        "lambda",
        "get-function",
        "--function-name",
        config.function,
        "--region",
        config.region,
        "--query",
        "Code",
    ) == {
        "Location": (
            "https://signed.invalid/?X-Amz-Signature=should-not-leak"
        )
    }
    artifact_paths = run_prepare(
        cli,
        config,
        "private-artifacts",
        lambda _url, destination: destination.write_bytes(package),
    )
    assert {path.name for path in artifact_paths} == {
        "sanitized-snapshot.json", "import-template.json",
        "resources-to-import.json", "import-parameters.json",
    }
    for path in artifact_paths:
        text = path.read_text(encoding="utf-8")
        assert "internal-secret" not in text
        assert "X-Amz-Signature" not in text
    parameters = json.loads((tmp_path / "import-parameters.json").read_text(encoding="utf-8"))
    assert parameters == [{"ParameterKey": "InternalApiKey", "UsePreviousValue": True}]
    template = json.loads((tmp_path / "import-template.json").read_text(encoding="utf-8"))
    function_properties = template["Resources"]["QueryFunction"]["Properties"]
    assert {"KmsKeyArn", "CodeSigningConfigArn", "ReservedConcurrentExecutions"}.isdisjoint(function_properties)
    assert all(value is not None for value in function_properties.values())
    captured = capsys.readouterr()
    assert "internal-secret" not in captured.out + captured.err
    assert "X-Amz-Signature" not in captured.out + captured.err


def test_prepare_with_missing_internal_key_writes_no_parameter_value(tmp_path):
    class MissingInternalKeyStackCli(FakeAwsCli):
        def json(self, *args):
            response = super().json(*args)
            if args[:2] == ("cloudformation", "describe-stacks"):
                response = deepcopy(response)
                response["Stacks"][0]["Parameters"] = []
            if (
                args[:2] == ("cloudformation", "get-template")
                and "--change-set-name" not in args
            ):
                response = deepcopy(response)
                response["TemplateBody"].pop("Parameters", None)
            return response

    package = lambda_zip_bytes()
    digest = base64.b64encode(hashlib.sha256(package).digest()).decode()
    cli = MissingInternalKeyStackCli(
        uploaded_checksum=digest,
        code_sha=digest,
    )

    artifact_paths = run_prepare(
        cli,
        fixture_config(tmp_path),
        "private-artifacts",
        lambda _url, destination: destination.write_bytes(package),
    )

    assert json.loads(
        (tmp_path / "import-parameters.json").read_text(encoding="utf-8")
    ) == []
    template = json.loads(
        (tmp_path / "import-template.json").read_text(encoding="utf-8")
    )
    assert "InternalApiKey" not in template.get("Parameters", {})
    assert "Environment" not in template["Resources"]["QueryFunction"]["Properties"]
    assert all(
        "internal-secret" not in path.read_text(encoding="utf-8")
        for path in artifact_paths
    )


def test_audit_exception_never_interpolates_secret_or_presigned_url(tmp_path):
    class SecretFailingCli(FakeAwsCli):
        def json(self, *args):
            if args[:2] == ("lambda", "get-policy"):
                raise RuntimeError("internal-secret https://signed.invalid/?X-Amz-Signature=leak")
            return super().json(*args)

    with pytest.raises(AdoptionError) as error:
        collect_snapshot(SecretFailingCli(), fixture_config(tmp_path))
    assert "internal-secret" not in str(error.value)
    assert "X-Amz-Signature" not in str(error.value)


def test_awscli_error_drops_secret_bearing_cause_and_stderr(monkeypatch, capsys):
    import subprocess
    from prepare_import import AwsCli

    def fail(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "aws", stderr="internal-secret X-Amz-Signature")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(AdoptionError) as error:
        AwsCli().json("sts", "get-caller-identity")
    assert error.value.__cause__ is None
    assert "internal-secret" not in str(error.value)
    captured = capsys.readouterr()
    assert "internal-secret" not in captured.out + captured.err
    assert "X-Amz-Signature" not in captured.out + captured.err


def test_sanitized_lambda_strips_output_only_runtime_and_snapstart_fields():
    from prepare_import import _sanitized_function
    configuration = valid_snapshot()["function"] | {
        "FunctionArn": "arn:aws:lambda:ap-southeast-2:111122223333:function:PacificBioArchive-QueryLambda",
        "RuntimeManagementConfig": {"UpdateRuntimeOn": "Auto", "FunctionArn": "output-only"},
        "SnapStart": {"ApplyOn": "None", "OptimizationStatus": "Off"},
        "Environment": {
            "Names": deepcopy(valid_snapshot()["function"]["environment_names"]),
            "Variables": deepcopy(valid_snapshot()["function"]["safe_environment"]),
        },
    }
    result = _sanitized_function(configuration)
    assert "FunctionArn" not in result
    assert result["RuntimeManagementConfig"] == {"UpdateRuntimeOn": "Auto"}
    assert result["SnapStart"] == {"ApplyOn": "None"}


def test_sanitized_lambda_rejects_unprojected_environment_without_secret_leak():
    from prepare_import import _sanitized_function

    secret = "secret-that-must-never-enter-an-error"
    configuration = valid_snapshot()["function"] | {
        "Environment": {
            "Names": deepcopy(valid_snapshot()["function"]["environment_names"]),
            "Variables": {
                **valid_snapshot()["function"]["safe_environment"],
                "INTERNAL_API_KEY": secret,
            }
        }
    }

    with pytest.raises(AdoptionError, match="environment.*malformed") as error:
        _sanitized_function(configuration)
    assert secret not in str(error.value)


def test_stack_parameter_name_query_returns_names_only():
    from prepare_import import collect_stack_parameter_names

    class ParameterCli:
        def __init__(self):
            self.calls = []

        def json(self, *args):
            self.calls.append(args)
            return {
                "StackName": "PacificBioArchive-Database",
                "ParameterNames": ["ExistingHttpApiId", "InternalApiKey"],
            }

    cli = ParameterCli()
    assert collect_stack_parameter_names(
        cli,
        "PacificBioArchive-Database",
        "ap-southeast-2",
    ) == {"ExistingHttpApiId", "InternalApiKey"}
    command = cli.calls[0]
    query = command[command.index("--query") + 1]
    assert query == (
        "Stacks[0].{StackName:StackName,"
        "ParameterNames:Parameters[].ParameterKey}"
    )
    assert "ParameterValue" not in " ".join(command)


def test_stack_parameter_name_query_accepts_omitted_parameters_for_known_stack():
    from prepare_import import collect_stack_parameter_names

    class ParameterCli:
        def json(self, *_args):
            return {
                "StackName": "PacificBioArchive-Database",
                "ParameterNames": None,
            }

    assert collect_stack_parameter_names(
        ParameterCli(),
        "PacificBioArchive-Database",
        "ap-southeast-2",
    ) == set()


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {
            "StackName": "wrong-stack",
            "ParameterNames": [],
        },
        {
            "StackName": "PacificBioArchive-Database",
            "ParameterNames": ["Duplicate", "Duplicate"],
        },
        {
            "StackName": "PacificBioArchive-Database",
            "ParameterNames": [""],
        },
        {
            "StackName": "PacificBioArchive-Database",
            "ParameterNames": {},
        },
    ],
)
def test_stack_parameter_name_query_fails_closed_on_malformed_shape(response):
    from prepare_import import collect_stack_parameter_names

    class ParameterCli:
        def json(self, *_args):
            return response

    with pytest.raises(AdoptionError, match="unavailable|duplicated"):
        collect_stack_parameter_names(
            ParameterCli(),
            "PacificBioArchive-Database",
            "ap-southeast-2",
        )


def test_collection_accepts_aws_null_optional_lambda_configuration_and_import_omits_it(
    tmp_path,
):
    class AwsNullOptionalFunctionCli(FakeAwsCli):
        def json(self, *args):
            result = super().json(*args)
            if "get-function-configuration" in " ".join(args):
                result.update({
                    "Layers": None,
                    "FileSystemConfigs": None,
                    "VpcConfig": None,
                    "DeadLetterConfig": None,
                    "Architectures": ["x86_64"],
                    "EphemeralStorage": {"Size": 512},
                })
            return result

    snapshot = collect_snapshot(
        AwsNullOptionalFunctionCli(),
        fixture_config(tmp_path),
    )
    template = build_import_template(
        snapshot,
        CodeArtifact("private-artifacts", "backups/code.zip", "version-1"),
    )
    properties = template["Resources"]["QueryFunction"]["Properties"]

    assert {
        "Layers",
        "FileSystemConfigs",
        "VpcConfig",
        "DeadLetterConfig",
    }.isdisjoint(properties)
    assert properties["Architectures"] == ["x86_64"]
    assert properties["EphemeralStorage"] == {"Size": 512}


def test_collection_strips_runtime_management_function_arn(tmp_path):
    class RuntimeOutputCli(FakeAwsCli):
        def json(self, *args):
            if args[:2] == ("lambda", "get-runtime-management-config"):
                return {
                    "UpdateRuntimeOn": "Auto",
                    "FunctionArn": "arn:aws:lambda:ap-southeast-2:111122223333:function:PacificBioArchive-QueryLambda",
                }
            return super().json(*args)

    snapshot = collect_snapshot(RuntimeOutputCli(), fixture_config(tmp_path))

    assert snapshot["function"]["RuntimeManagementConfig"] == {
        "UpdateRuntimeOn": "Auto"
    }


def test_backup_redacts_downloader_traceback_and_cause(capsys):
    location = "https://signed.invalid/?X-Amz-Signature=should-not-leak"

    def failing_downloader(url, _destination):
        raise RuntimeError(f"internal-secret from {url}")

    with pytest.raises(AdoptionError, match="package download failed") as error:
        backup_function_package(
            FakeAwsCli(),
            {"Location": location, "CodeSha256": "unused-after-download-failure"},
            "private-artifacts",
            failing_downloader,
        )

    rendered_traceback = "".join(traceback.format_exception(error.value))
    assert error.value.__cause__ is None
    assert "internal-secret" not in rendered_traceback
    assert "X-Amz-Signature" not in rendered_traceback
    captured = capsys.readouterr()
    assert "internal-secret" not in captured.out + captured.err
    assert "X-Amz-Signature" not in captured.out + captured.err


def test_collection_drops_api_gateway_output_and_preserves_route_api_key(tmp_path):
    class ApiGatewayOutputCli(FakeAwsCli):
        def json(self, *args):
            if args[:2] == ("apigatewayv2", "get-integration"):
                return {
                    **valid_snapshot()["integration"],
                    "ApiGatewayManaged": False,
                }
            if args[:2] == ("apigatewayv2", "get-routes"):
                routes = deepcopy(valid_snapshot()["api"]["routes"])
                routes[0]["ApiGatewayManaged"] = False
                routes[0]["ApiKeyRequired"] = True
                return {"Items": routes}
            return super().json(*args)

    snapshot = collect_snapshot(ApiGatewayOutputCli(), fixture_config(tmp_path))

    assert "ApiGatewayManaged" not in snapshot["integration"]
    assert "ApiGatewayManaged" not in snapshot["api"]["routes"][0]
    assert snapshot["api"]["routes"][0]["ApiKeyRequired"] is True
    template = build_import_template(
        snapshot,
        CodeArtifact("private-artifacts", "backups/code.zip", "version-1"),
    )
    assert template["Resources"]["AuthTestRoute"]["Properties"][
        "ApiKeyRequired"
    ] is True


def test_collection_preserves_every_supported_integration_property(tmp_path):
    supported = {
        "IntegrationId": "fbjojun",
        "IntegrationType": "AWS_PROXY",
        "IntegrationMethod": "POST",
        "PayloadFormatVersion": "2.0",
        "IntegrationUri": (
            "arn:aws:apigateway:ap-southeast-2:lambda:path/2015-03-31/"
            "functions/arn:aws:lambda:ap-southeast-2:111122223333:"
            "function:PacificBioArchive-QueryLambda/invocations"
        ),
        "ConnectionId": "vpc-link-1",
        "ConnectionType": "INTERNET",
        "ContentHandlingStrategy": "CONVERT_TO_TEXT",
        "CredentialsArn": "arn:aws:iam::111122223333:role/integration-role",
        "Description": "complete supported integration fixture",
        "IntegrationSubtype": "EventBridge-PutEvents",
        "PassthroughBehavior": "WHEN_NO_MATCH",
        "RequestParameters": {"Detail": "$request.body.detail"},
        "RequestTemplates": {"application/json": "{\"ok\":true}"},
        "ResponseParameters": {"200": {"append:header.test": "value"}},
        "TemplateSelectionExpression": "$request.body.action",
        "TimeoutInMillis": 30000,
        "TlsConfig": {"ServerNameToVerify": "example.internal"},
    }

    class CompleteIntegrationCli(FakeAwsCli):
        def json(self, *args):
            if args[:2] == ("apigatewayv2", "get-integration"):
                return {
                    **supported,
                    "ApiGatewayManaged": False,
                    "IntegrationResponseSelectionExpression": "$default",
                }
            return super().json(*args)

    snapshot = collect_snapshot(CompleteIntegrationCli(), fixture_config(tmp_path))

    assert snapshot["integration"] == supported
    template = build_import_template(
        snapshot,
        CodeArtifact("private-artifacts", "backups/code.zip", "version-1"),
    )
    expected_properties = {
        "ApiId": "2dd2aqb32j",
        **{key: value for key, value in supported.items() if key != "IntegrationId"},
    }
    assert template["Resources"]["QueryIntegration"]["Properties"] == (
        expected_properties
    )


def test_collection_rejects_api_gateway_managed_integration(tmp_path):
    class ManagedIntegrationCli(FakeAwsCli):
        def json(self, *args):
            if args[:2] == ("apigatewayv2", "get-integration"):
                return {
                    **valid_snapshot()["integration"],
                    "ApiGatewayManaged": True,
                }
            return super().json(*args)

    with pytest.raises(AdoptionError, match="managed by API Gateway"):
        collect_snapshot(ManagedIntegrationCli(), fixture_config(tmp_path))


def test_update_artifacts_accept_exact_maintained_packaged_candidate():
    adoption.validate_update_artifacts(
        _processed_update_template(),
        _built_update_template(),
        _packaged_update_template(),
        _maintained_source_template(),
        _explicit_update_parameters(),
        _EXPECTED_UPDATE_PARAMETER_VALUES,
        _update_artifact(),
    )


def test_update_artifacts_reject_built_template_not_bound_to_maintained_source():
    built = _built_update_template()
    built.setdefault("Outputs", {})["LeakedInternalApiKey"] = {
        "Value": {"Ref": "InternalApiKey"}
    }

    with pytest.raises(
        AdoptionError,
        match="built|maintained|source|template|Outputs",
    ):
        adoption.validate_update_artifacts(
            _processed_update_template(),
            built,
            _packaged_update_template(),
            _maintained_source_template(),
            _explicit_update_parameters(),
            _EXPECTED_UPDATE_PARAMETER_VALUES,
            _update_artifact(),
        )


def test_update_artifacts_reject_processed_output_that_leaks_internal_key():
    processed = _processed_update_template()
    processed["Outputs"]["LeakedInternalApiKey"] = {
        "Value": {"Ref": "InternalApiKey"}
    }

    with pytest.raises(AdoptionError, match="Outputs|packaged|template"):
        adoption.validate_update_artifacts(
            processed,
            _built_update_template(),
            _packaged_update_template(),
            _maintained_source_template(),
            _explicit_update_parameters(),
            _EXPECTED_UPDATE_PARAMETER_VALUES,
            _update_artifact(),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda template: template["Parameters"]["InternalApiKey"].update(
            {"NoEcho": False}
        ),
        lambda template: template["Conditions"].update(
            {
                "HasNotificationEmailEndpoint": {
                    "Fn::Equals": [
                        {"Ref": "NotificationEmailEndpoint"},
                        "always-enabled",
                    ]
                }
            }
        ),
        lambda template: template["Outputs"]["QueryFunctionArn"].update(
            {"Value": {"Ref": "InternalApiKey"}}
        ),
    ],
    ids=("parameters", "conditions", "outputs"),
)
def test_update_artifacts_reject_processed_top_level_mismatch(mutation):
    processed = _processed_update_template()
    mutation(processed)

    with pytest.raises(
        AdoptionError,
        match="Parameters|Conditions|Outputs|packaged|template",
    ):
        adoption.validate_update_artifacts(
            processed,
            _built_update_template(),
            _packaged_update_template(),
            _maintained_source_template(),
            _explicit_update_parameters(),
            _EXPECTED_UPDATE_PARAMETER_VALUES,
            _update_artifact(),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda template: template["Parameters"]["AllowedOrigin"].update(
            {"Default": "https://attacker.invalid"}
        ),
        lambda template: template["Conditions"].update(
            {"HasNotificationEmailEndpoint": {"Fn::Equals": ["1", "1"]}}
        ),
        lambda template: template["Outputs"].update(
            {"LeakedInternalApiKey": {"Value": {"Ref": "InternalApiKey"}}}
        ),
    ],
    ids=("parameters", "conditions", "outputs"),
)
def test_update_artifacts_reject_packaged_top_level_mismatch_from_repo(
    mutation,
):
    packaged = _packaged_update_template()
    processed = _processed_update_template()
    mutation(packaged)
    mutation(processed)

    with pytest.raises(AdoptionError, match="maintained|repository|template"):
        adoption.validate_update_artifacts(
            processed,
            _built_update_template(),
            packaged,
            _maintained_source_template(),
            _explicit_update_parameters(),
            _EXPECTED_UPDATE_PARAMETER_VALUES,
            _update_artifact(),
        )


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("S3Bucket", "attacker-bucket"),
        ("S3Key", "attacker/query-function.zip"),
        ("S3ObjectVersion", "attacker-version"),
    ],
)
def test_update_artifacts_reject_processed_code_mismatch_from_code_uri(
    field,
    wrong_value,
):
    processed = _processed_update_template()
    processed["Resources"]["QueryFunction"]["Properties"]["Code"][
        field
    ] = wrong_value

    with pytest.raises(AdoptionError, match="Code|CodeUri|packaged"):
        adoption.validate_update_artifacts(
            processed,
            _built_update_template(),
            _packaged_update_template(),
            _maintained_source_template(),
            _explicit_update_parameters(),
            _EXPECTED_UPDATE_PARAMETER_VALUES,
            _update_artifact(),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda parameters: parameters[0].update(
            {"ParameterValue": "wrong-api-id"}
        ),
        lambda parameters: parameters[-1].clear()
        or parameters[-1].update(
            {
                "ParameterKey": "InternalApiKey",
                "ParameterValue": "must-not-rotate-here",
            }
        ),
        lambda parameters: parameters.append(
            {
                "ParameterKey": "AllowedOrigin",
                "ParameterValue": "https://attacker.invalid",
            }
        ),
        lambda parameters: parameters.pop(2),
    ],
    ids=(
        "wrong-explicit-value",
        "secret-not-use-previous",
        "implicit-default-override",
        "missing-explicit-value",
    ),
)
def test_update_artifacts_reject_unapproved_change_set_parameters(mutation):
    parameters = _explicit_update_parameters()
    mutation(parameters)

    with pytest.raises(
        AdoptionError,
        match="parameter|Parameter|InternalApiKey|NoEcho|previous",
    ):
        adoption.validate_update_artifacts(
            _processed_update_template(),
            _built_update_template(),
            _packaged_update_template(),
            _maintained_source_template(),
            parameters,
            _EXPECTED_UPDATE_PARAMETER_VALUES,
            _update_artifact(),
        )


@pytest.mark.parametrize("mask", ["****", "******", "not-masked"])
def test_first_update_rejects_noncanonical_noecho_mask(mask):
    with pytest.raises(
        AdoptionError,
        match="masked|NoEcho|InternalApiKey|resolved",
    ):
        adoption.validate_update_artifacts(
            _processed_update_template(),
            _built_update_template(),
            _packaged_update_template(),
            _maintained_source_template(),
            _first_update_parameters(mask),
            _EXPECTED_UPDATE_PARAMETER_VALUES,
            _update_artifact(),
            internal_key_already_exists=False,
        )


@pytest.mark.parametrize(
    "internal_parameter",
    [
        {"ParameterKey": "InternalApiKey", "UsePreviousValue": True},
        {
            "ParameterKey": "InternalApiKey",
            "ParameterValue": "*****",
            "UsePreviousValue": False,
        },
        {
            "ParameterKey": "InternalApiKey",
            "ParameterValue": "*****",
            "ResolvedValue": "must-not-be-present",
        },
    ],
    ids=("use-previous", "mask-plus-use-previous", "resolved-value"),
)
def test_first_update_rejects_noncanonical_internal_parameter_shape(
    internal_parameter,
):
    parameters = _explicit_update_parameters()
    parameters[-1] = internal_parameter

    with pytest.raises(
        AdoptionError,
        match="masked|NoEcho|InternalApiKey|resolved",
    ):
        adoption.validate_update_artifacts(
            _processed_update_template(),
            _built_update_template(),
            _packaged_update_template(),
            _maintained_source_template(),
            parameters,
            _EXPECTED_UPDATE_PARAMETER_VALUES,
            _update_artifact(),
            internal_key_already_exists=False,
        )


@pytest.mark.parametrize(
    "leak",
    [
        {"Ref": "InternalApiKey"},
        {"Fn::Sub": "secret=${InternalApiKey}"},
        {"Fn::Sub": ["secret=${InternalApiKey}", {}]},
    ],
    ids=("ref", "sub-string", "sub-list"),
)
def test_update_rejects_internal_key_reference_outside_query_environment(leak):
    processed = _processed_update_template()
    built = _built_update_template()
    packaged = _packaged_update_template()
    maintained = _maintained_source_template()
    for template in (processed, built, packaged, maintained):
        template.setdefault("Outputs", {})["LeakedInternalApiKey"] = {
            "Value": deepcopy(leak)
        }

    with pytest.raises(AdoptionError, match="InternalApiKey|NoEcho|binding"):
        adoption.validate_update_artifacts(
            processed,
            built,
            packaged,
            maintained,
            _first_update_parameters(),
            _EXPECTED_UPDATE_PARAMETER_VALUES,
            _update_artifact(),
            internal_key_already_exists=False,
        )


def test_import_artifacts_accept_exact_local_template_and_use_previous():
    snapshot = valid_snapshot()
    local = build_import_template(
        snapshot,
        CodeArtifact(
            "private-artifacts",
            _FIXTURE_ARTIFACT_KEY,
            "import-version-1",
        ),
    )
    parameters = [
        {"ParameterKey": "InternalApiKey", "UsePreviousValue": True}
    ]

    adoption.validate_import_artifacts(
        deepcopy(local),
        local,
        deepcopy(parameters),
        parameters,
        snapshot,
        "private-artifacts",
    )


def test_import_artifacts_accept_no_parameters_when_internal_key_is_missing():
    snapshot = valid_snapshot()
    snapshot["stack"]["parameters"] = []
    snapshot["stack"]["template"].pop("Parameters")
    local = build_import_template(
        snapshot,
        CodeArtifact(
            "private-artifacts",
            _FIXTURE_ARTIFACT_KEY,
            "import-version-1",
        ),
    )

    adoption.validate_import_artifacts(
        deepcopy(local),
        local,
        [],
        [],
        snapshot,
        "private-artifacts",
    )


@pytest.mark.parametrize(
    ("change_set_parameters", "local_parameters"),
    [
        (
            [{
                "ParameterKey": "InternalApiKey",
                "UsePreviousValue": True,
            }],
            [],
        ),
        (
            [{
                "ParameterKey": "InternalApiKey",
                "ParameterValue": "*****",
            }],
            [{
                "ParameterKey": "InternalApiKey",
                "ParameterValue": "must-not-be-written",
            }],
        ),
    ],
    ids=(
        "missing-value-cannot-use-previous",
        "secret-cannot-enter-local-parameter-file",
    ),
)
def test_missing_internal_key_rejects_unsafe_parameter_sources(
    change_set_parameters,
    local_parameters,
):
    snapshot = valid_snapshot()
    snapshot["stack"]["parameters"] = []
    snapshot["stack"]["template"].pop("Parameters")
    local = build_import_template(
        snapshot,
        CodeArtifact(
            "private-artifacts",
            _FIXTURE_ARTIFACT_KEY,
            "import-version-1",
        ),
    )

    with pytest.raises(
        AdoptionError,
        match="IMPORT|parameter|NoEcho|console|previous",
    ):
        adoption.validate_import_artifacts(
            deepcopy(local),
            local,
            change_set_parameters,
            local_parameters,
            snapshot,
            "private-artifacts",
        )


def test_import_change_set_rejects_query_role_modify_with_missing_internal_key():
    snapshot = valid_snapshot()
    snapshot["stack"]["parameters"] = []
    snapshot["stack"]["template"].pop("Parameters")
    changes = _import_changes(snapshot)
    changes.append({
        "ResourceChange": {
            "Action": "Modify",
            "LogicalResourceId": "QueryLambdaRole",
            "ResourceType": "AWS::IAM::Role",
            "Replacement": "False",
        }
    })

    with pytest.raises(AdoptionError, match="19 Import"):
        adoption.validate_import_change_set(
            changes,
            build_resources_to_import(snapshot),
        )


def test_missing_internal_key_import_still_contains_exactly_nineteen_imports():
    snapshot = valid_snapshot()
    snapshot["stack"]["parameters"] = []
    snapshot["stack"]["template"].pop("Parameters")
    expected = build_resources_to_import(snapshot)
    changes = _import_changes(snapshot)

    adoption.validate_import_change_set(changes, expected)

    assert len(changes) == 19
    assert all(
        change["ResourceChange"]["Action"] == "Import"
        for change in changes
    )
    assert all(
        change["ResourceChange"]["LogicalResourceId"]
        != "QueryLambdaRole"
        for change in changes
    )


def test_import_rejects_console_supplied_internal_key_without_logging_it(
    tmp_path,
    monkeypatch,
    capsys,
):
    import prepare_import

    workdir = tmp_path / "missing-key-import"
    files = _write_change_set_validation_files(workdir)
    snapshot = files["snapshot"]
    snapshot["stack"]["parameters"] = []
    snapshot["stack"]["template"].pop("Parameters")
    files["snapshot"] = snapshot
    local = build_import_template(
        snapshot,
        CodeArtifact(
            "private-artifacts",
            _FIXTURE_ARTIFACT_KEY,
            "import-version-1",
        ),
    )
    (workdir / "sanitized-snapshot.json").write_text(
        json.dumps(_json_safe_snapshot(snapshot)),
        encoding="utf-8",
    )
    (workdir / "import-template.json").write_text(
        json.dumps(local),
        encoding="utf-8",
    )
    (workdir / "import-parameters.json").write_text(
        "[]",
        encoding="utf-8",
    )
    console_secret = "console-secret-must-never-leak"
    cli = CandidateChangeSetCli(
        change_set_type="IMPORT",
        changes=_import_changes(snapshot),
        parameters=[{
            "ParameterKey": "InternalApiKey",
            "ParameterValue": console_secret,
        }],
        processed=local,
    )
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    _use_stored_snapshot_as_fresh(monkeypatch, prepare_import, files)
    monkeypatch.setenv("INTERNAL_API_KEY", "environment-secret-must-be-ignored")

    with pytest.raises(AdoptionError, match="IMPORT|parameter|previous"):
        prepare_import.main(_validate_change_set_args(workdir, "IMPORT"))

    captured = capsys.readouterr()
    assert console_secret not in captured.out + captured.err
    assert "environment-secret-must-be-ignored" not in captured.out + captured.err
    for path in workdir.iterdir():
        if path.is_file():
            contents = path.read_bytes()
            assert console_secret.encode() not in contents
            assert b"environment-secret-must-be-ignored" not in contents


@pytest.mark.parametrize("section", ["Parameters", "Outputs"])
def test_import_artifacts_reject_processed_template_injection(section):
    snapshot = valid_snapshot()
    local = build_import_template(
        snapshot,
        CodeArtifact(
            "private-artifacts",
            _FIXTURE_ARTIFACT_KEY,
            "import-version-1",
        ),
    )
    processed = deepcopy(local)
    if section == "Parameters":
        processed["Parameters"]["InjectedParameter"] = {
            "Type": "String",
            "Default": "attacker-controlled",
        }
    else:
        processed.setdefault("Outputs", {})["LeakedInternalApiKey"] = {
            "Value": {"Ref": "InternalApiKey"}
        }
    parameters = [
        {"ParameterKey": "InternalApiKey", "UsePreviousValue": True}
    ]

    with pytest.raises(AdoptionError, match="processed|template|injection"):
        adoption.validate_import_artifacts(
            processed,
            local,
            parameters,
            parameters,
            snapshot,
            "private-artifacts",
        )


def test_real_import_failure_regression_rejects_parameter_registration_even_with_equal_outputs():
    snapshot = valid_snapshot()
    snapshot["stack"]["parameters"] = []
    snapshot["stack"]["template"].pop("Parameters")
    snapshot["stack"]["template"]["Outputs"] = {
        "NotificationsTableName": {"Value": {"Ref": "NotificationsTable"}},
        "QueryLambdaRoleArn": {
            "Value": {"Fn::GetAtt": ["QueryLambdaRole", "Arn"]}
        },
        "SubscriptionsTableName": {"Value": {"Ref": "SubscriptionsTable"}},
        "TableName": {"Value": {"Ref": "FilesTable"}},
    }
    local = build_import_template(
        snapshot,
        CodeArtifact(
            "private-artifacts",
            _FIXTURE_ARTIFACT_KEY,
            "import-version-1",
        ),
    )
    one_step_candidate = deepcopy(local)
    one_step_candidate["Parameters"] = {
        "InternalApiKey": {
            "Type": "String",
            "NoEcho": True,
            "MinLength": 1,
        }
    }
    assert one_step_candidate["Outputs"] == local["Outputs"]

    with pytest.raises(AdoptionError, match="processed|template|injection"):
        adoption.validate_import_artifacts(
            one_step_candidate,
            local,
            [],
            [],
            snapshot,
            "private-artifacts",
        )


@pytest.mark.parametrize("tamper_local_file", [False, True])
def test_import_artifacts_reject_any_non_use_previous_parameter(
    tamper_local_file,
):
    snapshot = valid_snapshot()
    local = build_import_template(
        snapshot,
        CodeArtifact(
            "private-artifacts",
            _FIXTURE_ARTIFACT_KEY,
            "import-version-1",
        ),
    )
    expected_parameters = [
        {"ParameterKey": "InternalApiKey", "UsePreviousValue": True}
    ]
    actual_parameters = [{
        "ParameterKey": "InternalApiKey",
        "ParameterValue": "must-not-rotate-here",
    }]
    if tamper_local_file:
        expected_parameters = deepcopy(actual_parameters)

    with pytest.raises(AdoptionError, match="UsePrevious|parameter"):
        adoption.validate_import_artifacts(
            deepcopy(local),
            local,
            actual_parameters,
            expected_parameters,
            snapshot,
            "private-artifacts",
        )


def test_update_change_set_main_binds_local_packaged_template_and_cli_values(
    tmp_path,
    monkeypatch,
):
    import prepare_import

    workdir = tmp_path / "update-work"
    files = _write_change_set_validation_files(workdir)
    cli = CandidateChangeSetCli(
        change_set_type="UPDATE",
        changes=_update_changes(),
        parameters=_explicit_update_parameters(),
        processed=files["processed_update"],
        artifact_checksum=files["update_checksum"],
        artifact_version=files["update_artifact"].version_id,
    )
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    _use_packaged_update_artifact(monkeypatch, prepare_import, files)
    monkeypatch.setattr(
        prepare_import,
        "collect_snapshot",
        lambda _cli, _config: deepcopy(files["snapshot"]),
    )

    assert prepare_import.main(
        _validate_change_set_args(workdir, "UPDATE")
    ) == 0


def test_first_update_accepts_console_masked_noecho_when_parameter_was_missing(
    tmp_path,
    monkeypatch,
):
    import prepare_import

    workdir = tmp_path / "first-update-work"
    files = _write_change_set_validation_files(workdir)
    files["snapshot"]["stack"]["parameters"] = []
    files["snapshot"]["stack"]["template"].pop("Parameters")
    (workdir / "sanitized-snapshot.json").write_text(
        json.dumps(_json_safe_snapshot(files["snapshot"])),
        encoding="utf-8",
    )
    parameters = _explicit_update_parameters()
    parameters[-1] = {
        "ParameterKey": "InternalApiKey",
        "ParameterValue": "*****",
    }
    cli = CandidateChangeSetCli(
        change_set_type="UPDATE",
        changes=_update_changes(),
        parameters=parameters,
        processed=files["processed_update"],
        artifact_checksum=files["update_checksum"],
        artifact_version=files["update_artifact"].version_id,
        stack_parameter_names=[],
    )
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    _use_packaged_update_artifact(monkeypatch, prepare_import, files)
    monkeypatch.setattr(
        prepare_import,
        "collect_snapshot",
        lambda _cli, _config: deepcopy(files["snapshot"]),
    )

    assert prepare_import.main(
        _validate_change_set_args(workdir, "UPDATE")
    ) == 0
    parameter_name_calls = [
        call
        for call in cli.calls
        if call[:2] == ("cloudformation", "describe-stacks")
        and "--query" in call
    ]
    assert len(parameter_name_calls) == 1
    assert "ParameterValue" not in " ".join(parameter_name_calls[0])


def test_first_update_rejects_parameter_state_change_during_validation(
    tmp_path,
    monkeypatch,
):
    import prepare_import

    workdir = tmp_path / "first-update-race"
    files = _write_change_set_validation_files(workdir)
    files["snapshot"]["stack"]["parameters"] = []
    files["snapshot"]["stack"]["template"].pop("Parameters")
    (workdir / "sanitized-snapshot.json").write_text(
        json.dumps(_json_safe_snapshot(files["snapshot"])),
        encoding="utf-8",
    )
    cli = CandidateChangeSetCli(
        change_set_type="UPDATE",
        changes=_update_changes(),
        parameters=_first_update_parameters(),
        processed=files["processed_update"],
        artifact_checksum=files["update_checksum"],
        artifact_version=files["update_artifact"].version_id,
        stack_parameter_names=["InternalApiKey"],
    )
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    _use_packaged_update_artifact(monkeypatch, prepare_import, files)
    monkeypatch.setattr(
        prepare_import,
        "collect_snapshot",
        lambda _cli, _config: deepcopy(files["snapshot"]),
    )

    with pytest.raises(AdoptionError, match="parameter names changed"):
        prepare_import.main(_validate_change_set_args(workdir, "UPDATE"))


def test_first_update_plaintext_parameter_is_not_logged_or_persisted(
    tmp_path,
    monkeypatch,
    capsys,
):
    import prepare_import

    workdir = tmp_path / "first-update-plaintext"
    files = _write_change_set_validation_files(workdir)
    files["snapshot"]["stack"]["parameters"] = []
    files["snapshot"]["stack"]["template"].pop("Parameters")
    (workdir / "sanitized-snapshot.json").write_text(
        json.dumps(_json_safe_snapshot(files["snapshot"])),
        encoding="utf-8",
    )
    secret = "plaintext-console-secret-must-not-leak"
    cli = CandidateChangeSetCli(
        change_set_type="UPDATE",
        changes=_update_changes(),
        parameters=_first_update_parameters(secret),
        processed=files["processed_update"],
        artifact_checksum=files["update_checksum"],
        artifact_version=files["update_artifact"].version_id,
        stack_parameter_names=[],
    )
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    _use_packaged_update_artifact(monkeypatch, prepare_import, files)
    monkeypatch.setattr(
        prepare_import,
        "collect_snapshot",
        lambda _cli, _config: deepcopy(files["snapshot"]),
    )

    with pytest.raises(AdoptionError, match="masked|NoEcho|InternalApiKey"):
        prepare_import.main(_validate_change_set_args(workdir, "UPDATE"))

    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert all(
        secret.encode() not in path.read_bytes()
        for path in workdir.iterdir()
        if path.is_file()
    )


def test_followup_update_does_not_reconcile_role_from_old_drift_snapshot(
    tmp_path,
    monkeypatch,
):
    import prepare_import

    workdir = tmp_path / "followup-update-work"
    files = _write_change_set_validation_files(workdir)
    function_change = [{
        "ResourceChange": {
            "Action": "Modify",
            "LogicalResourceId": "QueryFunction",
            "ResourceType": "AWS::Lambda::Function",
            "Replacement": "False",
        }
    }]
    cli = CandidateChangeSetCli(
        change_set_type="UPDATE",
        changes=function_change,
        parameters=_explicit_update_parameters(),
        processed=files["processed_update"],
        artifact_checksum=files["update_checksum"],
        artifact_version=files["update_artifact"].version_id,
    )
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    _use_packaged_update_artifact(monkeypatch, prepare_import, files)
    monkeypatch.setattr(
        prepare_import,
        "collect_snapshot",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("hardening-only UPDATE must not recollect old role drift")
        ),
    )
    hardening_evidence = []
    monkeypatch.setattr(
        prepare_import,
        "validate_hardening_runtime_evidence",
        lambda *args, **kwargs: hardening_evidence.append((args, kwargs)),
    )

    assert prepare_import.main(
        _validate_change_set_args(
            workdir,
            "UPDATE",
            expect_role_reconciliation="false",
        )
    ) == 0
    assert len(hardening_evidence) == 1
    assert hardening_evidence[0][1] == {"expected_callback": "false"}


def test_followup_update_rejects_unexpected_role_change(tmp_path, monkeypatch):
    import prepare_import

    workdir = tmp_path / "followup-update-work"
    files = _write_change_set_validation_files(workdir)
    cli = CandidateChangeSetCli(
        change_set_type="UPDATE",
        changes=_update_changes(),
        parameters=_explicit_update_parameters(),
        processed=files["processed_update"],
        artifact_checksum=files["update_checksum"],
        artifact_version=files["update_artifact"].version_id,
    )
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    _use_packaged_update_artifact(monkeypatch, prepare_import, files)

    with pytest.raises(
        AdoptionError,
        match="hardening|QueryFunction|QueryLambdaRole|role",
    ):
        prepare_import.main(
            _validate_change_set_args(
                workdir,
                "UPDATE",
                expect_role_reconciliation="false",
            )
        )


def test_update_change_set_main_rejects_tampered_local_packaged_template(
    tmp_path,
    monkeypatch,
):
    import prepare_import

    workdir = tmp_path / "update-work"
    files = _write_change_set_validation_files(workdir)
    tampered = deepcopy(files["packaged"])
    tampered["Outputs"]["LeakedInternalApiKey"] = {
        "Value": {"Ref": "InternalApiKey"}
    }
    (workdir / "packaged-template.yaml").write_text(
        json.dumps(tampered),
        encoding="utf-8",
    )
    cli = CandidateChangeSetCli(
        change_set_type="UPDATE",
        changes=_update_changes(),
        parameters=_explicit_update_parameters(),
        processed=files["processed_update"],
        artifact_checksum=files["update_checksum"],
        artifact_version=files["update_artifact"].version_id,
    )
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    _use_packaged_update_artifact(monkeypatch, prepare_import, files)
    monkeypatch.setattr(
        prepare_import,
        "collect_snapshot",
        lambda _cli, _config: deepcopy(files["snapshot"]),
    )

    with pytest.raises(AdoptionError, match="maintained|packaged|template"):
        prepare_import.main(_validate_change_set_args(workdir, "UPDATE"))


def test_import_change_set_main_binds_processed_template_and_local_artifacts(
    tmp_path,
    monkeypatch,
):
    import prepare_import

    workdir = tmp_path / "import-work"
    files = _write_change_set_validation_files(workdir)
    cli = CandidateChangeSetCli(
        change_set_type="IMPORT",
        changes=_import_changes(files["snapshot"]),
        parameters=files["import_parameters"],
        processed=files["import_template"],
    )
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    _use_stored_snapshot_as_fresh(monkeypatch, prepare_import, files)

    assert prepare_import.main(
        _validate_change_set_args(workdir, "IMPORT")
    ) == 0
    assert any(call[:2] == ("cloudformation", "get-template") for call in cli.calls)
    head_object = next(
        call for call in cli.calls if call[:2] == ("s3api", "head-object")
    )
    assert head_object[head_object.index("--bucket") + 1] == "private-artifacts"
    assert head_object[head_object.index("--key") + 1] == _FIXTURE_ARTIFACT_KEY
    assert head_object[head_object.index("--version-id") + 1] == (
        "import-version-1"
    )
    assert "--checksum-mode" in head_object


@pytest.mark.parametrize(
    ("candidate_kwargs", "error_pattern"),
    [
        (
            {"artifact_checksum": base64.b64encode(b"x" * 32).decode()},
            "checksum|SHA-256|digest|artifact",
        ),
        (
            {"artifact_version": "different-version"},
            "version|artifact",
        ),
    ],
    ids=("checksum-mismatch", "version-mismatch"),
)
def test_import_change_set_rejects_unverified_artifact_object(
    tmp_path,
    monkeypatch,
    candidate_kwargs,
    error_pattern,
):
    import prepare_import

    workdir = tmp_path / "import-work"
    files = _write_change_set_validation_files(workdir)
    cli = CandidateChangeSetCli(
        change_set_type="IMPORT",
        changes=_import_changes(files["snapshot"]),
        parameters=files["import_parameters"],
        processed=files["import_template"],
        **candidate_kwargs,
    )
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    _use_stored_snapshot_as_fresh(monkeypatch, prepare_import, files)

    with pytest.raises(AdoptionError, match=error_pattern):
        prepare_import.main(_validate_change_set_args(workdir, "IMPORT"))


def test_import_change_set_main_rejects_processed_output_injection(
    tmp_path,
    monkeypatch,
):
    import prepare_import

    workdir = tmp_path / "import-work"
    files = _write_change_set_validation_files(workdir)
    processed = deepcopy(files["import_template"])
    processed.setdefault("Outputs", {})["LeakedInternalApiKey"] = {
        "Value": {"Ref": "InternalApiKey"}
    }
    cli = CandidateChangeSetCli(
        change_set_type="IMPORT",
        changes=_import_changes(files["snapshot"]),
        parameters=files["import_parameters"],
        processed=processed,
    )
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    _use_stored_snapshot_as_fresh(monkeypatch, prepare_import, files)

    with pytest.raises(AdoptionError, match="processed|template|injection"):
        prepare_import.main(_validate_change_set_args(workdir, "IMPORT"))


def test_import_change_set_rebuilds_template_instead_of_trusting_matching_files(
    tmp_path,
    monkeypatch,
):
    import prepare_import

    workdir = tmp_path / "import-work"
    files = _write_change_set_validation_files(workdir)
    injected = deepcopy(files["import_template"])
    injected.setdefault("Outputs", {})["LeakedInternalApiKey"] = {
        "Value": {"Ref": "InternalApiKey"}
    }
    (workdir / "import-template.json").write_text(
        json.dumps(injected),
        encoding="utf-8",
    )
    cli = CandidateChangeSetCli(
        change_set_type="IMPORT",
        changes=_import_changes(files["snapshot"]),
        parameters=files["import_parameters"],
        processed=injected,
    )
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    _use_stored_snapshot_as_fresh(monkeypatch, prepare_import, files)

    with pytest.raises(
        AdoptionError,
        match="audited|snapshot|artifact|template|Outputs|injection",
    ):
        prepare_import.main(_validate_change_set_args(workdir, "IMPORT"))


def test_import_live_recollection_rejects_synchronized_snapshot_template_tamper(
    tmp_path,
    monkeypatch,
):
    import prepare_import

    workdir = tmp_path / "import-work"
    files = _write_change_set_validation_files(workdir)
    fresh = deepcopy(files["snapshot"])
    tampered_snapshot = deepcopy(fresh)
    tampered_snapshot["function"]["safe_environment"]["CORS_ORIGINS"] = (
        "https://attacker.invalid"
    )
    artifact = CodeArtifact(
        "private-artifacts",
        _FIXTURE_ARTIFACT_KEY,
        "import-version-1",
    )
    tampered_template = build_import_template(tampered_snapshot, artifact)
    (workdir / "sanitized-snapshot.json").write_text(
        json.dumps(_json_safe_snapshot(tampered_snapshot)),
        encoding="utf-8",
    )
    (workdir / "import-template.json").write_text(
        json.dumps(tampered_template),
        encoding="utf-8",
    )
    cli = CandidateChangeSetCli(
        change_set_type="IMPORT",
        changes=_import_changes(tampered_snapshot),
        parameters=files["import_parameters"],
        processed=tampered_template,
    )
    monkeypatch.setattr(prepare_import, "AwsCli", lambda: cli)
    recollections = []

    def recollect(_cli, config):
        recollections.append(config)
        return deepcopy(fresh)

    monkeypatch.setattr(prepare_import, "collect_snapshot", recollect)

    with pytest.raises(AdoptionError, match="runtime changed|fresh|snapshot"):
        prepare_import.main(_validate_change_set_args(workdir, "IMPORT"))
    assert len(recollections) == 1


@pytest.mark.parametrize("expected_type", ["IMPORT", "UPDATE"])
def test_change_set_validator_uses_explicit_workdir_and_candidate_template(tmp_path, monkeypatch, expected_type):
    import prepare_import

    workdir = tmp_path / "explicit-work"
    files = _write_change_set_validation_files(workdir)
    calls = []

    class ValidatorCli:
        def json(self, *args):
            calls.append(args)
            if args[:2] == ("cloudformation", "describe-stacks"):
                return {
                    "StackName": "PacificBioArchive-Database",
                    "ParameterNames": ["InternalApiKey"],
                }
            if args[:2] == ("cloudformation", "describe-change-set"):
                return {
                    "Status": "CREATE_COMPLETE",
                    "ExecutionStatus": "AVAILABLE",
                    "Changes": [],
                    "Parameters": [],
                }
            if args[:2] == ("cloudformation", "get-template"):
                return {"TemplateBody": {"Resources": {}}}
            if args[:2] == ("s3api", "head-object"):
                return {
                    "ChecksumSHA256": _FIXTURE_CODE_SHA256,
                    "VersionId": "import-version-1",
                }
            raise AssertionError(args)

    monkeypatch.setattr(prepare_import, "AwsCli", lambda: ValidatorCli())
    _use_stored_snapshot_as_fresh(monkeypatch, prepare_import, files)
    monkeypatch.setattr(prepare_import, "build_resources_to_import", lambda snapshot: [snapshot])
    monkeypatch.setattr(
        prepare_import,
        "validate_import_artifacts",
        lambda *artifacts: (
            calls.append(("import-artifacts", *artifacts))
            or CodeArtifact(
                "private-artifacts",
                _FIXTURE_ARTIFACT_KEY,
                "import-version-1",
            )
        ),
    )
    monkeypatch.setattr(
        prepare_import,
        "validate_update_artifacts",
        lambda processed, built, packaged, maintained, actual, expected, artifact, **kwargs: calls.append(
            (
                "update-artifacts",
                processed,
                built,
                packaged,
                maintained,
                actual,
                expected,
                artifact,
                kwargs,
            )
        ),
    )
    monkeypatch.setattr(
        prepare_import,
        "verify_update_artifact",
        lambda *_args: files["update_artifact"],
    )
    monkeypatch.setattr(
        prepare_import,
        "_verify_committed_file",
        lambda *_args: None,
    )
    monkeypatch.setattr(prepare_import, "validate_import_change_set", lambda changes, expected: calls.append(("import", expected)))
    monkeypatch.setattr(
        prepare_import,
        "validate_update_change_set",
        lambda changes, processed, role, **kwargs: calls.append(
            ("update", role, kwargs)
        ),
    )
    args = _validate_change_set_args(workdir, expected_type)
    assert prepare_import.main(args) == 0
    if expected_type == "UPDATE":
        get_template = next(call for call in calls if call[:2] == ("cloudformation", "get-template"))
        assert ("--change-set-name", "member-d-update") == get_template[get_template.index("--change-set-name"):get_template.index("--change-set-name") + 2]
        assert any(call[0] == "update-artifacts" for call in calls)
    else:
        assert any(call[0] == "import-artifacts" for call in calls)
    aws_calls = [call for call in calls if isinstance(call, tuple) and len(call) > 1 and call[0] == "cloudformation"]
    assert not any("execute" in " ".join(call) or "create-change-set" in " ".join(call) for call in aws_calls)


@pytest.mark.parametrize(
    ("status", "execution_status"),
    [
        ("CREATE_PENDING", "UNAVAILABLE"),
        ("CREATE_COMPLETE", "EXECUTE_IN_PROGRESS"),
        ("FAILED", "UNAVAILABLE"),
    ],
)
def test_change_set_validator_requires_complete_available_candidate(
    tmp_path,
    monkeypatch,
    status,
    execution_status,
):
    import prepare_import

    class ValidatorCli:
        def json(self, *args):
            return {
                "Status": status,
                "ExecutionStatus": execution_status,
                "Changes": [],
                "Parameters": [],
            }

    monkeypatch.setattr(prepare_import, "AwsCli", lambda: ValidatorCli())
    with pytest.raises(
        AdoptionError,
        match="(?i)Status|ExecutionStatus|complete|available",
    ):
        prepare_import.main([
            "validate-change-set",
            "--region", "ap-southeast-2",
            "--stack", "stack",
            "--change-set", "candidate",
            "--expected-type", "UPDATE",
            "--workdir", str(tmp_path),
        ])


@pytest.mark.parametrize("mismatch", ["stack", "iam", "processed"])
def test_collection_rejects_each_role_identity_mismatch(tmp_path, mismatch):
    class RoleMismatchCli(FakeAwsCli):
        def json(self, *args):
            response = super().json(*args)
            command = " ".join(args)
            if mismatch == "stack" and "list-stack-resources" in command:
                for item in response["StackResourceSummaries"]:
                    if item["LogicalResourceId"] == "QueryLambdaRole":
                        item["PhysicalResourceId"] = "wrong"
            elif mismatch == "iam" and "get-role" in command:
                response["Role"]["RoleName"] = "wrong"
            elif mismatch == "processed" and "get-template" in command:
                response["TemplateBody"]["Resources"]["QueryLambdaRole"]["Properties"]["RoleName"] = "wrong"
            return response
    with pytest.raises(AdoptionError, match="role|Role"):
        collect_snapshot(RoleMismatchCli(), fixture_config(tmp_path))


def test_collection_rejects_role_shape_when_processed_template_only_has_name(tmp_path):
    class NameOnlyRoleCli(FakeAwsCli):
        def json(self, *args):
            response = super().json(*args)
            if args[:2] == ("cloudformation", "get-template"):
                response["TemplateBody"]["Resources"]["QueryLambdaRole"]["Properties"] = {"RoleName": "PacificBioArchive-QueryLambdaRole"}
            return response
    with pytest.raises(AdoptionError, match="live definition"):
        collect_snapshot(NameOnlyRoleCli(), fixture_config(tmp_path))
