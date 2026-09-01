from copy import deepcopy
from inspect import signature
from pathlib import Path
import re
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import adoption

from adoption import (
    AdoptionError,
    CodeArtifact,
    OPTIONS_ROUTES_BY_LOGICAL_ID,
    ROUTES_BY_LOGICAL_ID,
    assert_runtime_unchanged,
    build_import_template,
    build_parameters_to_reuse,
    build_resources_to_import,
    validate_update_change_set,
    validate_snapshot,
    validate_lambda_policy_after_update,
)


def _missing_post_import_feature(*_args, **_kwargs):
    pytest.fail("post-import evidence gate is not implemented")


assert_post_import_equivalent = getattr(
    adoption,
    "assert_post_import_equivalent",
    _missing_post_import_feature,
)
assert_import_preview_equivalent = getattr(
    adoption,
    "assert_import_preview_equivalent",
    _missing_post_import_feature,
)
assert_post_import_boundary_current = getattr(
    adoption,
    "assert_post_import_boundary_current",
    _missing_post_import_feature,
)
assert_update_rollback_equivalent = getattr(
    adoption,
    "assert_update_rollback_equivalent",
    _missing_post_import_feature,
)
expected_imported_physical_ids = getattr(
    adoption,
    "expected_imported_physical_ids",
    _missing_post_import_feature,
)
validate_import_preview_snapshot = getattr(
    adoption,
    "validate_import_preview_snapshot",
    _missing_post_import_feature,
)
validate_update_rollback_snapshot = getattr(
    adoption,
    "validate_update_rollback_snapshot",
    _missing_post_import_feature,
)


_EXPECTED_SOURCE_STACK = "PacificBioArchive-Database"
_EXPECTED_TARGET_STACK = "PacificBioArchive-QueryAdoption"
_EXPECTED_ORIGINAL_STACK_RESOURCES = {
    "FilesTable",
    "SubscriptionsTable",
    "NotificationsTable",
    "QueryLambdaRole",
}
_EXPECTED_IMPORT_RESOURCES = {
    "ReservationsTable",
    "QueryFunction",
    "QueryIntegration",
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
_EXPECTED_IMPORT_RESOURCE_TYPES = {
    "ReservationsTable": "AWS::DynamoDB::Table",
    "QueryFunction": "AWS::Lambda::Function",
    "QueryIntegration": "AWS::ApiGatewayV2::Integration",
    "AuthTestRoute": "AWS::ApiGatewayV2::Route",
    "QueryByTagsRoute": "AWS::ApiGatewayV2::Route",
    "QueryBySpeciesRoute": "AWS::ApiGatewayV2::Route",
    "QueryByThumbnailRoute": "AWS::ApiGatewayV2::Route",
    "QueryByFileRoute": "AWS::ApiGatewayV2::Route",
    "EditTagsRoute": "AWS::ApiGatewayV2::Route",
    "DeleteFilesRoute": "AWS::ApiGatewayV2::Route",
    "SubscribeRoute": "AWS::ApiGatewayV2::Route",
    "UnsubscribeRoute": "AWS::ApiGatewayV2::Route",
    "SubscriptionsRoute": "AWS::ApiGatewayV2::Route",
    "NotificationsRoute": "AWS::ApiGatewayV2::Route",
    "ReserveUploadRoute": "AWS::ApiGatewayV2::Route",
    "AcquireProcessingRoute": "AWS::ApiGatewayV2::Route",
    "CompleteFileRoute": "AWS::ApiGatewayV2::Route",
    "FailFileRoute": "AWS::ApiGatewayV2::Route",
    "AuthorizeAssetsRoute": "AWS::ApiGatewayV2::Route",
}
_EXPECTED_IMPORT_PARAMETER_VALUES = {
    "ExistingQueryLambdaRoleArn": (
        "arn:aws:iam::111122223333:role/"
        "PacificBioArchive-QueryLambdaRole"
    ),
    "ExistingHttpApiId": "2dd2aqb32j",
    "ExistingJwtAuthorizerId": "7ir7fs",
}


def _historical_lambda_permissions(
    account="111122223333",
    region="ap-southeast-2",
    api_id="2dd2aqb32j",
):
    function_arn = (
        f"arn:aws:lambda:{region}:{account}:"
        "function:PacificBioArchive-QueryLambda"
    )
    source_prefix = f"arn:aws:execute-api:{region}:{account}:{api_id}"
    return [
        {
            "Sid": "apigateway-query-lambda",
            "Effect": "Allow",
            "Principal": {"Service": "apigateway.amazonaws.com"},
            "Action": "lambda:InvokeFunction",
            "Resource": function_arn,
            "Condition": {
                "ArnLike": {"AWS:SourceArn": f"{source_prefix}/*/*/*"}
            },
        },
        {
            "Sid": "AllowAuthTestInvoke",
            "Effect": "Allow",
            "Principal": {"Service": "apigateway.amazonaws.com"},
            "Action": "lambda:InvokeFunction",
            "Resource": function_arn,
            "Condition": {
                "ArnLike": {
                    "AWS:SourceArn": f"{source_prefix}/*/GET/auth-test"
                }
            },
        },
        {
            "Sid": "AllowApiGatewayInvokeAllRoutes-20260829030023",
            "Effect": "Allow",
            "Principal": {"Service": "apigateway.amazonaws.com"},
            "Action": "lambda:InvokeFunction",
            "Resource": function_arn,
            "Condition": {
                "ArnLike": {"AWS:SourceArn": f"{source_prefix}/*/*"}
            },
        },
    ]


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
                "Parameters": {
                    "InternalApiKey": {
                        "Type": "String",
                        "NoEcho": True,
                        "MinLength": 1,
                    }
                },
                "Resources": {
                    "FilesTable": {"Type": "AWS::DynamoDB::Table"},
                    "SubscriptionsTable": {"Type": "AWS::DynamoDB::Table"},
                    "NotificationsTable": {"Type": "AWS::DynamoDB::Table"},
                    "QueryLambdaRole": {"Type": "AWS::IAM::Role", "Properties": {
                        "RoleName": "PacificBioArchive-QueryLambdaRole",
                        "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": []},
                    }},
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
            "CodeSha256": "APsUW+8+ymZvVYmfkaKba20+sWzR3PMJPDimXIiqoIY=",
            "RevisionId": "fixture-function-revision",
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
            "resource_policy": {
                "Statement": _historical_lambda_permissions()
            },
            "resource_policy_revision_id": "fixture-policy-revision",
            "provisioned_concurrency": [],
        },
        "integration": {
            "IntegrationId": "fbjojun", "IntegrationType": "AWS_PROXY",
            "IntegrationMethod": "POST", "PayloadFormatVersion": "2.0",
            "IntegrationUri": "arn:aws:apigateway:ap-southeast-2:lambda:path/2015-03-31/functions/arn:aws:lambda:ap-southeast-2:111122223333:function:PacificBioArchive-QueryLambda/invocations",
        },
        "reservations_table": {
            "TableName": "PacificBioArchiveUploadReservations",
            "TableStatus": "ACTIVE",
            "TableArn": "arn:aws:dynamodb:ap-southeast-2:111122223333:table/PacificBioArchiveUploadReservations",
            "BillingMode": "PAY_PER_REQUEST",
            "AttributeDefinitions": [
                {"AttributeName": "reservation_key", "AttributeType": "S"},
            ],
            "KeySchema": [
                {"AttributeName": "reservation_key", "KeyType": "HASH"},
            ],
            "GlobalSecondaryIndexes": [],
            "LocalSecondaryIndexes": [],
            "StreamSpecification": None,
            "DeletionProtectionEnabled": False,
            "TableClass": "STANDARD",
            "Replicas": [],
            "Tags": [],
            "TimeToLiveStatus": "DISABLED",
            "PointInTimeRecoveryStatus": "DISABLED",
            "SSEMode": "AWS_OWNED",
            "OnDemandThroughput": None,
            "WarmThroughput": None,
            "MultiRegionConsistency": None,
            "ResourcePolicy": None,
            "KinesisDataStreamDestinations": [],
            "ContributorInsightsStatus": "DISABLED",
            "VectorIndexes": [],
            "GlobalTableWitnesses": [],
        },
        "type_schemas": {
            "AWS::DynamoDB::Table": ["/properties/TableName"],
            "AWS::Lambda::Function": ["/properties/FunctionName"],
            "AWS::ApiGatewayV2::Integration": ["/properties/ApiId", "/properties/IntegrationId"],
            "AWS::ApiGatewayV2::Route": ["/properties/ApiId", "/properties/RouteId"],
        },
        "import_owners": {
            logical_id: None for logical_id in _EXPECTED_IMPORT_RESOURCES
        },
        "target_stack": {
            "name": "PacificBioArchive-QueryAdoption",
            "status": None,
            "resources": {},
        },
        "role": {
            "role_name": "PacificBioArchive-QueryLambdaRole",
            "account": "111122223333",
            "region": "ap-southeast-2",
            "path": "/",
            "trust_policy": {"Version": "2012-10-17", "Statement": []},
            "permissions_boundary": None,
            "managed_policies": [], "inline_policies": {}, "tags": [],
            "drift": {"status": "IN_SYNC", "differences": []},
            "processed_definition": {"Type": "AWS::IAM::Role", "Properties": {
                "RoleName": "PacificBioArchive-QueryLambdaRole",
                "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": []},
            }},
        },
    }


def post_import_snapshot():
    snapshot = valid_snapshot()
    expected = expected_imported_physical_ids(snapshot)
    snapshot["import_owners"] = {
        logical_id: _EXPECTED_TARGET_STACK for logical_id in expected
    }
    snapshot["target_stack"] = {
        "name": _EXPECTED_TARGET_STACK,
        "status": "IMPORT_COMPLETE",
        "resources": deepcopy(expected),
    }
    return snapshot


def import_preview_snapshot():
    snapshot = valid_snapshot()
    snapshot["target_stack"] = {
        "name": _EXPECTED_TARGET_STACK,
        "status": "REVIEW_IN_PROGRESS",
        "resources": {},
    }
    return snapshot


def update_rollback_snapshot():
    snapshot = post_import_snapshot()
    snapshot["target_stack"]["status"] = "UPDATE_ROLLBACK_COMPLETE"
    return snapshot


def test_import_preview_gate_accepts_only_review_shell_with_all_unmanaged():
    validate_import_preview_snapshot(import_preview_snapshot())
    assert_import_preview_equivalent(valid_snapshot(), import_preview_snapshot())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda snapshot: snapshot["target_stack"].update({"status": None}),
        lambda snapshot: snapshot["target_stack"].update(
            {"status": "IMPORT_COMPLETE"}
        ),
        lambda snapshot: snapshot["target_stack"]["resources"].update(
            {
                "QueryFunction": {
                    "physical_id": "PacificBioArchive-QueryLambda",
                    "resource_type": "AWS::Lambda::Function",
                }
            }
        ),
        lambda snapshot: snapshot["import_owners"].pop("QueryFunction"),
        lambda snapshot: snapshot["import_owners"].update(
            {"Unexpected": None}
        ),
        lambda snapshot: snapshot["import_owners"].update(
            {"QueryFunction": _EXPECTED_TARGET_STACK}
        ),
        lambda snapshot: snapshot["import_owners"].update(
            {"QueryFunction": "ForeignStack"}
        ),
    ],
    ids=(
        "target-absent",
        "wrong-status",
        "target-has-resource",
        "missing-owner",
        "extra-owner",
        "target-owner",
        "foreign-owner",
    ),
)
def test_import_preview_gate_rejects_every_other_phase_or_owner(mutation):
    snapshot = import_preview_snapshot()
    mutation(snapshot)

    with pytest.raises(AdoptionError):
        validate_import_preview_snapshot(snapshot)


def test_import_preview_equivalence_rejects_runtime_or_source_change():
    preview = import_preview_snapshot()
    preview["function"]["RevisionId"] = "changed-during-preview"

    with pytest.raises(AdoptionError):
        assert_import_preview_equivalent(valid_snapshot(), preview)


def test_post_import_same_boundary_accepts_fresh_identical_evidence():
    assert_post_import_boundary_current(
        post_import_snapshot(),
        post_import_snapshot(),
    )


def test_update_rollback_gate_accepts_exact_import_complete_boundary():
    validate_update_rollback_snapshot(update_rollback_snapshot())
    assert_update_rollback_equivalent(
        post_import_snapshot(),
        update_rollback_snapshot(),
    )


def test_post_import_gate_remains_import_complete_only():
    with pytest.raises(AdoptionError):
        adoption.validate_post_import_snapshot(update_rollback_snapshot())


