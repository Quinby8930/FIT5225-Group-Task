from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from adoption import (
    AdoptionError,
    CodeArtifact,
    ROUTES_BY_LOGICAL_ID,
    assert_runtime_unchanged,
    build_import_template,
    build_parameters_to_reuse,
    build_resources_to_import,
    validate_update_change_set,
    validate_snapshot,
)


def valid_snapshot():
    routes = []
    for index, (_logical_id, contract) in enumerate(
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
        "caller": {"Arn": "arn:aws:iam::111122223333:user/fit5225-cli-deployer", "Account": "111122223333"},
        "region": "ap-southeast-2",
        "stack": {
            "name": "PacificBioArchive-Database",
            "status": "UPDATE_ROLLBACK_COMPLETE",
            "parameters": ["InternalApiKey"],
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
            "Description": "",
            "Tags": {},
            "SnapStart": {"ApplyOn": "None"},
            "PackageType": "Zip",
            "Architectures": ["x86_64"],
            "Layers": [],
            "EphemeralStorage": {"Size": 512},
            "VpcConfig": {"SubnetIds": [], "SecurityGroupIds": [], "Ipv6AllowedForDualStack": False},
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
                "REPO_BACKEND", "DYNAMODB_TABLE", "SUBSCRIPTIONS_TABLE",
                "INTERNAL_API_KEY", "NOTIFICATIONS_TABLE", "CORS_ORIGINS",
                "TAG_DETECTOR_BACKEND",
            ],
            "safe_environment": {
                "REPO_BACKEND": "dynamodb", "DYNAMODB_TABLE": "PacificBioArchiveFiles",
                "SUBSCRIPTIONS_TABLE": "PacificBioArchiveSubscriptions",
                "NOTIFICATIONS_TABLE": "PacificBioArchiveNotifications",
                "CORS_ORIGINS": "http://localhost:3000", "TAG_DETECTOR_BACKEND": "remote",
            },
            "resource_policy": {"Statement": [{
                "Effect": "Allow", "Principal": {"Service": "apigateway.amazonaws.com"},
                "Action": "lambda:InvokeFunction",
                "Resource": "arn:aws:lambda:ap-southeast-2:111122223333:function:PacificBioArchive-QueryLambda",
            }]},
        },
        "integration": {
            "IntegrationId": "fbjojun", "IntegrationType": "AWS_PROXY",
            "IntegrationMethod": "POST", "PayloadFormatVersion": "2.0",
            "IntegrationUri": "arn:aws:apigateway:ap-southeast-2:lambda:path/2015-03-31/functions/arn:aws:lambda:ap-southeast-2:111122223333:function:PacificBioArchive-QueryLambda/invocations",
        },
        "type_schemas": {
            "AWS::Lambda::Function": ["/properties/FunctionName"],
            "AWS::ApiGatewayV2::Integration": ["/properties/ApiId", "/properties/IntegrationId"],
            "AWS::ApiGatewayV2::Route": ["/properties/ApiId", "/properties/RouteId"],
        },
        "owned_physical_ids": set(),
    }


def test_valid_snapshot_accepts_exact_sixteen_member_d_routes():
    validate_snapshot(valid_snapshot())
    assert len(ROUTES_BY_LOGICAL_ID) == 16


def test_root_caller_is_rejected():
    snapshot = valid_snapshot()
    snapshot["caller"]["Arn"] = "arn:aws:iam::111122223333:root"
    with pytest.raises(AdoptionError, match="Root"):
        validate_snapshot(snapshot)


def test_primary_identifier_schema_mismatch_is_rejected():
    snapshot = valid_snapshot()
    snapshot["type_schemas"]["AWS::ApiGatewayV2::Route"] = ["/properties/RouteKey"]
    with pytest.raises(AdoptionError, match="primary identifier"):
        validate_snapshot(snapshot)


def test_post_import_runtime_comparison_rejects_route_or_function_change():
    before = valid_snapshot()
    after = deepcopy(before)
    after["api"]["routes"][0]["RouteId"] = "replacement-route"
    with pytest.raises(AdoptionError, match="runtime changed"):
        assert_runtime_unchanged(before, after)


def test_member_b_route_is_not_selected_from_candidate_routes():
    snapshot = valid_snapshot()
    snapshot["api"]["routes"].append({"RouteId": "broute", "RouteKey": "POST /upload-url", "Target": "integrations/media", "AuthorizationType": "JWT", "AuthorizerId": "7ir7fs"})
    validate_snapshot(snapshot)
    assert "POST /upload-url" not in {contract.route_key for contract in ROUTES_BY_LOGICAL_ID.values()}


@pytest.mark.parametrize(("mutation", "message"), [
    (lambda value: value["api"]["routes"].pop(), "missing"),
    (lambda value: value["api"]["routes"][0].update({"Target": "integrations/wrong"}), "integration"),
    (lambda value: value["integration"].update({"PayloadFormatVersion": "1.0"}), "payload"),
    (lambda value: value["function"]["environment_names"].append("UNEXPECTED_SECRET"), "environment"),
    (lambda value: value["function"].update({"PackageType": "Image"}), "package"),
])
def test_snapshot_mismatch_fails_closed(mutation, message):
    snapshot = valid_snapshot()
    mutation(snapshot)
    with pytest.raises(AdoptionError, match=message):
        validate_snapshot(snapshot)


