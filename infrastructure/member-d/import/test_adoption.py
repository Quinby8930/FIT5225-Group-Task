from copy import deepcopy
from pathlib import Path
import re
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))

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
                "Sid": "AllowExecutionFromAPIGateway",
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
        "owned_physical_ids": set(),
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


def _route_scoped_lambda_policy(snapshot, *, include_legacy):
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
    if include_legacy:
        statements.append(
            deepcopy(snapshot["function"]["resource_policy"]["Statement"][0])
        )
    return {"Version": "2012-10-17", "Statement": statements}


@pytest.mark.parametrize("expect_legacy", [True, False])
def test_post_update_lambda_policy_requires_exact_26_scoped_permissions(
    expect_legacy,
):
    snapshot = valid_snapshot()
    policy = _route_scoped_lambda_policy(
        snapshot,
        include_legacy=expect_legacy,
    )

    scoped = [
        statement
        for statement in policy["Statement"]
        if "Condition" in statement
    ]
    assert len(scoped) == 26
    assert validate_lambda_policy_after_update(
        policy,
        snapshot,
        expect_legacy=expect_legacy,
    ) == "AllowExecutionFromAPIGateway"


def test_post_update_lambda_policy_rejects_missing_scoped_permission():
    snapshot = valid_snapshot()
    policy = _route_scoped_lambda_policy(snapshot, include_legacy=False)
    policy["Statement"].pop(0)

    with pytest.raises(AdoptionError, match="exact route-scoped permissions"):
        validate_lambda_policy_after_update(
            policy,
            snapshot,
            expect_legacy=False,
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


def test_lambda_broad_gateway_policy_requires_a_nonempty_statement_sid():
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
    template = build_import_template(valid_snapshot(), CodeArtifact("private-artifacts", "backups/code.zip", "version-1"))
    imported = {"ReservationsTable", "QueryFunction", "QueryIntegration", *ROUTES_BY_LOGICAL_ID}
    for logical_id in imported:
        assert template["Resources"][logical_id]["DeletionPolicy"] == "Retain"
        assert template["Resources"][logical_id]["UpdateReplacePolicy"] == "Retain"
    assert template["Parameters"]["InternalApiKey"] == {"Type": "String", "NoEcho": True, "MinLength": 1}
    rendered = str(template)
    assert "fixture-secret" not in rendered
    assert "POST /upload-url" not in rendered


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
    assert function["Properties"]["Environment"]["Variables"]["INTERNAL_API_KEY"] == {"Ref": "InternalApiKey"}


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
    after["stack"]["managed"]["ReservationsTable"] = "PacificBioArchiveUploadReservations"
    for logical_id, route in zip(ROUTES_BY_LOGICAL_ID, after["api"]["routes"]):
        after["stack"]["managed"][logical_id] = route["RouteId"]
    assert_runtime_unchanged(before, after)


def test_post_import_snapshot_rejects_wrong_adopted_physical_identity():
    snapshot = valid_snapshot()
    snapshot["stack"]["managed"].update({
        "ReservationsTable": "wrong-table",
        "QueryFunction": "PacificBioArchive-QueryLambda",
        "QueryIntegration": "fbjojun",
    })
    for logical_id, route in zip(ROUTES_BY_LOGICAL_ID, snapshot["api"]["routes"]):
        snapshot["stack"]["managed"][logical_id] = route["RouteId"]

    with pytest.raises(AdoptionError, match="physical identity"):
        validate_snapshot(snapshot)


def test_post_import_runtime_comparison_rejects_reservations_table_change():
    before = valid_snapshot()
    after = deepcopy(before)
    after["reservations_table"]["BillingMode"] = "PROVISIONED"

    with pytest.raises(AdoptionError, match="runtime changed|ReservationsTable"):
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
    with pytest.raises(AdoptionError, match="forbidden"):
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


def test_update_accepts_only_exact_maintained_role_reconciliation():
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

    with pytest.raises(AdoptionError, match="ReservationsTable"):
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

    with pytest.raises(AdoptionError, match="FilesTable|Remove|removal"):
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
    changes = [{
        "ResourceChange": {
            "Action": "Modify",
            "LogicalResourceId": "QueryIntegration",
            "ResourceType": "AWS::ApiGatewayV2::Route",
            "Replacement": "False",
        }
    }]

    with pytest.raises(AdoptionError, match="resource type mismatch"):
        validate_update_change_set(changes, processed)


def test_update_accepts_maintained_processed_template_and_allowed_changes():
    processed = _update_processed(
        _maintained_role_target(),
        include_additions=True,
    )
    audited = approved_role_drift_snapshot()["role"]
    changes = [{
        "ResourceChange": {
            "Action": "Modify",
            "LogicalResourceId": "QueryLambdaRole",
            "ResourceType": "AWS::IAM::Role",
            "Replacement": "False",
        }
    }]
    changes.extend(
        {
            "ResourceChange": {
                "Action": "Add",
                "LogicalResourceId": logical_id,
                "ResourceType": resource["Type"],
            }
        }
        for logical_id, resource in processed["Resources"].items()
        if logical_id
        in {
            "NotificationTopic",
            "NotificationEmailSubscription",
            *_MAINTAINED_OPTIONS_ROUTES,
            *(
                _permission_logical_id(route_id)
                for route_id in (
                    *_MAINTAINED_IMPORTED_ROUTES,
                    *_MAINTAINED_OPTIONS_ROUTES,
                )
            ),
        }
    )

    validate_update_change_set(changes, processed, audited)


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


def test_role_modify_allows_only_new_retain_metadata():
    baseline = _maintained_role_target()
    baseline.pop("DeletionPolicy")
    baseline.pop("UpdateReplacePolicy")
    audited = {"processed_definition": baseline}
    processed = _update_processed(_maintained_role_target())
    changes = [{"ResourceChange": {"Action": "Modify", "LogicalResourceId": "QueryLambdaRole", "ResourceType": "AWS::IAM::Role", "Replacement": "False"}}]
    validate_update_change_set(changes, processed, audited)


def test_hardening_only_update_accepts_exact_query_function_modify():
    changes = [{
        "ResourceChange": {
            "Action": "Modify",
            "LogicalResourceId": "QueryFunction",
            "ResourceType": "AWS::Lambda::Function",
            "Replacement": "False",
        }
    }]

    validate_update_change_set(
        changes,
        _update_processed(_maintained_role_target()),
        None,
        hardening_only=True,
    )


@pytest.mark.parametrize(
    "change",
    [
        {
            "Action": "Modify",
            "LogicalResourceId": "ReservationsTable",
            "ResourceType": "AWS::DynamoDB::Table",
            "Replacement": "False",
        },
        {
            "Action": "Modify",
            "LogicalResourceId": "QueryByTagsRoute",
            "ResourceType": "AWS::ApiGatewayV2::Route",
            "Replacement": "False",
        },
        {
            "Action": "Add",
            "LogicalResourceId": "BackdoorRoute",
            "ResourceType": "AWS::ApiGatewayV2::Route",
            "Replacement": "False",
        },
        {
            "Action": "Modify",
            "LogicalResourceId": "QueryFunction",
            "ResourceType": "AWS::Lambda::Function",
            "Replacement": "True",
        },
    ],
    ids=("table-modify", "route-modify", "resource-add", "function-replacement"),
)
def test_hardening_only_update_rejects_every_other_change(change):
    with pytest.raises(AdoptionError, match="hardening|QueryFunction|exact"):
        validate_update_change_set(
            [{"ResourceChange": change}],
            _update_processed(_maintained_role_target()),
            None,
            hardening_only=True,
        )


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