def test_post_import_gate_accepts_exact_ownership_only_transition():
    assert_post_import_equivalent(valid_snapshot(), post_import_snapshot())


def test_expected_imported_physical_ids_are_exact_and_typed():
    expected = expected_imported_physical_ids(valid_snapshot())

    assert expected == {
        "ReservationsTable": {
            "physical_id": "PacificBioArchiveUploadReservations",
            "resource_type": "AWS::DynamoDB::Table",
        },
        "QueryFunction": {
            "physical_id": "PacificBioArchive-QueryLambda",
            "resource_type": "AWS::Lambda::Function",
        },
        "QueryIntegration": {
            "physical_id": "fbjojun",
            "resource_type": "AWS::ApiGatewayV2::Integration",
        },
        **{
            logical_id: {
                "physical_id": f"route{index:02d}",
                "resource_type": "AWS::ApiGatewayV2::Route",
            }
            for index, logical_id in enumerate(ROUTES_BY_LOGICAL_ID, start=1)
        },
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda snapshot: snapshot["target_stack"].update({"name": "WrongStack"}),
        lambda snapshot: snapshot["target_stack"].update({"status": "UPDATE_COMPLETE"}),
        lambda snapshot: snapshot["target_stack"]["resources"]["QueryFunction"].update(
            {"resource_type": "AWS::Lambda::Version"}
        ),
        lambda snapshot: snapshot["target_stack"]["resources"]["QueryFunction"].update(
            {"physical_id": "wrong-function"}
        ),
        lambda snapshot: snapshot["target_stack"]["resources"].pop("QueryFunction"),
        lambda snapshot: snapshot["target_stack"]["resources"].update(
            {"Unexpected": {"physical_id": "x", "resource_type": "AWS::S3::Bucket"}}
        ),
        lambda snapshot: snapshot["import_owners"].update({"QueryFunction": "OtherStack"}),
        lambda snapshot: snapshot["import_owners"].pop("QueryFunction"),
    ],
    ids=(
        "wrong-target-name",
        "wrong-target-status",
        "wrong-resource-type",
        "wrong-physical-id",
        "partial-target",
        "extra-target",
        "foreign-owner",
        "partial-owners",
    ),
)
def test_post_import_gate_rejects_invalid_ownership_evidence(mutation):
    observed = post_import_snapshot()
    mutation(observed)

    with pytest.raises(AdoptionError):
        assert_post_import_equivalent(valid_snapshot(), observed)


_POST_IMPORT_FUNCTION_FIELDS = (
    "FunctionName",
    "Runtime",
    "Handler",
    "Role",
    "Timeout",
    "MemorySize",
    "Description",
    "Tags",
    "SnapStart",
    "PackageType",
    "Architectures",
    "Layers",
    "EphemeralStorage",
    "VpcConfig",
    "FileSystemConfigs",
    "KmsKeyArn",
    "DeadLetterConfig",
    "TracingConfig",
    "LoggingConfig",
    "CodeSigningConfigArn",
    "RuntimeManagementConfig",
    "ReservedConcurrentExecutions",
    "CodeSha256",
    "RevisionId",
    "resource_policy",
    "resource_policy_revision_id",
    "provisioned_concurrency",
)


@pytest.mark.parametrize("field", _POST_IMPORT_FUNCTION_FIELDS)
def test_post_import_gate_rejects_each_lambda_field_mutation(field):
    before = valid_snapshot()
    observed = post_import_snapshot()
    observed["function"][field] = {"changed": True}

    with pytest.raises(AdoptionError):
        assert_post_import_equivalent(before, observed)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda function: function["environment_names"].remove("INTERNAL_API_KEY"),
        lambda function: function["environment_names"].append("UNEXPECTED_SECRET"),
        lambda function: function["safe_environment"].update({"REPO_BACKEND": "changed"}),
        lambda function: function["safe_environment"].pop("CORS_ORIGINS"),
    ],
    ids=("missing-name", "extra-name", "changed-safe-value", "missing-safe-value"),
)
def test_post_import_gate_rejects_environment_evidence_mutation(mutation):
    observed = post_import_snapshot()
    mutation(observed["function"])

    with pytest.raises(AdoptionError):
        assert_post_import_equivalent(valid_snapshot(), observed)


def _same_boundary_cases():
    return (
        (
            assert_post_import_boundary_current,
            post_import_snapshot(),
            post_import_snapshot(),
        ),
        (
            assert_update_rollback_equivalent,
            post_import_snapshot(),
            update_rollback_snapshot(),
        ),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda snapshot: snapshot["stack"]["managed"].update(
            {"FilesTable": "WrongFilesTable"}
        ),
        lambda snapshot: snapshot["target_stack"]["resources"][
            "QueryFunction"
        ].update({"physical_id": "wrong-function"}),
        lambda snapshot: snapshot["import_owners"].update(
            {"QueryFunction": "ForeignStack"}
        ),
        lambda snapshot: snapshot["api"].update({"id": "wrong-api"}),
        lambda snapshot: snapshot["api"]["authorizer"].update(
            {"JwtConfiguration": {"Issuer": "https://changed.invalid"}}
        ),
    ],
    ids=(
        "source-physical",
        "target-physical",
        "target-owner",
        "api-id",
        "complete-authorizer",
    ),
)
def test_same_boundary_gates_reject_scope_or_ownership_mutation(mutation):
    for gate, baseline, observed in _same_boundary_cases():
        mutation(observed)
        with pytest.raises(AdoptionError):
            gate(baseline, observed)


@pytest.mark.parametrize("field", _POST_IMPORT_FUNCTION_FIELDS)
def test_same_boundary_gates_reject_each_lambda_field_mutation(field):
    for gate, baseline, observed in _same_boundary_cases():
        observed["function"][field] = {"changed": True}
        with pytest.raises(AdoptionError):
            gate(baseline, observed)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda function: function["environment_names"].remove(
            "INTERNAL_API_KEY"
        ),
        lambda function: function["safe_environment"].update(
            {"DYNAMODB_TABLE": "WrongFilesTable"}
        ),
    ],
    ids=("environment-names", "core-table-value"),
)
def test_same_boundary_gates_reject_environment_mutation(mutation):
    for gate, baseline, observed in _same_boundary_cases():
        mutation(observed["function"])
        with pytest.raises(AdoptionError):
            gate(baseline, observed)


_COMPLETE_INTEGRATION = {
    "IntegrationId": "fbjojun",
    "IntegrationType": "AWS_PROXY",
    "IntegrationSubtype": "",
    "IntegrationMethod": "POST",
    "PayloadFormatVersion": "2.0",
    "IntegrationUri": "arn:aws:apigateway:ap-southeast-2:lambda:path/2015-03-31/functions/arn:aws:lambda:ap-southeast-2:111122223333:function:PacificBioArchive-QueryLambda/invocations",
    "ConnectionType": "INTERNET",
    "ConnectionId": None,
    "ContentHandlingStrategy": None,
    "CredentialsArn": None,
    "Description": "query integration",
    "PassthroughBehavior": None,
    "RequestParameters": {"append:header.x-test": "'safe'"},
    "RequestTemplates": {},
    "ResponseParameters": {},
    "TemplateSelectionExpression": None,
    "TlsConfig": {"ServerNameToVerify": "example.invalid"},
    "TimeoutInMillis": 29000,
}


@pytest.mark.parametrize("field", tuple(_COMPLETE_INTEGRATION))
def test_post_import_gate_rejects_each_captured_integration_field_mutation(field):
    before = valid_snapshot()
    observed = post_import_snapshot()
    before["integration"] = deepcopy(_COMPLETE_INTEGRATION)
    observed["integration"] = deepcopy(_COMPLETE_INTEGRATION)
    observed["integration"][field] = "changed"

    with pytest.raises(AdoptionError):
        assert_post_import_equivalent(before, observed)


@pytest.mark.parametrize("field", tuple(_COMPLETE_INTEGRATION))
def test_same_boundary_gates_reject_each_integration_field_mutation(field):
    for gate, baseline, observed in _same_boundary_cases():
        baseline["integration"] = deepcopy(_COMPLETE_INTEGRATION)
        observed["integration"] = deepcopy(_COMPLETE_INTEGRATION)
        observed["integration"][field] = "changed"
        with pytest.raises(AdoptionError):
            gate(baseline, observed)


_COMPLETE_ROUTE_FIELDS = {
    "ApiKeyRequired": False,
    "AuthorizationScopes": ["query:read"],
    "ModelSelectionExpression": "$request.body.action",
    "OperationName": "QueryRoute",
    "RequestModels": {"application/json": "model"},
    "RequestParameters": {"route.request.header.x-test": {"Required": False}},
    "RouteResponseSelectionExpression": "$default",
}


@pytest.mark.parametrize("logical_id", tuple(ROUTES_BY_LOGICAL_ID))
@pytest.mark.parametrize(
    "field",
    (
        "RouteId",
        "RouteKey",
        "Target",
        "AuthorizationType",
        "AuthorizerId",
        *_COMPLETE_ROUTE_FIELDS,
    ),
)
def test_post_import_gate_rejects_every_captured_field_for_each_route(
    logical_id,
    field,
):
    before = valid_snapshot()
    observed = post_import_snapshot()
    route_key = ROUTES_BY_LOGICAL_ID[logical_id].route_key
    before_route = next(route for route in before["api"]["routes"] if route["RouteKey"] == route_key)
    observed_route = next(route for route in observed["api"]["routes"] if route["RouteKey"] == route_key)
    before_route.update(deepcopy(_COMPLETE_ROUTE_FIELDS))
    observed_route.update(deepcopy(_COMPLETE_ROUTE_FIELDS))
    observed_route[field] = "changed"

    with pytest.raises(AdoptionError):
        assert_post_import_equivalent(before, observed)


@pytest.mark.parametrize("logical_id", tuple(ROUTES_BY_LOGICAL_ID))
@pytest.mark.parametrize(
    "field",
    (
        "RouteId",
        "RouteKey",
        "Target",
        "AuthorizationType",
        "AuthorizerId",
        *_COMPLETE_ROUTE_FIELDS,
    ),
)
def test_same_boundary_gates_reject_every_managed_route_field(
    logical_id,
    field,
):
    for gate, baseline, observed in _same_boundary_cases():
        route_key = ROUTES_BY_LOGICAL_ID[logical_id].route_key
        baseline_route = next(
            route
            for route in baseline["api"]["routes"]
            if route["RouteKey"] == route_key
        )
        observed_route = next(
            route
            for route in observed["api"]["routes"]
            if route["RouteKey"] == route_key
        )
        baseline_route.update(deepcopy(_COMPLETE_ROUTE_FIELDS))
        observed_route.update(deepcopy(_COMPLETE_ROUTE_FIELDS))
        observed_route[field] = "changed"
        with pytest.raises(AdoptionError):
            gate(baseline, observed)


def test_post_import_gate_ignores_unrelated_api_route():
    observed = post_import_snapshot()
    observed["api"]["routes"].append(
        {
            "RouteId": "member-b-route",
            "RouteKey": "GET /member-b",
            "Target": "integrations/member-b",
            "AuthorizationType": "NONE",
        }
    )

    assert_post_import_equivalent(valid_snapshot(), observed)


def test_failed_import_change_set_creation_requires_fresh_review_and_discard():
    classification = adoption.classify_recovery_state(
        None,
        set(),
        import_change_set_creation_failed=True,
    )

    assert classification == {
        "action": "re-audit-and-review",
        "empty_shell_cleanup_candidate": False,
        "deletion_requires_separate_approval": False,
        "discard_stale_artifacts": True,
    }


@pytest.mark.parametrize(
    ("status", "managed"),
    [
        ("IMPORT_IN_PROGRESS", set()),
        ("UPDATE_IN_PROGRESS", _EXPECTED_IMPORT_RESOURCES),
        ("CREATE_IN_PROGRESS", set()),
        ("UNKNOWN_FUTURE_STATE", _EXPECTED_IMPORT_RESOURCES),
    ],
)
def test_in_progress_and_unknown_recovery_states_never_proceed(status, managed):
    assert adoption.classify_recovery_state(status, set(managed))["action"] == "stop"


def test_failed_creation_flag_cannot_override_a_present_or_partial_target():
    classification = adoption.classify_recovery_state(
        "IMPORT_IN_PROGRESS",
        {"QueryFunction"},
        import_change_set_creation_failed=True,
    )

    assert classification["action"] == "stop"
    assert classification["discard_stale_artifacts"] is True