def test_import_manifest_contains_lambda_integration_and_sixteen_routes():
    manifest = build_resources_to_import(valid_snapshot())
    assert len(manifest) == 18
    assert {item["LogicalResourceId"] for item in manifest} == {
        "QueryFunction", "QueryIntegration", *ROUTES_BY_LOGICAL_ID,
    }
    assert all("OPTIONS" not in str(item) for item in manifest)
    assert all("upload-url" not in str(item) for item in manifest)
    assert all("asset-urls" not in str(item) for item in manifest)


def test_import_template_retains_every_imported_resource_without_secret_value():
    template = build_import_template(valid_snapshot(), CodeArtifact("private-artifacts", "backups/code.zip", "version-1"))
    imported = {"QueryFunction", "QueryIntegration", *ROUTES_BY_LOGICAL_ID}
    for logical_id in imported:
        assert template["Resources"][logical_id]["DeletionPolicy"] == "Retain"
        assert template["Resources"][logical_id]["UpdateReplacePolicy"] == "Retain"
    assert template["Parameters"]["InternalApiKey"] == {"Type": "String", "NoEcho": True, "MinLength": 1}
    rendered = str(template)
    assert "fixture-secret" not in rendered
    assert "POST /upload-url" not in rendered


def test_import_template_keeps_exact_live_lambda_rollback_package():
    template = build_import_template(valid_snapshot(), CodeArtifact("private-artifacts", "backups/code.zip", "version-1"))
    function = template["Resources"]["QueryFunction"]
    assert function["Type"] == "AWS::Lambda::Function"
    assert function["Properties"]["FunctionName"] == "PacificBioArchive-QueryLambda"
    assert function["Properties"]["Code"] == {"S3Bucket": "private-artifacts", "S3Key": "backups/code.zip", "S3ObjectVersion": "version-1"}
    assert function["Properties"]["Environment"]["Variables"]["INTERNAL_API_KEY"] == {"Ref": "InternalApiKey"}


def test_import_parameters_reuse_existing_internal_key_without_reading_it():
    parameters = build_parameters_to_reuse(valid_snapshot())
    assert {"ParameterKey": "InternalApiKey", "UsePreviousValue": True} in parameters
    assert all("ParameterValue" not in item for item in parameters)


def test_template_refuses_missing_existing_internal_key_parameter():
    snapshot = valid_snapshot()
    snapshot["stack"]["parameters"] = []
    with pytest.raises(AdoptionError, match="InternalApiKey"):
        build_import_template(snapshot, CodeArtifact("private-artifacts", "backups/code.zip", "version-1"))


def test_post_import_baseline_allows_expected_managed_resource_ownership_only():
    before = valid_snapshot()
    after = deepcopy(before)
    after["stack"]["managed"]["QueryFunction"] = "PacificBioArchive-QueryLambda"
    after["stack"]["managed"]["QueryIntegration"] = "fbjojun"
    for logical_id, route in zip(ROUTES_BY_LOGICAL_ID, after["api"]["routes"]):
        after["stack"]["managed"][logical_id] = route["RouteId"]
    assert_runtime_unchanged(before, after)


@pytest.mark.parametrize("replacement", ["Conditional", None])
def test_update_rejects_conditional_or_missing_protected_resource(replacement):
    processed = {"Resources": {"QueryLambdaRole": {"Type": "AWS::IAM::Role"}, "QueryFunction": {"Type": "AWS::Lambda::Function", "Properties": {"Role": {"Fn::GetAtt": ["QueryLambdaRole", "Arn"]}}}, "QueryIntegration": {"Type": "AWS::ApiGatewayV2::Integration"}}}
    processed["Resources"].update({logical_id: {"Type": "AWS::ApiGatewayV2::Route"} for logical_id in ROUTES_BY_LOGICAL_ID})
    if replacement is None:
        processed["Resources"].pop("AuthTestRoute")
    changes = [] if replacement is None else [{"ResourceChange": {"Action": "Modify", "LogicalResourceId": "QueryFunction", "Replacement": replacement}}]
    with pytest.raises(AdoptionError):
        validate_update_change_set(changes, processed)


def test_resource_policy_requires_exact_supported_mapping():
    snapshot = valid_snapshot()
    snapshot["function"]["resource_policy"]["Statement"][0]["Resource"] = "wrong"
    with pytest.raises(AdoptionError, match="resource policy"):
        validate_snapshot(snapshot)


@pytest.mark.parametrize("resource", ["integration", "route"])
def test_unknown_api_gateway_configuration_still_fails_closed(resource):
    snapshot = valid_snapshot()
    target = (
        snapshot["integration"]
        if resource == "integration"
        else snapshot["api"]["routes"][0]
    )
    target["UnexpectedFutureField"] = "must-not-be-silently-dropped"

    with pytest.raises(AdoptionError, match="unsupported configuration"):
        validate_snapshot(snapshot)
