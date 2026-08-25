from pathlib import Path
import re

import pytest
import yaml


TEMPLATE_PATH = Path(__file__).with_name("dynamodb.yaml")


class CloudFormationLoader(yaml.SafeLoader):
    """Load CloudFormation short-form intrinsic tags as ordinary mappings."""


INTRINSIC_NAMES = {
    "GetAtt": "Fn::GetAtt",
    "Sub": "Fn::Sub",
}


def _construct_intrinsic(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {INTRINSIC_NAMES.get(tag_suffix, tag_suffix): value}


CloudFormationLoader.add_multi_constructor("!", _construct_intrinsic)


@pytest.fixture(scope="module")
def template():
    return yaml.load(
        TEMPLATE_PATH.read_text(encoding="utf-8"), Loader=CloudFormationLoader
    )


def _properties(template, resource):
    return template["Resources"][resource]["Properties"]


PUBLIC_ROUTES = {
    "POST /query/by-tags",
    "POST /query/by-species",
    "GET /query/by-thumbnail",
    "POST /query/by-file",
    "POST /tags/edit",
    "POST /files/delete",
    "POST /notifications/subscribe",
    "DELETE /notifications/subscribe",
    "GET /notifications/subscriptions",
    "GET /notifications",
}
INTERNAL_ROUTES = {
    "POST /internal/uploads/reserve",
    "POST /internal/files/{file_id}/processing",
    "PUT /internal/files/{file_id}/complete",
    "PUT /internal/files/{file_id}/failed",
}
OPTIONS_ROUTES = {
    "OPTIONS /query/by-tags",
    "OPTIONS /query/by-species",
    "OPTIONS /query/by-thumbnail",
    "OPTIONS /query/by-file",
    "OPTIONS /tags/edit",
    "OPTIONS /files/delete",
    "OPTIONS /notifications/subscribe",
    "OPTIONS /notifications/subscriptions",
    "OPTIONS /notifications",
}


def test_template_is_sam_and_requires_external_deployment_values(template):
    assert template["Transform"] == "AWS::Serverless-2016-10-31"
    parameters = template["Parameters"]
    for name in (
        "ExistingHttpApiId",
        "ExistingJwtAuthorizerId",
        "QueryInputBucketName",
        "StorageDeleteFunctionName",
        "InferenceApiBaseUrl",
        "InternalApiKey",
    ):
        assert "Default" not in parameters[name]
    assert parameters["InternalApiKey"] == {
        "Type": "String",
        "NoEcho": True,
        "MinLength": 1,
    }
    assert "HTTPS" in parameters["InferenceApiBaseUrl"]["ConstraintDescription"]


def test_iam_arn_parameters_reject_wildcards_and_accept_resource_names(template):
    cases = {
        "QueryInputBucketName": ("private-media-123", ["*", "bucket/*"]),
        "StorageDeleteFunctionName": ("storage-delete-prod", ["*", "function:*"]),
    }
    for name, (valid, invalid_values) in cases.items():
        pattern = re.compile(template["Parameters"][name]["AllowedPattern"])
        assert pattern.fullmatch(valid)
        assert all(not pattern.fullmatch(value) for value in invalid_values)


def test_all_three_dynamodb_tables_are_retained(template):
    expected_keys = {
        "FilesTable": [("file_id", "HASH")],
        "SubscriptionsTable": [("user_id", "HASH"), ("species", "RANGE")],
        "NotificationsTable": [
            ("user_id", "HASH"),
            ("notification_id", "RANGE"),
        ],
    }
    for name, expected in expected_keys.items():
        resource = template["Resources"][name]
        assert resource["Type"] == "AWS::DynamoDB::Table"
        assert resource["Properties"]["BillingMode"] == "PAY_PER_REQUEST"
        assert [
            (item["AttributeName"], item["KeyType"])
            for item in resource["Properties"]["KeySchema"]
        ] == expected


def test_query_function_runtime_handler_and_production_environment(template):
    function = template["Resources"]["QueryFunction"]
    assert function["Type"] == "AWS::Serverless::Function"
    properties = function["Properties"]
    assert properties["CodeUri"] == "../../backend/lambdas/query/"
    assert properties["Handler"] == "lambda_function.handler"
    assert properties["Runtime"] == "python3.12"
    assert properties["Timeout"] == 30
    assert properties["MemorySize"] == 1024
    assert properties["Environment"]["Variables"] == {
        "REPO_BACKEND": "dynamodb",
        "DYNAMODB_TABLE": {"Ref": "FilesTable"},
        "SUBSCRIPTIONS_TABLE": {"Ref": "SubscriptionsTable"},
        "NOTIFICATIONS_TABLE": {"Ref": "NotificationsTable"},
        "STORAGE_BACKEND": "lambda",
        "STORAGE_DELETE_FUNCTION_NAME": {"Ref": "StorageDeleteFunctionName"},
        "TAG_DETECTOR_BACKEND": "remote",
        "QUERY_INPUT_BUCKET": {"Ref": "QueryInputBucketName"},
        "INFERENCE_API_URL": {"Ref": "InferenceApiBaseUrl"},
        "INTERNAL_API_KEY": {"Ref": "InternalApiKey"},
        "CORS_ORIGINS": {"Ref": "AllowedOrigin"},
    }


def test_query_function_policies_are_least_privilege(template):
    policies = _properties(template, "QueryFunction")["Policies"]
    assert policies[0] == "AWSLambdaBasicExecutionRole"
    statements = policies[1]["Statement"]
    assert statements == [
        {
            "Effect": "Allow",
            "Action": [
                "dynamodb:PutItem",
                "dynamodb:GetItem",
                "dynamodb:Scan",
                "dynamodb:Query",
                "dynamodb:UpdateItem",
                "dynamodb:DeleteItem",
            ],
            "Resource": [
                {"Fn::GetAtt": "FilesTable.Arn"},
                {"Fn::GetAtt": "SubscriptionsTable.Arn"},
                {"Fn::GetAtt": "NotificationsTable.Arn"},
            ],
        },
        {
            "Effect": "Allow",
            "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
            "Resource": {
                "Fn::Sub": "arn:${AWS::Partition}:s3:::${QueryInputBucketName}/query-inputs/*"
            },
        },
        {
            "Effect": "Allow",
            "Action": "lambda:InvokeFunction",
            "Resource": {
                "Fn::Sub": (
                    "arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:"
                    "function:${StorageDeleteFunctionName}"
                )
            },
        },
    ]
    assert all(statement["Resource"] != "*" for statement in statements)


def test_routes_share_one_integration_and_have_explicit_auth(template):
    resources = template["Resources"]
    integrations = [
        item for item in resources.values() if item["Type"] == "AWS::ApiGatewayV2::Integration"
    ]
    assert len(integrations) == 1
    integration = integrations[0]["Properties"]
    assert integration == {
        "ApiId": {"Ref": "ExistingHttpApiId"},
        "IntegrationType": "AWS_PROXY",
        "IntegrationMethod": "POST",
        "IntegrationUri": {
            "Fn::Sub": (
                "arn:${AWS::Partition}:apigateway:${AWS::Region}:lambda:path/"
                "2015-03-31/functions/${QueryFunction.Arn}/invocations"
            )
        },
        "PayloadFormatVersion": "2.0",
    }

    routes = {
        item["Properties"]["RouteKey"]: item["Properties"]
        for item in resources.values()
        if item["Type"] == "AWS::ApiGatewayV2::Route"
    }
    assert set(routes) == PUBLIC_ROUTES | INTERNAL_ROUTES | OPTIONS_ROUTES
    assert "$default" not in routes
    for route_key in PUBLIC_ROUTES:
        assert routes[route_key]["AuthorizationType"] == "JWT"
        assert routes[route_key]["AuthorizerId"] == {
            "Ref": "ExistingJwtAuthorizerId"
        }
    for route_key in INTERNAL_ROUTES | OPTIONS_ROUTES:
        assert routes[route_key]["AuthorizationType"] == "NONE"
        assert "AuthorizerId" not in routes[route_key]
    assert all(
        route["Target"] == {"Fn::Sub": "integrations/${QueryIntegration}"}
        for route in routes.values()
    )


def test_every_route_has_a_method_scoped_invoke_permission(template):
    permissions = [
        item["Properties"]
        for item in template["Resources"].values()
        if item["Type"] == "AWS::Lambda::Permission"
    ]
    assert len(permissions) == len(PUBLIC_ROUTES | INTERNAL_ROUTES | OPTIONS_ROUTES)
    source_arns = {permission["SourceArn"]["Fn::Sub"] for permission in permissions}

    def source_arn(route_key):
        method, path = route_key.split(" ", 1)
        scoped_path = path.lstrip("/").replace("{file_id}", "*")
        return (
            "arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:"
            f"${{ExistingHttpApiId}}/*/{method}/{scoped_path}"
        )

    assert source_arns == {
        source_arn(route)
        for route in PUBLIC_ROUTES | INTERNAL_ROUTES | OPTIONS_ROUTES
    }
    assert all(
        permission["Action"] == "lambda:InvokeFunction"
        and permission["FunctionName"] == {"Ref": "QueryFunction"}
        and permission["Principal"] == "apigateway.amazonaws.com"
        for permission in permissions
    )