def _query_adoption_import_template():
    snapshot = valid_snapshot()
    template = build_import_template(
        snapshot,
        CodeArtifact(
            "private-artifacts",
            "backups/code.zip",
            "version-1",
        ),
    )
    template.pop("Outputs", None)
    template["Parameters"] = {
        name: {"Type": "String"}
        for name in _EXPECTED_IMPORT_PARAMETER_VALUES
    }
    template["Resources"] = {
        logical_id: resource
        for logical_id, resource in template["Resources"].items()
        if logical_id in _EXPECTED_IMPORT_RESOURCES
    }
    function = template["Resources"]["QueryFunction"]["Properties"]
    function["Role"] = {"Ref": "ExistingQueryLambdaRoleArn"}
    function.pop("Environment", None)
    integration = template["Resources"]["QueryIntegration"]["Properties"]
    integration["ApiId"] = {"Ref": "ExistingHttpApiId"}
    for logical_id in ROUTES_BY_LOGICAL_ID:
        route = template["Resources"][logical_id]["Properties"]
        route["ApiId"] = {"Ref": "ExistingHttpApiId"}
        if route["AuthorizationType"] == "JWT":
            route["AuthorizerId"] = {
                "Ref": "ExistingJwtAuthorizerId"
            }
    return template


def _query_adoption_import_parameters():
    return [
        {"ParameterKey": name, "ParameterValue": value}
        for name, value in _EXPECTED_IMPORT_PARAMETER_VALUES.items()
    ]


def _query_adoption_import_changes():
    return [
        {
            "ResourceChange": {
                "Action": "Import",
                "LogicalResourceId": item["LogicalResourceId"],
                "ResourceType": item["ResourceType"],
                "Replacement": "False",
            }
        }
        for item in build_resources_to_import(valid_snapshot())
    ]


def test_query_adoption_contract_has_exact_disjoint_stack_ownership():
    assert adoption.SOURCE_STACK_NAME == _EXPECTED_SOURCE_STACK
    assert adoption.TARGET_STACK_NAME == _EXPECTED_TARGET_STACK
    assert set(adoption.ORIGINAL_STACK_LOGICAL_IDS) == (
        _EXPECTED_ORIGINAL_STACK_RESOURCES
    )
    assert set(adoption.IMPORT_LOGICAL_IDS) == _EXPECTED_IMPORT_RESOURCES
    assert _EXPECTED_ORIGINAL_STACK_RESOURCES.isdisjoint(
        _EXPECTED_IMPORT_RESOURCES
    )


def test_query_adoption_contract_accepts_only_exact_source_and_target_names():
    adoption.validate_stack_names(
        _EXPECTED_SOURCE_STACK,
        _EXPECTED_TARGET_STACK,
    )


@pytest.mark.parametrize(
    ("source_stack", "target_stack"),
    [
        ("WrongSource", _EXPECTED_TARGET_STACK),
        (_EXPECTED_SOURCE_STACK, "WrongTarget"),
        (_EXPECTED_SOURCE_STACK, _EXPECTED_SOURCE_STACK),
    ],
    ids=("wrong-source", "wrong-target", "same-stack"),
)
def test_query_adoption_contract_rejects_wrong_or_equal_stack_names(
    source_stack,
    target_stack,
):
    with pytest.raises(AdoptionError, match="source|target|stack"):
        adoption.validate_stack_names(source_stack, target_stack)


def test_query_adoption_contract_builds_standalone_nineteen_resource_template():
    template = build_import_template(
        valid_snapshot(),
        CodeArtifact(
            "private-artifacts",
            "backups/code.zip",
            "version-1",
        ),
    )

    assert set(template["Resources"]) == _EXPECTED_IMPORT_RESOURCES
    assert {
        logical_id: resource["Type"]
        for logical_id, resource in template["Resources"].items()
    } == _EXPECTED_IMPORT_RESOURCE_TYPES
    assert template["Parameters"] == {
        name: {"Type": "String"}
        for name in _EXPECTED_IMPORT_PARAMETER_VALUES
    }
    assert "Outputs" not in template
    assert "InternalApiKey" not in str(template)
    assert {"Metadata", "Transform"}.isdisjoint(template)
    assert _EXPECTED_ORIGINAL_STACK_RESOURCES.isdisjoint(
        template["Resources"]
    )
    function = template["Resources"]["QueryFunction"]
    assert function["Properties"]["Role"] == {
        "Ref": "ExistingQueryLambdaRoleArn"
    }
    assert "Environment" not in function["Properties"]
    assert template["Resources"]["QueryIntegration"]["Properties"][
        "ApiId"
    ] == {"Ref": "ExistingHttpApiId"}
    routes = {
        logical_id: template["Resources"][logical_id]["Properties"]
        for logical_id in ROUTES_BY_LOGICAL_ID
    }
    assert all(
        route["ApiId"] == {"Ref": "ExistingHttpApiId"}
        for route in routes.values()
    )
    assert all(
        route["AuthorizerId"] == {
            "Ref": "ExistingJwtAuthorizerId"
        }
        for route in routes.values()
        if route["AuthorizationType"] == "JWT"
    )
    assert all(
        resource["DeletionPolicy"] == "Retain"
        and resource["UpdateReplacePolicy"] == "Retain"
        for resource in template["Resources"].values()
    )


def test_query_adoption_contract_binds_exact_three_audited_parameters():
    parameters = build_parameters_to_reuse(valid_snapshot())

    assert parameters == _query_adoption_import_parameters()
    assert all(set(item) == {"ParameterKey", "ParameterValue"} for item in parameters)
    assert "InternalApiKey" not in str(parameters)


def test_query_adoption_contract_manifest_has_exact_nineteen_resource_types():
    manifest = build_resources_to_import(valid_snapshot())

    assert len(manifest) == 19
    assert {
        item["LogicalResourceId"]: item["ResourceType"]
        for item in manifest
    } == _EXPECTED_IMPORT_RESOURCE_TYPES


def test_query_adoption_contract_accepts_exact_initial_import_artifacts():
    adoption.validate_initial_import_contract(
        _query_adoption_import_template(),
        build_resources_to_import(valid_snapshot()),
        _query_adoption_import_parameters(),
        valid_snapshot(),
    )


@pytest.mark.parametrize(
    "prohibited_logical_id",
    [
        "FilesTable",
        "SubscriptionsTable",
        "NotificationsTable",
        "QueryLambdaRole",
    ],
)
def test_query_adoption_contract_rejects_each_original_stack_resource_at_same_count(
    prohibited_logical_id,
):
    template = _query_adoption_import_template()
    replacement = template["Resources"].pop("ReservationsTable")
    template["Resources"][prohibited_logical_id] = replacement

    assert len(template["Resources"]) == 19

    with pytest.raises(
        AdoptionError,
        match=rf"original|prohibited|{prohibited_logical_id}",
    ):
        adoption.validate_initial_import_contract(
            template,
            build_resources_to_import(valid_snapshot()),
            _query_adoption_import_parameters(),
            valid_snapshot(),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda template: template.update(
            {"Outputs": {"Forbidden": {"Value": "not-permitted"}}}
        ),
        lambda template: template["Parameters"].update(
            {"InternalApiKey": {"Type": "String", "NoEcho": True}}
        ),
    ],
    ids=("output", "secret-parameter"),
)
def test_query_adoption_contract_rejects_prohibited_import_template_section(
    mutation,
):
    template = _query_adoption_import_template()
    mutation(template)

    with pytest.raises(
        AdoptionError,
        match="original|resource|Output|InternalApiKey|secret|parameter",
    ):
        adoption.validate_initial_import_contract(
            template,
            build_resources_to_import(valid_snapshot()),
            _query_adoption_import_parameters(),
            valid_snapshot(),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda template: template["Resources"]["QueryFunction"].update(
            {"Type": "AWS::SNS::Topic"}
        ),
        lambda template: template["Resources"].update(
            {
                "UnknownLogicalId": template["Resources"].pop(
                    "QueryFunction"
                )
            }
        ),
    ],
    ids=("wrong-resource-type", "unknown-logical-id"),
)
def test_query_adoption_contract_rejects_template_type_or_logical_id(mutation):
    template = _query_adoption_import_template()
    mutation(template)

    with pytest.raises(AdoptionError, match="type|logical|unknown|resource"):
        adoption.validate_initial_import_contract(
            template,
            build_resources_to_import(valid_snapshot()),
            _query_adoption_import_parameters(),
            valid_snapshot(),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest[0].update(
            {"ResourceType": "AWS::SNS::Topic"}
        ),
        lambda manifest: manifest[0].update(
            {"LogicalResourceId": "UnknownLogicalId"}
        ),
    ],
    ids=("wrong-resource-type", "unknown-logical-id"),
)
def test_query_adoption_contract_rejects_manifest_type_or_logical_id(mutation):
    manifest = build_resources_to_import(valid_snapshot())
    mutation(manifest)

    with pytest.raises(AdoptionError, match="type|logical|unknown|resource"):
        adoption.validate_initial_import_contract(
            _query_adoption_import_template(),
            manifest,
            _query_adoption_import_parameters(),
            valid_snapshot(),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda parameters: parameters.pop(),
        lambda parameters: parameters.append(
            {"ParameterKey": "UnexpectedParameter", "ParameterValue": "x"}
        ),
        lambda parameters: parameters[0].update(
            {"ParameterValue": "arn:aws:iam::999988887777:role/foreign"}
        ),
        lambda parameters: parameters[1].update(
            {"UsePreviousValue": True}
        ),
    ],
    ids=("missing", "extra", "value-mismatch", "use-previous"),
)
def test_query_adoption_contract_rejects_import_parameter_mismatch(mutation):
    parameters = _query_adoption_import_parameters()
    mutation(parameters)

    with pytest.raises(AdoptionError, match="parameter|audit|value"):
        adoption.validate_initial_import_contract(
            _query_adoption_import_template(),
            build_resources_to_import(valid_snapshot()),
            parameters,
            valid_snapshot(),
        )


@pytest.mark.parametrize(
    "owner",
    [
        _EXPECTED_SOURCE_STACK,
        _EXPECTED_TARGET_STACK,
        "ForeignStack",
    ],
)
def test_query_adoption_contract_rejects_any_existing_resource_owner(owner):
    owners = {logical_id: None for logical_id in _EXPECTED_IMPORT_RESOURCES}
    owners["QueryFunction"] = owner

    with pytest.raises(AdoptionError, match="owner|managed|QueryFunction"):
        adoption.validate_import_owners(owners)


def test_query_adoption_contract_accepts_exact_unmanaged_owner_set():
    adoption.validate_import_owners(
        {logical_id: None for logical_id in _EXPECTED_IMPORT_RESOURCES}
    )


@pytest.mark.parametrize("mutation", ["missing", "unexpected"])
def test_query_adoption_contract_requires_exact_nineteen_owner_keys(mutation):
    owners = {logical_id: None for logical_id in _EXPECTED_IMPORT_RESOURCES}
    if mutation == "missing":
        owners.pop("QueryFunction")
    else:
        owners["UnexpectedLogicalId"] = None

    with pytest.raises(AdoptionError, match="owner|logical|19|resource"):
        adoption.validate_import_owners(owners)


@pytest.mark.parametrize(
    "replacement_mode",
    ["explicit-false", "omitted"],
)
def test_query_adoption_contract_accepts_explicit_import_change_set_type(
    replacement_mode,
):
    changes = _query_adoption_import_changes()
    if replacement_mode == "omitted":
        changes[0]["ResourceChange"].pop("Replacement")

    adoption.validate_import_change_set(
        changes,
        build_resources_to_import(valid_snapshot()),
        change_set_type="IMPORT",
    )


@pytest.mark.parametrize(
    "change_set_type",
    ["CREATE", "UPDATE", "import", None, False],
    ids=("create", "update", "lowercase", "none", "boolean"),
)
def test_query_adoption_contract_rejects_invalid_import_change_set_type(
    change_set_type,
):
    with pytest.raises(AdoptionError, match="IMPORT|type"):
        adoption.validate_import_change_set(
            _query_adoption_import_changes(),
            build_resources_to_import(valid_snapshot()),
            change_set_type=change_set_type,
        )


def test_query_adoption_contract_rejects_missing_import_change_set_type():
    with pytest.raises(TypeError):
        adoption.validate_import_change_set(
            _query_adoption_import_changes(),
            build_resources_to_import(valid_snapshot()),
        )


