from pathlib import Path
import re

import pytest
import yaml


DATABASE_TEMPLATE_PATH = Path(__file__).with_name("dynamodb.yaml")
QUERY_TEMPLATE_PATH = Path(__file__).with_name("query-adoption.yaml")


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


def _load(path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=CloudFormationLoader)


@pytest.fixture(scope="module")
def database_template():
    return _load(DATABASE_TEMPLATE_PATH)


@pytest.fixture(scope="module")
def query_template():
    return _load(
        QUERY_TEMPLATE_PATH
        if QUERY_TEMPLATE_PATH.exists()
        else DATABASE_TEMPLATE_PATH
    )


DATABASE_LOGICAL_IDS = {
    "FilesTable",
    "SubscriptionsTable",
    "NotificationsTable",
    "QueryLambdaRole",
}
IMPORTED_ROUTE_KEYS_BY_LOGICAL_ID = {
    "AuthTestRoute": "GET /auth-test",
    "QueryByTagsRoute": "POST /query/by-tags",
    "QueryBySpeciesRoute": "POST /query/by-species",
    "QueryByThumbnailRoute": "GET /query/by-thumbnail",
    "QueryByFileRoute": "POST /query/by-file",
    "EditTagsRoute": "POST /tags/edit",
    "DeleteFilesRoute": "POST /files/delete",
    "SubscribeRoute": "POST /notifications/subscribe",
    "UnsubscribeRoute": "DELETE /notifications/subscribe",
    "SubscriptionsRoute": "GET /notifications/subscriptions",
    "NotificationsRoute": "GET /notifications",
    "ReserveUploadRoute": "POST /internal/uploads/reserve",
    "AcquireProcessingRoute": "POST /internal/files/{file_id}/processing",
    "CompleteFileRoute": "PUT /internal/files/{file_id}/complete",
    "FailFileRoute": "PUT /internal/files/{file_id}/failed",
    "AuthorizeAssetsRoute": "POST /internal/assets/authorize",
}
OPTIONS_ROUTE_KEYS_BY_LOGICAL_ID = {
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
IMPORTED_LOGICAL_IDS = {
    "ReservationsTable",
    "QueryFunction",
    "QueryIntegration",
    *IMPORTED_ROUTE_KEYS_BY_LOGICAL_ID,
}
PERMISSION_LOGICAL_IDS = {
    f"{logical_id.removesuffix('Route')}Permission"
    for logical_id in {
        *IMPORTED_ROUTE_KEYS_BY_LOGICAL_ID,
        *OPTIONS_ROUTE_KEYS_BY_LOGICAL_ID,
    }
}
QUERY_PARAMETER_NAMES = {
    "ExistingQueryLambdaRoleArn",
    "ExistingHttpApiId",
    "ExistingJwtAuthorizerId",
    "ExistingFilesTableName",
    "ExistingSubscriptionsTableName",
    "ExistingNotificationsTableName",
    "AllowedOrigin",
    "PublicAllowedOrigin",
    "QueryInputBucketName",
    "StorageDeleteFunctionName",
    "InferenceApiBaseUrl",
    "AllowLegacyProcessingCallbacks",
    "InternalApiKey",
}


def _references(value, path=()):
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "Ref":
                yield path + (key,), nested
            elif key == "Fn::GetAtt":
                target = nested[0] if isinstance(nested, list) else nested.split(".", 1)[0]
                yield path + (key,), target
            yield from _references(nested, path + (key,))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _references(nested, path + (index,))


def test_database_template_has_exact_original_stack_ownership(database_template):
    assert set(database_template["Resources"]) == DATABASE_LOGICAL_IDS
    assert set(database_template["Outputs"]) == {
        "TableName",
        "SubscriptionsTableName",
        "NotificationsTableName",
        "QueryLambdaRoleArn",
    }
    assert all("Export" not in output for output in database_template["Outputs"].values())
    assert set(database_template["Parameters"]) == {
        "ReservationsTableArn",
        "QueryInputBucketName",
        "StorageDeleteFunctionName",
    }
    assert "InternalApiKey" not in str(database_template)


def test_database_resources_are_retained_and_role_uses_only_ordinary_inputs(database_template):
    resources = database_template["Resources"]
    for resource in resources.values():
        assert resource["DeletionPolicy"] == "Retain"
        assert resource["UpdateReplacePolicy"] == "Retain"
    statements = resources["QueryLambdaRole"]["Properties"]["Policies"][0][
        "PolicyDocument"
    ]["Statement"]
    assert statements[0]["Resource"] == [
        {"Fn::GetAtt": "FilesTable.Arn"},
        {"Ref": "ReservationsTableArn"},
        {"Fn::GetAtt": "SubscriptionsTable.Arn"},
        {"Fn::GetAtt": "NotificationsTable.Arn"},
    ]
    assert all(statement["Resource"] != "*" for statement in statements)
    assert "sns:Publish" not in str(statements)


def test_query_template_has_exact_disjoint_19_10_26_resource_groups(query_template):
    resources = query_template["Resources"]
    assert set(resources) == (
        IMPORTED_LOGICAL_IDS
        | set(OPTIONS_ROUTE_KEYS_BY_LOGICAL_ID)
        | PERMISSION_LOGICAL_IDS
    )
    assert len(resources) == 55
    assert not DATABASE_LOGICAL_IDS & set(resources)
    assert {
        logical_id
        for logical_id, resource in resources.items()
        if resource["Type"] == "AWS::Lambda::Permission"
    } == PERMISSION_LOGICAL_IDS
    assert {
        logical_id
        for logical_id, resource in resources.items()
        if resource["Type"] == "AWS::ApiGatewayV2::Route"
        and resource["Properties"]["RouteKey"].startswith("OPTIONS ")
    } == set(OPTIONS_ROUTE_KEYS_BY_LOGICAL_ID)


def test_query_template_retains_exact_imported_resources_only(query_template):
    resources = query_template["Resources"]
    retained = {
        logical_id
        for logical_id, resource in resources.items()
        if resource.get("DeletionPolicy") == "Retain"
        and resource.get("UpdateReplacePolicy") == "Retain"
    }
    assert retained == IMPORTED_LOGICAL_IDS
    assert resources["ReservationsTable"]["Type"] == "AWS::DynamoDB::Table"
    assert resources["QueryFunction"]["Type"] == "AWS::Serverless::Function"
    assert resources["QueryIntegration"]["Type"] == "AWS::ApiGatewayV2::Integration"


def test_query_template_has_exact_parameter_and_secret_contract(query_template):
    parameters = query_template["Parameters"]
    assert set(parameters) == QUERY_PARAMETER_NAMES
    assert parameters["InternalApiKey"] == {
        "Type": "String",
        "NoEcho": True,
        "MinLength": 1,
    }
    assert all(
        definition.get("Type") == "String" and definition.get("NoEcho") is not True
        for name, definition in parameters.items()
        if name != "InternalApiKey"
    )
    refs = [item for item in _references(query_template) if item[1] == "InternalApiKey"]
    assert refs == [
        (
            (
                "Resources",
                "QueryFunction",
                "Properties",
                "Environment",
                "Variables",
                "INTERNAL_API_KEY",
                "Ref",
            ),
            "InternalApiKey",
        )
    ]


def test_query_template_uses_only_parameters_for_database_owned_resources(query_template):
    assert not [
        (path, target)
        for path, target in _references(query_template)
        if target in DATABASE_LOGICAL_IDS
    ]
    function = query_template["Resources"]["QueryFunction"]["Properties"]
    assert function["Role"] == {"Ref": "ExistingQueryLambdaRoleArn"}
    assert function["Environment"]["Variables"] == {
        "REPO_BACKEND": "dynamodb",
        "DYNAMODB_TABLE": {"Ref": "ExistingFilesTableName"},
        "RESERVATIONS_TABLE": {"Ref": "ReservationsTable"},
        "SUBSCRIPTIONS_TABLE": {"Ref": "ExistingSubscriptionsTableName"},
        "NOTIFICATIONS_TABLE": {"Ref": "ExistingNotificationsTableName"},
        "STORAGE_BACKEND": "lambda",
        "STORAGE_DELETE_FUNCTION_NAME": {"Ref": "StorageDeleteFunctionName"},
        "TAG_DETECTOR_BACKEND": "remote",
        "QUERY_INPUT_BUCKET": {"Ref": "QueryInputBucketName"},
        "INFERENCE_API_URL": {"Ref": "InferenceApiBaseUrl"},
        "INTERNAL_API_KEY": {"Ref": "InternalApiKey"},
        "ALLOW_LEGACY_PROCESSING_CALLBACKS": {
            "Ref": "AllowLegacyProcessingCallbacks"
        },
        "CORS_ORIGINS": {
            "Fn::Join": [
                ",",
                [{"Ref": "AllowedOrigin"}, {"Ref": "PublicAllowedOrigin"}],
            ]
        },
    }


def test_query_imported_routes_match_import_intrinsic_representation(query_template):
    resources = query_template["Resources"]
    integration = resources["QueryIntegration"]["Properties"]
    assert integration["ApiId"] == {"Ref": "ExistingHttpApiId"}
    assert integration["IntegrationUri"] == {
        "Fn::Sub": (
            "arn:${AWS::Partition}:apigateway:${AWS::Region}:lambda:path/"
            "2015-03-31/functions/${QueryFunction.Arn}/invocations"
        )
    }
    for logical_id, route_key in IMPORTED_ROUTE_KEYS_BY_LOGICAL_ID.items():
        route = resources[logical_id]["Properties"]
        assert route["RouteKey"] == route_key
        assert route["ApiId"] == {"Ref": "ExistingHttpApiId"}
        assert route["Target"] == {
            "Fn::Join": ["", ["integrations/", {"Ref": "QueryIntegration"}]]
        }


def test_query_permissions_are_exactly_method_and_path_scoped(query_template):
    route_keys = {
        **IMPORTED_ROUTE_KEYS_BY_LOGICAL_ID,
        **OPTIONS_ROUTE_KEYS_BY_LOGICAL_ID,
    }

    def source_arn(route_key):
        method, path = route_key.split(" ", 1)
        scoped_path = path.replace("{file_id}", "*")
        return (
            "arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:"
            f"${{ExistingHttpApiId}}/*/{method}{scoped_path}"
        )

    for route_logical_id, route_key in route_keys.items():
        logical_id = f"{route_logical_id.removesuffix('Route')}Permission"
        permission = query_template["Resources"][logical_id]["Properties"]
        assert permission == {
            "Action": "lambda:InvokeFunction",
            "FunctionName": {"Ref": "QueryFunction"},
            "Principal": "apigateway.amazonaws.com",
            "SourceArn": {"Fn::Sub": source_arn(route_key)},
        }
        assert "/*/*" not in permission["SourceArn"]["Fn::Sub"]


def test_query_template_omits_notifications_outputs_roles_and_exports(query_template):
    assert "Outputs" not in query_template
    assert "Conditions" not in query_template
    assert all(
        resource["Type"]
        not in {
            "AWS::IAM::Role",
            "AWS::SNS::Topic",
            "AWS::SNS::Subscription",
        }
        for resource in query_template["Resources"].values()
    )
    serialized = str(query_template)
    assert "NOTIFICATION_PUBLISHER" not in serialized
    assert "SNS_TOPIC_ARN" not in serialized
    assert "Export" not in serialized


def test_query_parameter_constraints_remain_fail_closed(query_template):
    parameters = query_template["Parameters"]
    inference = re.compile(parameters["InferenceApiBaseUrl"]["AllowedPattern"])
    assert inference.fullmatch("https://inference.example/api/inference")
    assert not inference.fullmatch("https://inference.example/api/infer")
    bucket = re.compile(parameters["QueryInputBucketName"]["AllowedPattern"])
    function = re.compile(parameters["StorageDeleteFunctionName"]["AllowedPattern"])
    assert bucket.fullmatch("private-media-123")
    assert not bucket.fullmatch("bucket/*")
    assert function.fullmatch("storage-delete-prod")
    assert not function.fullmatch("function:*")
