import base64
import hashlib
import json
import sys
import traceback
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from unittest.mock import ANY
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).parent))

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
    backup_function_package,
    collect_snapshot,
    run_prepare,
    run_audit,
    verify_artifact_bucket,
)
from test_adoption import valid_snapshot


class FakeAwsCli:
    def __init__(self, caller_arn="arn:aws:iam::111122223333:user/fit5225-cli-deployer", version_id="version-1", uploaded_checksum=None, bucket_state="safe", code_sha=None):
        self.calls = []
        self.put_objects = []
        self.caller_arn = caller_arn
        self.version_id = version_id
        self.uploaded_checksum = uploaded_checksum
        self.bucket_state = bucket_state
        self.code_sha = code_sha

    def json(self, *args):
        self.calls.append(args)
        command = " ".join(args)
        if "get-caller-identity" in command:
            return {"Arn": self.caller_arn, "Account": "111122223333"}
        if "describe-stacks" in command:
            return {"Stacks": [{"StackName": "PacificBioArchive-Database", "StackStatus": "UPDATE_ROLLBACK_COMPLETE", "Parameters": [{"ParameterKey": "InternalApiKey", "ParameterValue": "internal-secret"}]}]}
        if "get-template" in command:
            return {"TemplateBody": valid_snapshot()["stack"]["template"]}
        if "list-stack-resources" in command:
            return {"StackResourceSummaries": [{"LogicalResourceId": logical_id, "PhysicalResourceId": physical_id} for logical_id, physical_id in valid_snapshot()["stack"]["managed"].items()]}
        if "list-stacks" in command:
            return {"StackSummaries": [{"StackName": "PacificBioArchive-Database", "StackStatus": "UPDATE_ROLLBACK_COMPLETE"}]}
        if "describe-type" in command:
            type_name = args[args.index("--type-name") + 1]
            return {"Schema": json.dumps({"primaryIdentifier": valid_snapshot()["type_schemas"][type_name]})}
        if "get-function-configuration" in command:
            configuration = {**valid_snapshot()["function"], "Environment": {"Variables": {**valid_snapshot()["function"]["safe_environment"], "INTERNAL_API_KEY": "internal-secret"}}}
            if self.code_sha:
                configuration["CodeSha256"] = self.code_sha
            return configuration
        if "get-function" in command and "get-function-" not in command:
            return {"Code": {"Location": "https://signed.invalid/?X-Amz-Signature=should-not-leak"}}
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
        if "get-integration" in command:
            return valid_snapshot()["integration"]
        if "get-routes" in command:
            return {"Items": valid_snapshot()["api"]["routes"]}
        if "get-stage" in command:
            return valid_snapshot()["api"]["stage"]
        if "get-authorizers" in command:
            return {"Items": [valid_snapshot()["api"]["authorizer"]]}
        if "get-bucket-location" in command:
            return {"LocationConstraint": None if self.bucket_state != "wrong-region" else "us-east-1"}
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
            return {"VersionId": self.version_id, "ChecksumSHA256": self.uploaded_checksum}
        raise AssertionError(f"unexpected fake AWS call: {args}")

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


def test_collection_queries_only_allowlisted_environment_values(tmp_path):
    cli = FakeAwsCli()
    snapshot = collect_snapshot(cli, fixture_config(tmp_path))
    configuration_calls = [" ".join(call) for call in cli.calls if "get-function-configuration" in call]
    assert len(configuration_calls) == 1
    assert "INTERNAL_API_KEY" not in configuration_calls[0]
    assert "Environment.Variables.REPO_BACKEND" in configuration_calls[0]
    assert "Environment.Variables.CORS_ORIGINS" in configuration_calls[0]
    assert "FunctionName: FunctionName" in configuration_calls[0]
    assert "internal-secret" not in str(snapshot)


def test_audit_refuses_root_before_writing_snapshot(tmp_path):
    with pytest.raises(AdoptionError, match="Root"):
        run_audit(FakeAwsCli(caller_arn="arn:aws:iam::111122223333:root"), fixture_config(tmp_path))
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


@pytest.mark.parametrize("bucket_state", ["public", "unencrypted", "wrong-account", "authorized-cross-account", "wrong-region", "unversioned", "unreadable"])
def test_prepare_rejects_unsafe_artifact_bucket(bucket_state):
    with pytest.raises(AdoptionError, match="artifact bucket"):
        verify_artifact_bucket(FakeAwsCli(bucket_state=bucket_state), "artifacts", "ap-southeast-2")


def test_backup_rejects_non_zip():
    digest = base64.b64encode(hashlib.sha256(b"not-a-zip").digest()).decode()
    with pytest.raises(AdoptionError, match="zip"):
        backup_function_package(FakeAwsCli(), {"Location": "https://signed.invalid", "CodeSha256": digest}, "private-artifacts", lambda _url, path: path.write_bytes(b"not-a-zip"))


def test_backup_rejects_uploaded_checksum_mismatch():
    package = lambda_zip_bytes()
    digest = base64.b64encode(hashlib.sha256(package).digest()).decode()
    with pytest.raises(AdoptionError, match="uploaded checksum"):
        backup_function_package(FakeAwsCli(uploaded_checksum="wrong"), {"Location": "https://signed.invalid", "CodeSha256": digest}, "private-artifacts", lambda _url, path: path.write_bytes(package))