@pytest.mark.parametrize("action", ["Add", "Modify", "Remove", "Dynamic"])
def test_query_adoption_contract_rejects_every_non_import_action(action):
    expected = build_resources_to_import(valid_snapshot())
    changes = _query_adoption_import_changes()
    changes[0]["ResourceChange"]["Action"] = action

    with pytest.raises(AdoptionError, match="19 Import|action"):
        adoption.validate_import_change_set(
            changes,
            expected,
            change_set_type="IMPORT",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ResourceType", "AWS::SNS::Topic"),
        ("LogicalResourceId", "UnknownLogicalId"),
    ],
    ids=("wrong-resource-type", "unknown-logical-id"),
)
def test_query_adoption_contract_rejects_change_set_resource_type_or_logical_id(
    field,
    value,
):
    expected = build_resources_to_import(valid_snapshot())
    changes = _query_adoption_import_changes()
    changes[0]["ResourceChange"][field] = value

    with pytest.raises(AdoptionError, match="type|logical|unknown|resource"):
        adoption.validate_import_change_set(
            changes,
            expected,
            change_set_type="IMPORT",
        )


@pytest.mark.parametrize(
    "replacement",
    [False, True, "True", "Conditional"],
    ids=("boolean-false", "boolean-true", "string-true", "conditional"),
)
def test_query_adoption_contract_rejects_invalid_import_replacement(
    replacement,
):
    expected = build_resources_to_import(valid_snapshot())
    changes = _query_adoption_import_changes()
    changes[0]["ResourceChange"]["Replacement"] = replacement

    with pytest.raises(AdoptionError, match="Replacement|False|replacement"):
        adoption.validate_import_change_set(
            changes,
            expected,
            change_set_type="IMPORT",
        )


def _route_scoped_lambda_policy(snapshot, *, removed_legacy_count):
    account = snapshot["caller"]["Account"]
    region = snapshot["region"]
    api_id = snapshot["api"]["id"]
    function_arn = (
        f"arn:aws:lambda:{region}:{account}:"
        "function:PacificBioArchive-QueryLambda"
    )
    statements = []
    contracts = (
        list(ROUTES_BY_LOGICAL_ID.values())
        + list(OPTIONS_ROUTES_BY_LOGICAL_ID.values())
    )
    for index, contract in enumerate(contracts, start=1):
        method, path = contract.route_key.split(" ", 1)
        path = re.sub(r"\{[^/{}]+\}", "*", path)
        statements.append({
            "Sid": f"RouteScopedPermission{index:02d}",
            "Effect": "Allow",
            "Principal": {"Service": "apigateway.amazonaws.com"},
            "Action": "lambda:InvokeFunction",
            "Resource": function_arn,
            "Condition": {
                "ArnLike": {
                    "AWS:SourceArn": (
                        f"arn:aws:execute-api:{region}:{account}:{api_id}"
                        f"/*/{method}{path}"
                    )
                }
            },
        })
    statements.extend(
        deepcopy(
            snapshot["function"]["resource_policy"]["Statement"][
                removed_legacy_count:
            ]
        )
    )
    return {"Version": "2012-10-17", "Statement": statements}


@pytest.mark.parametrize(
    ("removed_legacy_count", "next_sid"),
    [
        (0, "apigateway-query-lambda"),
        (1, "AllowAuthTestInvoke"),
        (2, "AllowApiGatewayInvokeAllRoutes-20260829030023"),
        (3, None),
    ],
)
def test_post_update_lambda_policy_requires_exact_scoped_and_remaining_history(
    removed_legacy_count,
    next_sid,
):
    snapshot = valid_snapshot()
    policy = _route_scoped_lambda_policy(
        snapshot,
        removed_legacy_count=removed_legacy_count,
    )

    assert validate_lambda_policy_after_update(
        policy,
        snapshot,
        removed_legacy_count=removed_legacy_count,
    ) == next_sid
    assert len(policy["Statement"]) == 29 - removed_legacy_count

    if removed_legacy_count == 3:
        historical_sources = {
            statement["Condition"]["ArnLike"]["AWS:SourceArn"]
            for statement in snapshot["function"]["resource_policy"]["Statement"]
        }
        live_sources = {
            statement["Condition"]["ArnLike"]["AWS:SourceArn"]
            for statement in policy["Statement"]
        }
        assert live_sources.isdisjoint(historical_sources - {
            "arn:aws:execute-api:ap-southeast-2:111122223333:"
            "2dd2aqb32j/*/GET/auth-test"
        })


def test_post_update_lambda_policy_rejects_missing_scoped_permission():
    snapshot = valid_snapshot()
    policy = _route_scoped_lambda_policy(snapshot, removed_legacy_count=3)
    policy["Statement"].pop(0)

    with pytest.raises(AdoptionError, match="exact route-scoped permissions"):
        validate_lambda_policy_after_update(
            policy,
            snapshot,
            removed_legacy_count=3,
        )


def test_post_update_lambda_policy_rejects_any_extra_permission():
    snapshot = valid_snapshot()
    policy = _route_scoped_lambda_policy(snapshot, removed_legacy_count=0)
    extra = deepcopy(policy["Statement"][0])
    extra["Sid"] = "UnexpectedExtraPermission"
    extra["Condition"]["ArnLike"]["AWS:SourceArn"] = (
        "arn:aws:execute-api:ap-southeast-2:111122223333:"
        "2dd2aqb32j/*/POST/unverified"
    )
    policy["Statement"].append(extra)

    with pytest.raises(AdoptionError, match="unapproved|exact|permission"):
        validate_lambda_policy_after_update(
            policy,
            snapshot,
            removed_legacy_count=0,
        )


def test_final_lambda_policy_rejects_a_restored_historical_wildcard():
    snapshot = valid_snapshot()
    policy = _route_scoped_lambda_policy(snapshot, removed_legacy_count=3)
    policy["Statement"].append(
        deepcopy(snapshot["function"]["resource_policy"]["Statement"][0])
    )

    with pytest.raises(AdoptionError, match="historical permission"):
        validate_lambda_policy_after_update(
            policy,
            snapshot,
            removed_legacy_count=3,
        )


def test_cleanup_stage_rejects_a_previously_removed_historical_permission():
    snapshot = valid_snapshot()
    policy = _route_scoped_lambda_policy(snapshot, removed_legacy_count=1)
    policy["Statement"].append(
        deepcopy(snapshot["function"]["resource_policy"]["Statement"][0])
    )

    with pytest.raises(AdoptionError, match="historical permission"):
        validate_lambda_policy_after_update(
            policy,
            snapshot,
            removed_legacy_count=1,
        )


def _reservation_arn():
    return (
        "arn:aws:dynamodb:ap-southeast-2:111122223333:"
        "table/PacificBioArchiveUploadReservations"
    )


def _query_policy(include_reservations=False):
    actions = [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:Scan",
        "dynamodb:Query",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
    ]
    resources = [
        "arn:aws:dynamodb:ap-southeast-2:111122223333:table/PacificBioArchiveFiles",
        "arn:aws:dynamodb:ap-southeast-2:111122223333:table/PacificBioArchiveSubscriptions",
        "arn:aws:dynamodb:ap-southeast-2:111122223333:table/PacificBioArchiveNotifications",
    ]
    if include_reservations:
        actions.append("dynamodb:TransactWriteItems")
        resources.append(_reservation_arn())
    return {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Action": actions, "Resource": resources}],
    }


def _upload_reservations_policy():
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:DeleteItem",
                "dynamodb:Query",
                "dynamodb:Scan",
                "dynamodb:TransactWriteItems",
            ],
            "Resource": _reservation_arn(),
        }],
    }


def approved_role_drift_snapshot():
    snapshot = valid_snapshot()
    baseline_role = {
        "Type": "AWS::IAM::Role",
        "Properties": {
            "RoleName": "PacificBioArchive-QueryLambdaRole",
            "AssumeRolePolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            },
            "ManagedPolicyArns": [
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            ],
            "Policies": [{
                "PolicyName": "DynamoDBFilesAccess",
                "PolicyDocument": _query_policy(),
            }],
        },
    }
    snapshot["stack"]["template"]["Resources"]["QueryLambdaRole"] = deepcopy(
        baseline_role
    )
    snapshot["role"]["processed_definition"] = deepcopy(baseline_role)
    snapshot["role"]["trust_policy"] = deepcopy(
        baseline_role["Properties"]["AssumeRolePolicyDocument"]
    )
    snapshot["role"]["managed_policies"] = [{
        "PolicyName": "AWSLambdaBasicExecutionRole",
        "PolicyArn": (
            "arn:aws:iam::aws:policy/service-role/"
            "AWSLambdaBasicExecutionRole"
        ),
    }]
    snapshot["role"]["inline_policies"] = {
        "DynamoDBFilesAccess": {
            "PolicyName": "DynamoDBFilesAccess",
            "PolicyDocument": _query_policy(include_reservations=True),
        },
        "UploadReservationsAccess": {
            "PolicyName": "UploadReservationsAccess",
            "PolicyDocument": _upload_reservations_policy(),
        },
    }
    snapshot["role"]["drift"] = {
        "status": "MODIFIED",
        "differences": [
            {
                "path": "/Policies/0/PolicyDocument",
                "type": "NOT_EQUAL",
                "expected": _query_policy(),
                "actual": _query_policy(include_reservations=True),
            },
            {
                "path": "/Policies/1",
                "type": "ADD",
                "expected": None,
                "actual": {
                    "PolicyName": "UploadReservationsAccess",
                    "PolicyDocument": _upload_reservations_policy(),
                },
            },
        ],
    }
    return snapshot


def test_valid_snapshot_accepts_exact_sixteen_member_d_routes():
    validate_snapshot(valid_snapshot())
    assert len(ROUTES_BY_LOGICAL_ID) == 16


def test_valid_snapshot_accepts_aws_default_unlimited_and_warm_throughput():
    snapshot = valid_snapshot()
    snapshot["reservations_table"]["OnDemandThroughput"] = {
        "MaxReadRequestUnits": -1,
        "MaxWriteRequestUnits": -1,
    }
    snapshot["reservations_table"]["WarmThroughput"] = {
        "ReadUnitsPerSecond": 12000,
        "WriteUnitsPerSecond": 4000,
        "Status": "ACTIVE",
    }

    validate_snapshot(snapshot)


def test_valid_snapshot_accepts_active_warm_throughput_above_aws_minimums():
    snapshot = valid_snapshot()
    snapshot["reservations_table"]["WarmThroughput"] = {
        "ReadUnitsPerSecond": 24000,
        "WriteUnitsPerSecond": 8000,
        "Status": "ACTIVE",
    }

    validate_snapshot(snapshot)


def test_valid_snapshot_accepts_only_disabled_kinesis_destinations():
    snapshot = valid_snapshot()
    snapshot["reservations_table"]["KinesisDataStreamDestinations"] = [
        {
            "StreamArn": (
                "arn:aws:kinesis:ap-southeast-2:111122223333:"
                "stream/retired-reservations-export"
            ),
            "DestinationStatus": "DISABLED",
        },
        {
            "StreamArn": (
                "arn:aws:kinesis:ap-southeast-2:111122223333:"
                "stream/another-retired-export"
            ),
            "DestinationStatus": "DISABLED",
        },
    ]

    validate_snapshot(snapshot)


@pytest.mark.parametrize(
    "status",
    ["ACTIVE", "ENABLING", "DISABLING", "ENABLE_FAILED", "UPDATING"],
)
def test_reservations_table_rejects_any_non_disabled_kinesis_destination(
    status,
):
    snapshot = valid_snapshot()
    snapshot["reservations_table"]["KinesisDataStreamDestinations"] = [{
        "StreamArn": (
            "arn:aws:kinesis:ap-southeast-2:111122223333:"
            "stream/reservations-export"
        ),
        "DestinationStatus": status,
    }]

    with pytest.raises(AdoptionError, match="ReservationsTable|Kinesis"):
        validate_snapshot(snapshot)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("VectorIndexes", [{"IndexName": "unexpected-vector-index"}]),
        (
            "GlobalTableWitnesses",
            [{"RegionName": "us-east-1"}],
        ),
    ],
)
def test_reservations_table_rejects_unmanaged_new_dynamodb_features(
    field,
    value,
):
    snapshot = valid_snapshot()
    snapshot["reservations_table"][field] = value

    with pytest.raises(AdoptionError, match="ReservationsTable"):
        validate_snapshot(snapshot)


def test_historical_gateway_policy_requires_a_nonempty_statement_sid():
    validate_snapshot(valid_snapshot())

    for invalid_sid in (None, "", "   "):
        snapshot = valid_snapshot()
        statement = snapshot["function"]["resource_policy"]["Statement"][0]
        if invalid_sid is None:
            statement.pop("Sid")
        else:
            statement["Sid"] = invalid_sid

        with pytest.raises(AdoptionError, match="resource policy|Sid"):
            validate_snapshot(snapshot)


