from pathlib import Path
import re

import pytest
import yaml


TEMPLATE_PATH = Path(__file__).with_name("dynamodb.yaml")


class CloudFormationLoader(yaml.SafeLoader):
    """Load CloudFormation short-form intrinsic tags as ordinary mappings."""


INTRINSIC_NAMES = {
    "Equals": "Fn::Equals",
    "GetAtt": "Fn::GetAtt",
    "Join": "Fn::Join",
    "Not": "Fn::Not",
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
    "GET /auth-test",
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
    "POST /internal/assets/authorize",
}
OPTIONS_ROUTES = {
    "OPTIONS /auth-test",
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
IMPORTED_ROUTE_LOGICAL_IDS = {
    "AuthTestRoute", "QueryByTagsRoute", "QueryBySpeciesRoute", "QueryByThumbnailRoute", "QueryByFileRoute", "EditTagsRoute", "DeleteFilesRoute", "SubscribeRoute", "UnsubscribeRoute", "SubscriptionsRoute", "NotificationsRoute", "ReserveUploadRoute", "AcquireProcessingRoute", "CompleteFileRoute", "FailFileRoute", "AuthorizeAssetsRoute",
}


def test_query_function_reuses_imported_name_and_stack_owned_role(template):
    function = template["Resources"]["QueryFunction"]
    assert function["DeletionPolicy"] == function["UpdateReplacePolicy"] == "Retain"
    assert function["Properties"]["FunctionName"] == "PacificBioArchive-QueryLambda"
    assert function["Properties"]["Role"] == {"Fn::GetAtt": "QueryLambdaRole.Arn"}
    assert "Policies" not in function["Properties"]
    role = template["Resources"]["QueryLambdaRole"]
    assert role["Type"] == "AWS::IAM::Role"
    assert role["DeletionPolicy"] == role["UpdateReplacePolicy"] == "Retain"
    assert role["Properties"]["RoleName"] == "PacificBioArchive-QueryLambdaRole"


def test_imported_integration_and_routes_are_retained(template):
    for logical_id in {"QueryIntegration", *IMPORTED_ROUTE_LOGICAL_IDS}:
        assert template["Resources"][logical_id]["DeletionPolicy"] == "Retain"
        assert template["Resources"][logical_id]["UpdateReplacePolicy"] == "Retain"


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
    assert parameters["AllowedOrigin"]["Default"] == "http://localhost:3000"
    assert parameters["PublicAllowedOrigin"]["Default"] == "https://quinby8930.github.io"
    assert "HTTPS" in parameters["InferenceApiBaseUrl"]["ConstraintDescription"]
    assert parameters["NotificationEmailEndpoint"] == {
        "Type": "String",
        "Default": "",
        "Description": "Optional email address subscribed to the notification topic.",
    }
    assert parameters["AllowLegacyProcessingCallbacks"] == {
        "Type": "String",
        "Default": "false",
        "AllowedValues": ["true", "false"],
        "Description": (
            "Temporary rolling-deployment compatibility for tokenless Member B "
            "processing callbacks. Disable after Member B is token-aware."
        ),
    }


def test_inference_url_constraint_rejects_decoded_infer_segments_only(template):
    parameter = template["Parameters"]["InferenceApiBaseUrl"]
    pattern = re.compile(parameter["AllowedPattern"])
    valid_urls = [
        "https://inference.example",
        "https://inference.example/inference",
        "https://inference.example/api/inferential",
    ]
    rejected_urls = [
        "https://inference.example/infer",
        "https://inference.example/infer/",
        "https://inference.example/api/infer",
        "https://inference.example/%69nfer",
        "https://inference.example/InFeR",
        "https://inference.example/api/%49nF%65r/",
    ]

    assert all(pattern.fullmatch(value) for value in valid_urls)
    assert all(not pattern.fullmatch(value) for value in rejected_urls)
    assert "must not contain" in parameter["ConstraintDescription"].lower()
    assert "infer" in parameter["ConstraintDescription"].lower()


def test_iam_arn_parameters_reject_wildcards_and_accept_resource_names(template):
    cases = {
        "QueryInputBucketName": ("private-media-123", ["*", "bucket/*"]),
        "StorageDeleteFunctionName": (
            "storage-delete-prod",
            [
                "*",
                "function:*",
                (
                    "arn:aws:lambda:ap-southeast-2:123456789012:"
                    "function:storage-delete-prod"
                ),
            ],
        ),
    }
    for name, (valid, invalid_values) in cases.items():
        pattern = re.compile(template["Parameters"][name]["AllowedPattern"])
        assert pattern.fullmatch(valid)
        assert all(not pattern.fullmatch(value) for value in invalid_values)


def test_all_four_dynamodb_tables_are_retained(template):
    expected_keys = {
        "FilesTable": [("file_id", "HASH")],
        "ReservationsTable": [("reservation_key", "HASH")],
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
        assert resource["DeletionPolicy"] == "Retain"
        assert resource["UpdateReplacePolicy"] == "Retain"
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
        "RESERVATIONS_TABLE": {"Ref": "ReservationsTable"},
        "SUBSCRIPTIONS_TABLE": {"Ref": "SubscriptionsTable"},
        "NOTIFICATIONS_TABLE": {"Ref": "NotificationsTable"},
        "STORAGE_BACKEND": "lambda",
        "STORAGE_DELETE_FUNCTION_NAME": {"Ref": "StorageDeleteFunctionName"},
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
                [{"Ref": "AllowedOrigin"}, {"Ref": "PublicAllowedOrigin"}],
            ]
        },
    }


def test_query_function_policies_are_least_privilege(template):
    role = _properties(template, "QueryLambdaRole")
    assert role["ManagedPolicyArns"] == ["arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"]
    statements = role["Policies"][0]["PolicyDocument"]["Statement"]
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
                "dynamodb:TransactWriteItems",
            ],
            "Resource": [
                {"Fn::GetAtt": "FilesTable.Arn"},
                {"Fn::GetAtt": "ReservationsTable.Arn"},
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
        {
            "Effect": "Allow",
            "Action": "sns:Publish",
            "Resource": {"Ref": "NotificationTopic"},
        },
    ]
    assert all(statement["Resource"] != "*" for statement in statements)


def test_sns_topic_and_optional_email_subscription_are_wired(template):
    assert template["Conditions"]["HasNotificationEmailEndpoint"] == {
        "Fn::Not": [
            {
                "Fn::Equals": [
                    {"Ref": "NotificationEmailEndpoint"},
                    "",
                ]
            }
        ]
    }
    topic = template["Resources"]["NotificationTopic"]
    assert topic["Type"] == "AWS::SNS::Topic"
    subscription = template["Resources"]["NotificationEmailSubscription"]
    assert subscription == {
        "Type": "AWS::SNS::Subscription",
        "Condition": "HasNotificationEmailEndpoint",
        "Properties": {
            "Protocol": "email",
            "Endpoint": {"Ref": "NotificationEmailEndpoint"},
            "TopicArn": {"Ref": "NotificationTopic"},
        },
    }
    assert template["Outputs"]["NotificationTopicArn"]["Value"] == {
        "Ref": "NotificationTopic"
    }
    assert template["Outputs"]["ReservationsTableName"]["Value"] == {
        "Ref": "ReservationsTable"
    }


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