def test_change_set_must_contain_exactly_eighteen_imports():
    expected = build_resources_to_import(valid_snapshot())
    changes = [{"ResourceChange": {"Action": "Import", "LogicalResourceId": item["LogicalResourceId"], "ResourceType": item["ResourceType"], "Replacement": "False"}} for item in expected]
    validate_import_change_set(changes, expected)


@pytest.mark.parametrize("action", ["Add", "Modify", "Remove", "Dynamic"])
def test_change_set_rejects_every_non_import_action(action):
    expected = build_resources_to_import(valid_snapshot())
    with pytest.raises(AdoptionError, match="18 Import"):
        validate_import_change_set([{"ResourceChange": {"Action": action, "LogicalResourceId": "QueryFunction", "ResourceType": "AWS::Lambda::Function", "Replacement": "False"}}], expected)


def test_processed_update_reuses_function_and_has_no_implicit_role():
    processed = {"Resources": {"QueryLambdaRole": {"Type": "AWS::IAM::Role"}, "QueryFunction": {"Type": "AWS::Lambda::Function", "Properties": {"FunctionName": "PacificBioArchive-QueryLambda", "Role": {"Fn::GetAtt": ["QueryLambdaRole", "Arn"]}}}, "QueryIntegration": {"Type": "AWS::ApiGatewayV2::Integration"}}}
    processed["Resources"].update({logical_id: {"Type": "AWS::ApiGatewayV2::Route"} for logical_id in valid_snapshot()["api"]["routes"] and build_resources_to_import(valid_snapshot())[2:] and [item["LogicalResourceId"] for item in build_resources_to_import(valid_snapshot())[2:]]})
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
    assert "internal-secret" not in str(snapshot)
    assert "X-Amz-Signature" not in str(snapshot)


def test_audit_baseline_compares_runtime_and_output_stays_sanitized(tmp_path, capsys):
    config = fixture_config(tmp_path)
    first = run_audit(FakeAwsCli(), config)
    second = run_audit(FakeAwsCli(), config, baseline=first)
    assert second.read_bytes() == first.read_bytes()
    captured = capsys.readouterr()
    assert "internal-secret" not in captured.out + captured.err
    assert "X-Amz-Signature" not in captured.out + captured.err


def test_prepare_writes_only_sanitized_deterministic_artifacts(tmp_path, capsys):
    package = lambda_zip_bytes()
    digest = base64.b64encode(hashlib.sha256(package).digest()).decode()
    config = fixture_config(tmp_path)
    artifact_paths = run_prepare(
        FakeAwsCli(uploaded_checksum=digest, code_sha=digest),
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
    captured = capsys.readouterr()
    assert "internal-secret" not in captured.out + captured.err
    assert "X-Amz-Signature" not in captured.out + captured.err


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
        "Environment": {"Variables": {**valid_snapshot()["function"]["safe_environment"], "INTERNAL_API_KEY": "internal-secret"}},
    }
    result = _sanitized_function(configuration)
    assert "FunctionArn" not in result
    assert result["RuntimeManagementConfig"] == {"UpdateRuntimeOn": "Auto"}
    assert result["SnapStart"] == {"ApplyOn": "None"}


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


@pytest.mark.parametrize("expected_type", ["IMPORT", "UPDATE"])
def test_change_set_validator_uses_explicit_workdir_and_candidate_template(tmp_path, monkeypatch, expected_type):
    import prepare_import

    workdir = tmp_path / "explicit-work"
    workdir.mkdir()
    (workdir / "sanitized-snapshot.json").write_text(json.dumps({"role": {"processed_definition": {}}}), encoding="utf-8")
    calls = []

    class ValidatorCli:
        def json(self, *args):
            calls.append(args)
            if args[:2] == ("cloudformation", "describe-change-set"):
                return {"Changes": []}
            if args[:2] == ("cloudformation", "get-template"):
                return {"TemplateBody": {"Resources": {}}}
            raise AssertionError(args)

    monkeypatch.setattr(prepare_import, "AwsCli", lambda: ValidatorCli())
    monkeypatch.setattr(prepare_import, "build_resources_to_import", lambda snapshot: [snapshot])
    monkeypatch.setattr(prepare_import, "validate_import_change_set", lambda changes, expected: calls.append(("import", expected)))
    monkeypatch.setattr(prepare_import, "validate_update_change_set", lambda changes, processed, role: calls.append(("update", role)))
    assert prepare_import.main(["validate-change-set", "--region", "ap-southeast-2", "--stack", "stack", "--change-set", "candidate-update", "--expected-type", expected_type, "--workdir", str(workdir)]) == 0
    if expected_type == "UPDATE":
        get_template = next(call for call in calls if call[:2] == ("cloudformation", "get-template"))
        assert ("--change-set-name", "candidate-update") == get_template[get_template.index("--change-set-name"):get_template.index("--change-set-name") + 2]
    aws_calls = [call for call in calls if isinstance(call, tuple) and len(call) > 1 and call[0] == "cloudformation"]
    assert not any("execute" in " ".join(call) or "create-change-set" in " ".join(call) for call in aws_calls)