def test_audit_accepts_verified_historical_permissions_in_any_statement_order():
    snapshot = valid_snapshot()
    snapshot["function"]["resource_policy"]["Statement"].reverse()

    validate_snapshot(snapshot)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda statement: statement.update({
            "Principal": {"Service": "events.amazonaws.com"}
        }),
        lambda statement: statement.update({"Action": "lambda:GetFunction"}),
        lambda statement: statement.update({
            "Resource": (
                "arn:aws:lambda:ap-southeast-2:999988887777:"
                "function:PacificBioArchive-QueryLambda"
            )
        }),
        lambda statement: statement["Condition"]["ArnLike"].update({
            "AWS:SourceArn": (
                "arn:aws:execute-api:ap-southeast-2:999988887777:"
                "2dd2aqb32j/*/*/*"
            )
        }),
        lambda statement: statement["Condition"]["ArnLike"].update({
            "AWS:SourceArn": (
                "arn:aws:execute-api:ap-southeast-2:111122223333:"
                "otherapi/*/*/*"
            )
        }),
        lambda statement: statement["Condition"]["ArnLike"].update({
            "AWS:SourceArn": (
                "arn:aws:execute-api:ap-southeast-2:111122223333:"
                "2dd2aqb32j/*/POST/unverified"
            )
        }),
        lambda statement: statement.update({"Sid": "UnknownHistoricalSid"}),
        lambda statement: statement.update({"UnknownField": "not-allowed"}),
    ],
)
def test_audit_rejects_any_unverified_historical_permission_change(mutation):
    snapshot = valid_snapshot()
    mutation(snapshot["function"]["resource_policy"]["Statement"][0])

    with pytest.raises(AdoptionError, match="resource policy"):
        validate_snapshot(snapshot)


def test_audit_rejects_an_extra_lambda_permission():
    snapshot = valid_snapshot()
    extra = deepcopy(snapshot["function"]["resource_policy"]["Statement"][0])
    extra["Sid"] = "UnexpectedExtraPermission"
    snapshot["function"]["resource_policy"]["Statement"].append(extra)

    with pytest.raises(AdoptionError, match="resource policy"):
        validate_snapshot(snapshot)


def test_root_caller_is_rejected():
    snapshot = valid_snapshot()
    snapshot["caller"]["Arn"] = "arn:aws:iam::111122223333:root"
    with pytest.raises(AdoptionError, match="Root"):
        validate_snapshot(snapshot)


@pytest.mark.parametrize("principal", [
    "arn:aws:sts::111122223333:federated-user/fit5225-cli-deployer",
    "arn:aws:sts::111122223333:assumed-role/team/fit5225-cli-deployer",
])
def test_non_iam_principal_cannot_impersonate_deployer(principal):
    snapshot = valid_snapshot()
    snapshot["caller"]["Arn"] = principal
    with pytest.raises(AdoptionError, match="exact IAM user"):
        validate_snapshot(snapshot)


@pytest.mark.parametrize("mutation", [
    lambda snapshot: snapshot["stack"]["managed"].update({"QueryLambdaRole": "wrong"}),
    lambda snapshot: snapshot["stack"]["template"]["Resources"]["QueryLambdaRole"]["Properties"].update({"RoleName": "wrong"}),
    lambda snapshot: snapshot["function"].update({"Role": "arn:aws:iam::999988887777:role/PacificBioArchive-QueryLambdaRole"}),
    lambda snapshot: snapshot["role"].update({"role_name": "wrong"}),
])
def test_role_identity_mismatch_fails_pure_validation(mutation):
    snapshot = valid_snapshot()
    mutation(snapshot)
    with pytest.raises(AdoptionError, match="role identity|function role"):
        validate_snapshot(snapshot)


def test_exact_known_reservation_role_drift_is_accepted():
    validate_snapshot(approved_role_drift_snapshot())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda snapshot: snapshot["role"]["drift"]["differences"][0][
            "actual"
        ]["Statement"][0]["Action"].append("dynamodb:CreateTable"),
        lambda snapshot: snapshot["role"]["drift"]["differences"][0][
            "actual"
        ]["Statement"][0]["Resource"].append("*"),
        lambda snapshot: snapshot["role"]["drift"]["differences"][1][
            "actual"
        ].update({"PolicyName": "UnexpectedPolicy"}),
        lambda snapshot: snapshot["role"]["inline_policies"][
            "UploadReservationsAccess"
        ]["PolicyDocument"]["Statement"][0]["Action"].append(
            "dynamodb:CreateTable"
        ),
        lambda snapshot: snapshot["role"]["drift"].update(
            {"differences": []}
        ),
    ],
)
def test_any_unapproved_role_drift_fails_closed(mutation):
    snapshot = approved_role_drift_snapshot()
    mutation(snapshot)

    with pytest.raises(AdoptionError, match="QueryLambdaRole|drift"):
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


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("Layers", ["arn:aws:lambda:ap-southeast-2:111122223333:layer:extra:1"], "layers"),
        (
            "FileSystemConfigs",
            [{"Arn": "arn:aws:elasticfilesystem:ap-southeast-2:111122223333:access-point/fsap-example", "LocalMountPath": "/mnt/data"}],
            "file system",
        ),
        (
            "VpcConfig",
            {
                "SubnetIds": ["subnet-12345678"],
                "SecurityGroupIds": ["sg-12345678"],
                "VpcId": "vpc-12345678",
                "Ipv6AllowedForDualStack": False,
            },
            "VPC",
        ),
    ],
)
def test_snapshot_rejects_nonempty_unmanaged_lambda_configuration(
    field, value, message
):
    snapshot = valid_snapshot()
    snapshot["function"][field] = value

    with pytest.raises(AdoptionError, match=message):
        validate_snapshot(snapshot)


def test_import_manifest_contains_reservations_table_lambda_integration_and_sixteen_routes():
    manifest = build_resources_to_import(valid_snapshot())
    assert len(manifest) == 19
    assert {item["LogicalResourceId"] for item in manifest} == {
        "ReservationsTable", "QueryFunction", "QueryIntegration", *ROUTES_BY_LOGICAL_ID,
    }
    assert {
        "ResourceType": "AWS::DynamoDB::Table",
        "LogicalResourceId": "ReservationsTable",
        "ResourceIdentifier": {"TableName": "PacificBioArchiveUploadReservations"},
    } in manifest
    assert all("OPTIONS" not in str(item) for item in manifest)
    assert all("upload-url" not in str(item) for item in manifest)
    assert all("asset-urls" not in str(item) for item in manifest)


def test_import_template_retains_every_imported_resource_without_secret_value():
    snapshot = valid_snapshot()
    audited_policy = deepcopy(snapshot["function"]["resource_policy"])
    template = build_import_template(snapshot, CodeArtifact("private-artifacts", "backups/code.zip", "version-1"))
    imported = {"ReservationsTable", "QueryFunction", "QueryIntegration", *ROUTES_BY_LOGICAL_ID}
    for logical_id in imported:
        assert template["Resources"][logical_id]["DeletionPolicy"] == "Retain"
        assert template["Resources"][logical_id]["UpdateReplacePolicy"] == "Retain"
    assert template["Parameters"] == {
        name: {"Type": "String"}
        for name in _EXPECTED_IMPORT_PARAMETER_VALUES
    }
    assert "InternalApiKey" not in str(template)
    rendered = str(template)
    assert "fixture-secret" not in rendered
    assert "POST /upload-url" not in rendered
    assert all(
        resource.get("Type") != "AWS::Lambda::Permission"
        for resource in template["Resources"].values()
    )
    assert snapshot["function"]["resource_policy"] == audited_policy


def test_import_template_describes_exact_live_reservations_table():
    template = build_import_template(
        valid_snapshot(),
        CodeArtifact("private-artifacts", "backups/code.zip", "version-1"),
    )

    table = template["Resources"]["ReservationsTable"]
    assert table == {
        "Type": "AWS::DynamoDB::Table",
        "DeletionPolicy": "Retain",
        "UpdateReplacePolicy": "Retain",
        "Properties": {
            "TableName": "PacificBioArchiveUploadReservations",
            "BillingMode": "PAY_PER_REQUEST",
            "AttributeDefinitions": [
                {"AttributeName": "reservation_key", "AttributeType": "S"},
            ],
            "KeySchema": [
                {"AttributeName": "reservation_key", "KeyType": "HASH"},
            ],
        },
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("TableStatus", "UPDATING"),
        ("BillingMode", "PROVISIONED"),
        ("KeySchema", [{"AttributeName": "wrong", "KeyType": "HASH"}]),
        ("GlobalSecondaryIndexes", [{"IndexName": "unexpected"}]),
        ("LocalSecondaryIndexes", [{"IndexName": "unexpected"}]),
        ("StreamSpecification", {"StreamEnabled": True}),
        ("DeletionProtectionEnabled", True),
        ("TableClass", "STANDARD_INFREQUENT_ACCESS"),
        ("Replicas", [{"RegionName": "us-east-1"}]),
        ("Tags", [{"Key": "unexpected", "Value": "tag"}]),
        ("TimeToLiveStatus", "ENABLED"),
        ("PointInTimeRecoveryStatus", "ENABLED"),
        ("SSEMode", "NON_DEFAULT"),
    ],
)
def test_reservations_table_import_rejects_unsupported_live_shape(field, value):
    snapshot = valid_snapshot()
    snapshot["reservations_table"][field] = value

    with pytest.raises(AdoptionError, match="ReservationsTable"):
        validate_snapshot(snapshot)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("TableName", "wrong"),
        (
            "TableArn",
            "arn:aws:dynamodb:us-east-1:111122223333:"
            "table/PacificBioArchiveUploadReservations",
        ),
        (
            "TableArn",
            "arn:aws:dynamodb:ap-southeast-2:999988887777:"
            "table/PacificBioArchiveUploadReservations",
        ),
        (
            "AttributeDefinitions",
            [{"AttributeName": "reservation_key", "AttributeType": "N"}],
        ),
    ],
)
def test_reservations_table_identity_must_match_exact_account_region_and_key(
    field, value
):
    snapshot = valid_snapshot()
    snapshot["reservations_table"][field] = value

    with pytest.raises(AdoptionError, match="ReservationsTable"):
        validate_snapshot(snapshot)


def test_import_template_keeps_exact_live_lambda_rollback_package():
    template = build_import_template(valid_snapshot(), CodeArtifact("private-artifacts", "backups/code.zip", "version-1"))
    function = template["Resources"]["QueryFunction"]
    assert function["Type"] == "AWS::Lambda::Function"
    assert function["Properties"]["FunctionName"] == "PacificBioArchive-QueryLambda"
    assert function["Properties"]["Code"] == {"S3Bucket": "private-artifacts", "S3Key": "backups/code.zip", "S3ObjectVersion": "version-1"}
    assert function["Properties"]["Role"] == {
        "Ref": "ExistingQueryLambdaRoleArn"
    }
    assert "Environment" not in function["Properties"]


def test_import_template_omits_unset_optional_lambda_properties_but_keeps_set_values():
    snapshot = valid_snapshot()
    template = build_import_template(snapshot, CodeArtifact("private-artifacts", "backups/code.zip", "version-1"))
    properties = template["Resources"]["QueryFunction"]["Properties"]
    assert {
        "KmsKeyArn",
        "CodeSigningConfigArn",
        "ReservedConcurrentExecutions",
        "Layers",
        "VpcConfig",
        "FileSystemConfigs",
    }.isdisjoint(properties)
    assert properties["RuntimeManagementConfig"] == {"UpdateRuntimeOn": "Auto"}


def test_import_template_preserves_explicit_optional_lambda_values_and_role_path():
    snapshot = valid_snapshot()
    snapshot["function"]["Role"] = "arn:aws:iam::111122223333:role/service/team/PacificBioArchive-QueryLambdaRole"
    snapshot["function"].update({
        "KmsKeyArn": "arn:aws:kms:ap-southeast-2:111122223333:key/example",
        "CodeSigningConfigArn": "arn:aws:lambda:ap-southeast-2:111122223333:code-signing-config:csc-example",
        "ReservedConcurrentExecutions": 0,
    })
    template = build_import_template(snapshot, CodeArtifact("private-artifacts", "backups/code.zip", "version-1"))
    properties = template["Resources"]["QueryFunction"]["Properties"]
    assert properties["KmsKeyArn"].endswith("key/example")
    assert properties["CodeSigningConfigArn"].endswith("csc-example")
    assert properties["ReservedConcurrentExecutions"] == 0


def test_import_parameters_bind_only_exact_audited_non_secret_values():
    parameters = build_parameters_to_reuse(valid_snapshot())
    assert parameters == _query_adoption_import_parameters()
    assert "InternalApiKey" not in str(parameters)
    assert "UsePreviousValue" not in str(parameters)


def test_import_template_discards_source_parameters_and_outputs():
    snapshot = valid_snapshot()
    snapshot["stack"]["parameters"] = []
    snapshot["stack"]["template"].pop("Parameters")
    snapshot["stack"]["template"]["Outputs"] = {
        "NotificationsTableName": {
            "Value": {"Ref": "NotificationsTable"}
        },
        "QueryLambdaRoleArn": {
            "Value": {"Fn::GetAtt": ["QueryLambdaRole", "Arn"]}
        },
        "SubscriptionsTableName": {
            "Value": {"Ref": "SubscriptionsTable"}
        },
        "TableName": {"Value": {"Ref": "FilesTable"}},
    }
    template = build_import_template(
        snapshot,
        CodeArtifact(
            "private-artifacts",
            "backups/code.zip",
            "version-1",
        ),
    )

    assert set(template) == {
        "AWSTemplateFormatVersion",
        "Description",
        "Parameters",
        "Resources",
    }
    assert template["Parameters"] == {
        name: {"Type": "String"}
        for name in _EXPECTED_IMPORT_PARAMETER_VALUES
    }
    assert "Outputs" not in template
    assert "Environment" not in template["Resources"]["QueryFunction"]["Properties"]
    assert "InternalApiKey" not in str(template)


@pytest.mark.parametrize("keep_empty_section", [False, True])
def test_source_parameter_shape_does_not_change_import_parameters(keep_empty_section):
    snapshot = valid_snapshot()
    snapshot["stack"]["parameters"] = []
    if keep_empty_section:
        snapshot["stack"]["template"]["Parameters"] = {}
    else:
        snapshot["stack"]["template"].pop("Parameters")

    template = build_import_template(
        snapshot,
        CodeArtifact("private-artifacts", "backups/code.zip", "version-1"),
    )

    assert template["Parameters"] == {
        name: {"Type": "String"}
        for name in _EXPECTED_IMPORT_PARAMETER_VALUES
    }


def test_import_omits_source_role_and_parameter_file_is_secret_free():
    snapshot = valid_snapshot()
    snapshot["stack"]["parameters"] = []
    snapshot["stack"]["template"].pop("Parameters")
    template = build_import_template(
        snapshot,
        CodeArtifact(
            "private-artifacts",
            "backups/code.zip",
            "version-1",
        ),
    )

    assert "QueryLambdaRole" not in template["Resources"]
    assert build_parameters_to_reuse(snapshot) == (
        _query_adoption_import_parameters()
    )


def test_source_snapshot_rejects_target_stack_resource_ownership():
    snapshot = valid_snapshot()
    snapshot["stack"]["managed"]["QueryFunction"] = (
        "PacificBioArchive-QueryLambda"
    )

    with pytest.raises(AdoptionError, match="source stack|original resources"):
        validate_snapshot(snapshot)


def test_runtime_baseline_accepts_unchanged_source_snapshot():
    before = valid_snapshot()
    before["stack"]["parameters"] = []
    before["stack"]["template"].pop("Parameters")
    after = deepcopy(before)
    assert_runtime_unchanged(before, after)


def test_post_import_baseline_rejects_parameter_registration_during_import():
    before = valid_snapshot()
    before["stack"]["parameters"] = []
    before["stack"]["template"].pop("Parameters")
    after = deepcopy(before)
    after["stack"]["parameters"] = ["InternalApiKey"]
    after["stack"]["template"]["Parameters"] = {
        "InternalApiKey": {
            "Type": "String",
            "NoEcho": True,
            "MinLength": 1,
        }
    }

    with pytest.raises(AdoptionError, match="parameter|runtime|import"):
        assert_runtime_unchanged(before, after)


@pytest.mark.parametrize(
    ("parameter_names", "parameter_definition"),
    [
        (["InternalApiKey"], None),
        ([], {"Type": "String", "NoEcho": True, "MinLength": 1}),
        (["InternalApiKey"], {"Type": "String", "NoEcho": False}),
    ],
    ids=("name-only", "template-only", "non-noecho-definition"),
)
def test_snapshot_ignores_internal_key_registration_state(
    parameter_names,
    parameter_definition,
):
    snapshot = valid_snapshot()
    snapshot["stack"]["parameters"] = parameter_names
    if parameter_definition is None:
        snapshot["stack"]["template"].pop("Parameters")
    else:
        snapshot["stack"]["template"]["Parameters"] = {
            "InternalApiKey": parameter_definition
        }

    validate_snapshot(snapshot)


def test_post_import_baseline_rejects_output_change_during_import():
    before = valid_snapshot()
    before["stack"]["template"]["Outputs"] = {
        "TableName": {"Value": {"Ref": "FilesTable"}}
    }
    after = deepcopy(before)
    after["stack"]["template"]["Outputs"]["Injected"] = {
        "Value": "must-not-change-during-import"
    }

    with pytest.raises(AdoptionError, match="runtime changed|template|import"):
        assert_runtime_unchanged(before, after)


def test_source_snapshot_rejects_any_adopted_resource_identity():
    snapshot = valid_snapshot()
    snapshot["stack"]["managed"].update({
        "ReservationsTable": "wrong-table",
        "QueryFunction": "PacificBioArchive-QueryLambda",
        "QueryIntegration": "fbjojun",
    })
    for logical_id, route in zip(ROUTES_BY_LOGICAL_ID, snapshot["api"]["routes"]):
        snapshot["stack"]["managed"][logical_id] = route["RouteId"]

    with pytest.raises(AdoptionError, match="source stack|original resources"):
        validate_snapshot(snapshot)


def test_post_import_runtime_comparison_rejects_reservations_table_change():
    before = valid_snapshot()
    after = deepcopy(before)
    after["reservations_table"]["BillingMode"] = "PROVISIONED"

    with pytest.raises(AdoptionError, match="runtime changed|ReservationsTable"):
        assert_runtime_unchanged(before, after)


def test_post_import_runtime_comparison_rejects_hidden_environment_rotation():
    before = valid_snapshot()
    after = deepcopy(before)
    after["function"]["RevisionId"] = "rotated-function-revision"

    with pytest.raises(AdoptionError, match="runtime changed|function"):
        assert_runtime_unchanged(before, after)


def test_post_import_runtime_comparison_rejects_role_drift_toctou_change():
    before = approved_role_drift_snapshot()
    after = deepcopy(before)
    after["role"]["drift"] = {"status": "IN_SYNC", "differences": []}
    after["role"]["inline_policies"] = {
        "DynamoDBFilesAccess": deepcopy(
            after["role"]["inline_policies"]["DynamoDBFilesAccess"]
        )
    }

    with pytest.raises(AdoptionError, match="runtime changed"):
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


@pytest.mark.parametrize("action", ["Add", "Remove", "Import"])
def test_update_rejects_non_modify_action_for_protected_resource(action):
    processed = _update_processed(_maintained_role_target())
    changes = [{"ResourceChange": {"Action": action, "LogicalResourceId": "QueryFunction", "ResourceType": "AWS::Lambda::Function", "Replacement": "False"}}]
    with pytest.raises(AdoptionError, match="forbidden|contract"):
        validate_update_change_set(changes, processed)


def test_resource_policy_requires_exact_supported_mapping():
    snapshot = valid_snapshot()
    snapshot["function"]["resource_policy"]["Statement"][0]["Resource"] = "wrong"
    with pytest.raises(AdoptionError, match="resource policy"):
        validate_snapshot(snapshot)


_MAINTAINED_IMPORTED_ROUTES = {
    "AuthTestRoute": ("GET /auth-test", "JWT"),
    "QueryByTagsRoute": ("POST /query/by-tags", "JWT"),
    "QueryBySpeciesRoute": ("POST /query/by-species", "JWT"),
    "QueryByThumbnailRoute": ("GET /query/by-thumbnail", "JWT"),
    "QueryByFileRoute": ("POST /query/by-file", "JWT"),
    "EditTagsRoute": ("POST /tags/edit", "JWT"),
    "DeleteFilesRoute": ("POST /files/delete", "JWT"),
    "SubscribeRoute": ("POST /notifications/subscribe", "JWT"),
    "UnsubscribeRoute": ("DELETE /notifications/subscribe", "JWT"),
    "SubscriptionsRoute": ("GET /notifications/subscriptions", "JWT"),
    "NotificationsRoute": ("GET /notifications", "JWT"),
    "ReserveUploadRoute": ("POST /internal/uploads/reserve", "NONE"),
    "AcquireProcessingRoute": (
        "POST /internal/files/{file_id}/processing",
        "NONE",
    ),
    "CompleteFileRoute": ("PUT /internal/files/{file_id}/complete", "NONE"),
    "FailFileRoute": ("PUT /internal/files/{file_id}/failed", "NONE"),
    "AuthorizeAssetsRoute": ("POST /internal/assets/authorize", "NONE"),
}

_MAINTAINED_OPTIONS_ROUTES = {
    "AuthTestOptionsRoute": "OPTIONS /auth-test",
    "QueryByTagsOptionsRoute": "OPTIONS /query/by-tags",
    "QueryBySpeciesOptionsRoute": "OPTIONS /query/by-species",
    "QueryByThumbnailOptionsRoute": "OPTIONS /query/by-thumbnail",
    "QueryByFileOptionsRoute": "OPTIONS /query/by-file",
    "EditTagsOptionsRoute": "OPTIONS /tags/edit",
    "DeleteFilesOptionsRoute": "OPTIONS /files/delete",
    "SubscribeOptionsRoute": "OPTIONS /notifications/subscribe",
    "SubscriptionsOptionsRoute": "OPTIONS /notifications/subscriptions",
    "NotificationsOptionsRoute": "OPTIONS /notifications",
}


def _maintained_route_target(route_key, authorization_type, *, retain):
    properties = {
        "ApiId": {"Ref": "ExistingHttpApiId"},
        "RouteKey": route_key,
        "AuthorizationType": authorization_type,
        "Target": {"Fn::Sub": "integrations/${QueryIntegration}"},
    }
    if authorization_type == "JWT":
        properties["AuthorizerId"] = {"Ref": "ExistingJwtAuthorizerId"}
    resource = {
        "Type": "AWS::ApiGatewayV2::Route",
        "Properties": properties,
    }
    if retain:
        resource.update(
            {"DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain"}
        )
    return resource


def _maintained_permission_target(route_key):
    method, path = route_key.split(" ", 1)
    source_path = path.replace("{file_id}", "*")
    return {
        "Type": "AWS::Lambda::Permission",
        "Properties": {
            "Action": "lambda:InvokeFunction",
            "FunctionName": {"Ref": "QueryFunction"},
            "Principal": "apigateway.amazonaws.com",
            "SourceArn": {
                "Fn::Sub": (
                    "arn:${AWS::Partition}:execute-api:${AWS::Region}:"
                    "${AWS::AccountId}:${ExistingHttpApiId}/*/"
                    f"{method}{source_path}"
                )
            },
        },
    }


def _permission_logical_id(route_logical_id):
    return f"{route_logical_id.removesuffix('Route')}Permission"


def _maintained_base_table_target(table_name, key_schema):
    return {
        "Type": "AWS::DynamoDB::Table",
        "DeletionPolicy": "Retain",
        "UpdateReplacePolicy": "Retain",
        "Properties": {
            "TableName": table_name,
            "BillingMode": "PAY_PER_REQUEST",
            "AttributeDefinitions": [
                {
                    "AttributeName": item["AttributeName"],
                    "AttributeType": "S",
                }
                for item in key_schema
            ],
            "KeySchema": deepcopy(key_schema),
        },
    }


def _update_processed(role, *, include_additions=True):
    resources = {
        "FilesTable": _maintained_base_table_target(
            "PacificBioArchiveFiles",
            [{"AttributeName": "file_id", "KeyType": "HASH"}],
        ),
        "SubscriptionsTable": _maintained_base_table_target(
            "PacificBioArchiveSubscriptions",
            [
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "species", "KeyType": "RANGE"},
            ],
        ),
        "NotificationsTable": _maintained_base_table_target(
            "PacificBioArchiveNotifications",
            [
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "notification_id", "KeyType": "RANGE"},
            ],
        ),
        "QueryLambdaRole": role,
        "ReservationsTable": _maintained_reservations_table_target(),
        "QueryFunction": {
            "Type": "AWS::Lambda::Function",
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
            "Properties": {
                "FunctionName": "PacificBioArchive-QueryLambda",
                "Role": {"Fn::GetAtt": ["QueryLambdaRole", "Arn"]},
                "Handler": "lambda_function.handler",
                "Runtime": "python3.12",
                "Timeout": 30,
                "MemorySize": 1024,
                "Code": {
                    "S3Bucket": "aws-sam-cli-managed-default-samclisourcebucket",
                    "S3Key": "member-d/query-function.zip",
                },
                "Environment": {
                    "Variables": {
                        "REPO_BACKEND": "dynamodb",
                        "DYNAMODB_TABLE": {"Ref": "FilesTable"},
                        "RESERVATIONS_TABLE": {"Ref": "ReservationsTable"},
                        "SUBSCRIPTIONS_TABLE": {"Ref": "SubscriptionsTable"},
                        "NOTIFICATIONS_TABLE": {"Ref": "NotificationsTable"},
                        "STORAGE_BACKEND": "lambda",
                        "STORAGE_DELETE_FUNCTION_NAME": {
                            "Ref": "StorageDeleteFunctionName"
                        },
                        "TAG_DETECTOR_BACKEND": "remote",
                        "QUERY_INPUT_BUCKET": {"Ref": "QueryInputBucketName"},
                        "INFERENCE_API_URL": {"Ref": "InferenceApiBaseUrl"},
                        "INTERNAL_API_KEY": {"Ref": "InternalApiKey"},
                        "ALLOW_LEGACY_PROCESSING_CALLBACKS": {
                            "Ref": "AllowLegacyProcessingCallbacks"
                        },
                        "NOTIFICATION_PUBLISHER": "sns",
                        "SNS_TOPIC_ARN": {"Ref": "NotificationTopic"},
                        "CORS_ORIGINS": {
                            "Fn::Join": [
                                ",",
                                [
                                    {"Ref": "AllowedOrigin"},
                                    {"Ref": "PublicAllowedOrigin"},
                                ],
                            ]
                        },
                    }
                },
                "Tags": [{"Key": "lambda:createdBy", "Value": "SAM"}],
            },
        },
        "QueryIntegration": {
            "Type": "AWS::ApiGatewayV2::Integration",
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
            "Properties": {
                "ApiId": {"Ref": "ExistingHttpApiId"},
                "IntegrationType": "AWS_PROXY",
                "IntegrationMethod": "POST",
                "IntegrationUri": {
                    "Fn::Sub": (
                        "arn:${AWS::Partition}:apigateway:${AWS::Region}:"
                        "lambda:path/2015-03-31/functions/"
                        "${QueryFunction.Arn}/invocations"
                    )
                },
                "PayloadFormatVersion": "2.0",
            },
        },
    }
    resources.update(
        {
            logical_id: _maintained_route_target(
                route_key,
                authorization_type,
                retain=True,
            )
            for logical_id, (
                route_key,
                authorization_type,
            ) in _MAINTAINED_IMPORTED_ROUTES.items()
        }
    )
    if include_additions:
        resources.update(
            {
                "NotificationTopic": {"Type": "AWS::SNS::Topic"},
                "NotificationEmailSubscription": {
                    "Type": "AWS::SNS::Subscription",
                    "Condition": "HasNotificationEmailEndpoint",
                    "Properties": {
                        "Protocol": "email",
                        "Endpoint": {"Ref": "NotificationEmailEndpoint"},
                        "TopicArn": {"Ref": "NotificationTopic"},
                    },
                },
            }
        )
        resources.update(
            {
                logical_id: _maintained_route_target(
                    route_key,
                    "NONE",
                    retain=False,
                )
                for logical_id, route_key in _MAINTAINED_OPTIONS_ROUTES.items()
            }
        )
        for logical_id, (route_key, _authorization_type) in (
            _MAINTAINED_IMPORTED_ROUTES.items()
        ):
            resources[_permission_logical_id(logical_id)] = (
                _maintained_permission_target(route_key)
            )
        for logical_id, route_key in _MAINTAINED_OPTIONS_ROUTES.items():
            resources[_permission_logical_id(logical_id)] = (
                _maintained_permission_target(route_key)
            )
    for logical_id in {
        "FilesTable",
        "SubscriptionsTable",
        "NotificationsTable",
        "QueryLambdaRole",
        "NotificationTopic",
        "NotificationEmailSubscription",
    }:
        resources.pop(logical_id, None)
    function = resources["QueryFunction"]["Properties"]
    function["Role"] = {"Ref": "ExistingQueryLambdaRoleArn"}
    variables = function["Environment"]["Variables"]
    variables["DYNAMODB_TABLE"] = {"Ref": "ExistingFilesTableName"}
    variables["SUBSCRIPTIONS_TABLE"] = {
        "Ref": "ExistingSubscriptionsTableName"
    }
    variables["NOTIFICATIONS_TABLE"] = {
        "Ref": "ExistingNotificationsTableName"
    }
    variables.pop("NOTIFICATION_PUBLISHER", None)
    variables.pop("SNS_TOPIC_ARN", None)
    for resource in resources.values():
        if resource["Type"] == "AWS::ApiGatewayV2::Route":
            resource["Properties"]["Target"] = {
                "Fn::Join": [
                    "",
                    ["integrations/", {"Ref": "QueryIntegration"}],
                ]
            }
    return {"Resources": resources}


def _maintained_reservations_table_target():
    return {
        "Type": "AWS::DynamoDB::Table",
        "DeletionPolicy": "Retain",
        "UpdateReplacePolicy": "Retain",
        "Properties": {
            "TableName": "PacificBioArchiveUploadReservations",
            "BillingMode": "PAY_PER_REQUEST",
            "AttributeDefinitions": [
                {"AttributeName": "reservation_key", "AttributeType": "S"},
            ],
            "KeySchema": [
                {"AttributeName": "reservation_key", "KeyType": "HASH"},
            ],
        },
    }


def _maintained_role_target():
    return {
        "Type": "AWS::IAM::Role",
        "DeletionPolicy": "Retain",
        "UpdateReplacePolicy": "Retain",
        "Properties": {
            "RoleName": "PacificBioArchive-QueryLambdaRole",
            "AssumeRolePolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            },
            "ManagedPolicyArns": [
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            ],
            "Policies": [{
                "PolicyName": "DynamoDBFilesAccess",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "dynamodb:PutItem",
                                "dynamodb:GetItem",
                                "dynamodb:Scan",
                                "dynamodb:Query",
                                "dynamodb:UpdateItem",
                                "dynamodb:DeleteItem",
                                "dynamodb:TransactWriteItems",
                            ],
                            "Resource": [
                                {"Fn::GetAtt": ["FilesTable", "Arn"]},
                                {"Fn::GetAtt": ["ReservationsTable", "Arn"]},
                                {"Fn::GetAtt": ["SubscriptionsTable", "Arn"]},
                                {"Fn::GetAtt": ["NotificationsTable", "Arn"]},
                            ],
                        },
                        {
                            "Effect": "Allow",
                            "Action": [
                                "s3:PutObject",
                                "s3:GetObject",
                                "s3:DeleteObject",
                            ],
                            "Resource": {
                                "Fn::Sub": (
                                    "arn:${AWS::Partition}:s3:::"
                                    "${QueryInputBucketName}/query-inputs/*"
                                )
                            },
                        },
                        {
                            "Effect": "Allow",
                            "Action": "lambda:InvokeFunction",
                            "Resource": {
                                "Fn::Sub": (
                                    "arn:${AWS::Partition}:lambda:${AWS::Region}:"
                                    "${AWS::AccountId}:function:"
                                    "${StorageDeleteFunctionName}"
                                )
                            },
                        },
                        {
                            "Effect": "Allow",
                            "Action": "sns:Publish",
                            "Resource": {"Ref": "NotificationTopic"},
                        },
                    ],
                },
            }],
        },
    }


def test_update_disables_database_owned_role_reconciliation():
    audited = approved_role_drift_snapshot()["role"]
    processed = _update_processed(_maintained_role_target())
    changes = [{
        "ResourceChange": {
            "Action": "Modify",
            "LogicalResourceId": "QueryLambdaRole",
            "ResourceType": "AWS::IAM::Role",
            "Replacement": "False",
        }
    }]

    with pytest.raises(AdoptionError, match="database-owned|reconcile"):
        validate_update_change_set(changes, processed, audited)


def test_update_with_known_role_drift_requires_explicit_in_place_role_modify():
    audited = approved_role_drift_snapshot()["role"]
    processed = _update_processed(_maintained_role_target())

    with pytest.raises(AdoptionError, match="QueryLambdaRole"):
        validate_update_change_set([], processed, audited)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda role: role["Properties"]["Policies"][0]["PolicyDocument"][
            "Statement"
        ][0]["Action"].append("dynamodb:CreateTable"),
        lambda role: role["Properties"]["Policies"][0]["PolicyDocument"][
            "Statement"
        ][0]["Resource"].append({"Fn::GetAtt": ["WrongTable", "Arn"]}),
        lambda role: role["Properties"].update(
            {"PermissionsBoundary": "arn:aws:iam::111122223333:policy/wrong"}
        ),
    ],
)
def test_update_rejects_any_role_target_outside_maintained_contract(mutation):
    audited = approved_role_drift_snapshot()["role"]
    role = _maintained_role_target()
    mutation(role)
    changes = [{
        "ResourceChange": {
            "Action": "Modify",
            "LogicalResourceId": "QueryLambdaRole",
            "ResourceType": "AWS::IAM::Role",
            "Replacement": "False",
        }
    }]

    with pytest.raises(AdoptionError, match="QueryLambdaRole"):
        validate_update_change_set(changes, _update_processed(role), audited)


def test_update_rejects_role_reconciliation_if_non_policy_baseline_differs():
    audited = approved_role_drift_snapshot()["role"]
    audited["processed_definition"]["Properties"][
        "AssumeRolePolicyDocument"
    ]["Statement"][0]["Principal"] = {"Service": "ec2.amazonaws.com"}
    changes = [{
        "ResourceChange": {
            "Action": "Modify",
            "LogicalResourceId": "QueryLambdaRole",
            "ResourceType": "AWS::IAM::Role",
            "Replacement": "False",
        }
    }]

    with pytest.raises(AdoptionError, match="QueryLambdaRole"):
        validate_update_change_set(
            changes,
            _update_processed(_maintained_role_target()),
            audited,
        )


def test_update_rejects_tampered_audited_role_drift_evidence():
    audited = approved_role_drift_snapshot()["role"]
    audited["drift"]["differences"][0]["actual"]["Statement"][0][
        "Action"
    ].append("dynamodb:CreateTable")
    changes = [{
        "ResourceChange": {
            "Action": "Modify",
            "LogicalResourceId": "QueryLambdaRole",
            "ResourceType": "AWS::IAM::Role",
            "Replacement": "False",
        }
    }]

    with pytest.raises(AdoptionError, match="QueryLambdaRole|drift"):
        validate_update_change_set(
            changes,
            _update_processed(_maintained_role_target()),
            audited,
        )


def test_update_protects_imported_reservations_table_from_replacement():
    processed = _update_processed(_maintained_role_target())
    changes = [{
        "ResourceChange": {
            "Action": "Modify",
            "LogicalResourceId": "ReservationsTable",
            "ResourceType": "AWS::DynamoDB::Table",
            "Replacement": "True",
        }
    }]

    with pytest.raises(AdoptionError, match="exact|ReservationsTable"):
        validate_update_change_set(changes, processed)


def test_update_rejects_removing_a_base_managed_table():
    processed = _update_processed(_maintained_role_target())
    changes = [{
        "ResourceChange": {
            "Action": "Remove",
            "LogicalResourceId": "FilesTable",
            "ResourceType": "AWS::DynamoDB::Table",
        }
    }]

    with pytest.raises(AdoptionError, match="exact|FilesTable|Remove|removal"):
        validate_update_change_set(changes, processed)


def test_update_rejects_adding_an_unmaintained_backdoor_role():
    processed = _update_processed(_maintained_role_target())
    processed["Resources"]["BackdoorRole"] = {
        "Type": "AWS::IAM::Role",
        "Properties": {
            "AssumeRolePolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"AWS": "*"},
                    "Action": "sts:AssumeRole",
                }],
            },
            "ManagedPolicyArns": [
                "arn:aws:iam::aws:policy/AdministratorAccess"
            ],
        },
    }
    changes = [{
        "ResourceChange": {
            "Action": "Add",
            "LogicalResourceId": "BackdoorRole",
            "ResourceType": "AWS::IAM::Role",
        }
    }]

    with pytest.raises(
        AdoptionError,
        match="BackdoorRole|unmaintained|Add|resource set|unexpected",
    ):
        validate_update_change_set(changes, processed)


@pytest.mark.parametrize(
    ("mutation", "resource_type"),
    [
        (
            lambda route: route["Properties"].update(
                {"AuthorizationType": "NONE"}
            ),
            "AWS::ApiGatewayV2::Route",
        ),
        (
            lambda route: route["Properties"].update(
                {"Target": {"Fn::Sub": "integrations/${BackdoorIntegration}"}}
            ),
            "AWS::ApiGatewayV2::Route",
        ),
        (
            lambda route: route.update({"Type": "AWS::IAM::Role"}),
            "AWS::IAM::Role",
        ),
    ],
    ids=("authorization", "target", "type"),
)
def test_update_rejects_tampering_with_an_imported_route(
    mutation,
    resource_type,
):
    processed = _update_processed(_maintained_role_target())
    mutation(processed["Resources"]["AuthTestRoute"])
    changes = [{
        "ResourceChange": {
            "Action": "Modify",
            "LogicalResourceId": "AuthTestRoute",
            "ResourceType": resource_type,
            "Replacement": "False",
        }
    }]

    with pytest.raises(AdoptionError, match="AuthTestRoute|route|contract"):
        validate_update_change_set(changes, processed)


def test_update_rejects_tampered_lambda_permission_source_arn():
    processed = _update_processed(_maintained_role_target())
    permission = _maintained_permission_target("GET /auth-test")
    permission["Properties"]["SourceArn"] = {
        "Fn::Sub": "arn:${AWS::Partition}:execute-api:*:*:*/*/*/*"
    }
    processed["Resources"]["AuthTestPermission"] = permission
    changes = [{
        "ResourceChange": {
            "Action": "Add",
            "LogicalResourceId": "AuthTestPermission",
            "ResourceType": "AWS::Lambda::Permission",
        }
    }]

    with pytest.raises(
        AdoptionError,
        match="AuthTestPermission|SourceArn|permission",
    ):
        validate_update_change_set(changes, processed)


def test_update_rejects_change_set_resource_type_mismatch():
    processed = _update_processed(_maintained_role_target())
    changes = _first_query_update_changes()
    next(
        change
        for change in changes
        if change["ResourceChange"]["LogicalResourceId"]
        == "AuthTestPermission"
    )["ResourceChange"]["ResourceType"] = "AWS::ApiGatewayV2::Route"

    with pytest.raises(AdoptionError, match="resource type mismatch"):
        validate_update_change_set(changes, processed)


def test_update_accepts_maintained_processed_template_and_allowed_changes():
    validate_update_change_set(
        _first_query_update_changes(),
        _first_query_update_processed(),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda table: table["Properties"].update({"TableName": "wrong"}),
        lambda table: table["Properties"].update({"BillingMode": "PROVISIONED"}),
        lambda table: table["Properties"]["KeySchema"][0].update(
            {"AttributeName": "wrong"}
        ),
        lambda table: table["Properties"].update(
            {"DeletionProtectionEnabled": True}
        ),
        lambda table: table.update({"DeletionPolicy": "Delete"}),
        lambda table: table.update({"Metadata": {"unexpected": True}}),
    ],
)
def test_update_requires_exact_maintained_reservations_table_contract(mutation):
    processed = _update_processed({"Type": "AWS::IAM::Role"})
    mutation(processed["Resources"]["ReservationsTable"])

    with pytest.raises(AdoptionError, match="ReservationsTable"):
        validate_update_change_set([], processed)


def test_role_modify_is_rejected_even_for_retain_only_metadata():
    baseline = _maintained_role_target()
    baseline.pop("DeletionPolicy")
    baseline.pop("UpdateReplacePolicy")
    audited = {"processed_definition": baseline}
    processed = _update_processed(_maintained_role_target())
    changes = [{"ResourceChange": {"Action": "Modify", "LogicalResourceId": "QueryLambdaRole", "ResourceType": "AWS::IAM::Role", "Replacement": "False"}}]
    with pytest.raises(AdoptionError, match="database-owned|reconcile"):
        validate_update_change_set(changes, processed, audited)


def _query_function_only_change():
    return [{
        "ResourceChange": {
            "Action": "Modify",
            "LogicalResourceId": "QueryFunction",
            "ResourceType": "AWS::Lambda::Function",
            "Replacement": "False",
        }
    }]


def test_first_update_rejects_lone_query_function_modify():
    with pytest.raises(AdoptionError, match="exact 37-action"):
        validate_update_change_set(
            _query_function_only_change(),
            _update_processed(_maintained_role_target()),
        )


def test_update_validator_has_no_alternate_action_set_parameter():
    assert set(signature(validate_update_change_set).parameters) == {
        "changes",
        "processed",
        "audited_role",
    }


def _hardening_function_configs():
    before = {
        "FunctionName": "PacificBioArchive-QueryLambda",
        "Runtime": "python3.12",
        "Handler": "lambda_function.handler",
        "MemorySize": 1024,
        "Timeout": 30,
        "CodeSha256": "before-code-digest",
        "Environment": {
            "Variables": {
                "REPO_BACKEND": "dynamodb",
                "ALLOW_LEGACY_PROCESSING_CALLBACKS": "true",
            }
        },
    }
    after = deepcopy(before)
    after["Environment"]["Variables"][
        "ALLOW_LEGACY_PROCESSING_CALLBACKS"
    ] = "false"
    return before, after


def test_hardening_transition_accepts_only_true_to_false_callback_change():
    import adoption

    validator = getattr(adoption, "validate_hardening_function_transition")
    before, after = _hardening_function_configs()

    validator(before, after, expected_callback="false")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda before, after: after.update({"Timeout": 31}),
        lambda before, after: after["Environment"]["Variables"].update(
            {"REPO_BACKEND": "attacker"}
        ),
        lambda before, after: after["Environment"]["Variables"].update(
            {"EXTRA_VARIABLE": "unexpected"}
        ),
        lambda before, after: after["Environment"]["Variables"].update(
            {"ALLOW_LEGACY_PROCESSING_CALLBACKS": "true"}
        ),
    ],
    ids=("timeout", "other-environment", "extra-environment", "callback-unchanged"),
)
def test_hardening_transition_rejects_any_other_or_missing_change(mutation):
    import adoption

    validator = getattr(adoption, "validate_hardening_function_transition")
    before, after = _hardening_function_configs()
    mutation(before, after)

    with pytest.raises(AdoptionError, match="hardening|callback|only|function"):
        validator(before, after, expected_callback="false")


def test_hardening_transition_requires_expected_callback_false():
    import adoption

    validator = getattr(adoption, "validate_hardening_function_transition")
    before, after = _hardening_function_configs()

    with pytest.raises(AdoptionError, match="callback|false|hardening"):
        validator(before, after, expected_callback="true")


@pytest.mark.parametrize("role", [
    {"Type": "AWS::IAM::Role", "Properties": {"Path": "/changed", "Tags": []}, "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain"},
    {"Type": "AWS::IAM::Role", "Properties": {"Path": "/", "Tags": []}, "DeletionPolicy": "Delete", "UpdateReplacePolicy": "Retain"},
    {"Type": "AWS::IAM::Role", "Properties": {"Path": "/", "Tags": []}, "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Metadata": {"unexpected": True}},
])
def test_role_modify_rejects_any_non_retain_or_other_difference(role):
    audited = {"processed_definition": {"Type": "AWS::IAM::Role", "Properties": {"Path": "/", "Tags": []}}}
    changes = [{"ResourceChange": {"Action": "Modify", "LogicalResourceId": "QueryLambdaRole", "Replacement": "False"}}]
    with pytest.raises(AdoptionError, match="QueryLambdaRole"):
        validate_update_change_set(changes, _update_processed(role), audited)


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


def _first_query_update_processed():
    return _update_processed(
        _maintained_role_target(),
        include_additions=True,
    )


def _first_query_update_changes():
    processed = _first_query_update_processed()
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
    changes.extend(
        {
            "ResourceChange": {
                "Action": "Add",
                "LogicalResourceId": logical_id,
                "ResourceType": resource["Type"],
                "Replacement": "False",
            }
        }
        for logical_id, resource in processed["Resources"].items()
        if logical_id in _MAINTAINED_OPTIONS_ROUTES
        or logical_id.endswith("Permission")
    )
    return changes


def test_first_query_update_accepts_exact_query_function_and_36_additions():
    changes = _first_query_update_changes()

    assert len(changes) == 37
    validate_update_change_set(changes, _first_query_update_processed())


@pytest.mark.parametrize(
    "replacement",
    [None, False, True, 0, 1, "false", "True", "Conditional"],
    ids=(
        "null",
        "boolean-false",
        "boolean-true",
        "zero",
        "one",
        "lowercase",
        "true-string",
        "conditional",
    ),
)
def test_first_query_update_modify_requires_exact_false_string(replacement):
    changes = _first_query_update_changes()
    changes[0]["ResourceChange"]["Replacement"] = replacement

    with pytest.raises(AdoptionError, match="Replacement|replacement|False"):
        validate_update_change_set(changes, _first_query_update_processed())


def test_first_query_update_modify_rejects_missing_replacement_key():
    changes = _first_query_update_changes()
    changes[0]["ResourceChange"].pop("Replacement")

    with pytest.raises(AdoptionError, match="Replacement|replacement|False"):
        validate_update_change_set(changes, _first_query_update_processed())


@pytest.mark.parametrize(
    "replacement",
    [None, False, True, 0, 1, "false", "True", "Conditional"],
    ids=(
        "null",
        "boolean-false",
        "boolean-true",
        "zero",
        "one",
        "lowercase",
        "true-string",
        "conditional",
    ),
)
def test_first_query_update_add_rejects_non_false_wire_value(replacement):
    changes = _first_query_update_changes()
    changes[1]["ResourceChange"]["Replacement"] = replacement

    with pytest.raises(AdoptionError, match="Replacement|replacement|False"):
        validate_update_change_set(changes, _first_query_update_processed())


def test_first_query_update_add_accepts_omitted_replacement():
    changes = _first_query_update_changes()
    changes[1]["ResourceChange"].pop("Replacement")

    validate_update_change_set(changes, _first_query_update_processed())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda changes: changes.pop(),
        lambda changes: changes.append(deepcopy(changes[-1])),
        lambda changes: changes[0]["ResourceChange"].update(
            {"Replacement": "True"}
        ),
        lambda changes: changes[1]["ResourceChange"].update(
            {"Action": "Modify"}
        ),
        lambda changes: changes[1]["ResourceChange"].update(
            {"LogicalResourceId": "FilesTable"}
        ),
        lambda changes: changes.append(
            {
                "ResourceChange": {
                    "Action": "Remove",
                    "LogicalResourceId": "QueryIntegration",
                    "ResourceType": "AWS::ApiGatewayV2::Integration",
                    "Replacement": "False",
                }
            }
        ),
    ],
    ids=(
        "missing-addition",
        "duplicate",
        "function-replacement",
        "addition-modify",
        "database-resource",
        "remove-imported-resource",
    ),
)
def test_first_query_update_rejects_any_non_exact_change_set(mutation):
    changes = _first_query_update_changes()
    mutation(changes)

    with pytest.raises(
        AdoptionError,
        match=(
            "UPDATE|change|exact|unexpected|duplicate|replacement|"
            "non-replacing|forbidden"
        ),
    ):
        validate_update_change_set(changes, _first_query_update_processed())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda resources: resources.update(
            {"QueryLambdaRole": {"Type": "AWS::IAM::Role"}}
        ),
        lambda resources: resources.update(
            {"NotificationTopic": {"Type": "AWS::SNS::Topic"}}
        ),
        lambda resources: resources["QueryFunction"]["Properties"].update(
            {"Role": {"Fn::GetAtt": ["QueryLambdaRole", "Arn"]}}
        ),
        lambda resources: resources["AuthTestPermission"]["Properties"].update(
            {
                "SourceArn": {
                    "Fn::Sub": "arn:${AWS::Partition}:execute-api:*:*:*/*/*/*"
                }
            }
        ),
        lambda resources: resources["AuthTestRoute"]["Properties"].update(
            {"Target": {"Fn::Sub": "integrations/${QueryIntegration}"}}
        ),
    ],
    ids=(
        "iam-role",
        "sns-topic",
        "database-role-reference",
        "wildcard-permission",
        "imported-route-representation",
    ),
)
def test_first_query_update_rejects_template_outside_55_resource_contract(mutation):
    processed = _first_query_update_processed()
    mutation(processed["Resources"])

    with pytest.raises(
        AdoptionError,
        match="template|resource|contract|QueryFunction|permission|route",
    ):
        validate_update_change_set(_first_query_update_changes(), processed)
