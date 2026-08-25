import re
from pathlib import Path

import pytest
import yaml


TEMPLATE_PATH = Path(__file__).with_name("template.yaml")


class CloudFormationLoader(yaml.SafeLoader):
    """Load CloudFormation short-form intrinsic tags as ordinary mappings."""


INTRINSIC_NAMES = {
    "Equals": "Fn::Equals",
    "GetAtt": "Fn::GetAtt",
    "If": "Fn::If",
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
    assert TEMPLATE_PATH.exists(), "template.yaml must exist"
    return yaml.load(TEMPLATE_PATH.read_text(encoding="utf-8"), Loader=CloudFormationLoader)


def _properties(template, resource):
    return template["Resources"][resource]["Properties"]


def _custom_s3_statements(template, function):
    statements = []
    for policy in _properties(template, function)["Policies"]:
        if isinstance(policy, dict):
            statements.extend(policy.get("Statement", []))
    return [
        statement
        for statement in statements
        if any(action.startswith("s3:") for action in _as_list(statement["Action"]))
    ]


def _as_list(value):
    return value if isinstance(value, list) else [value]


def test_template_declares_required_parameters(template):
    parameters = template["Parameters"]
    assert parameters["ExistingHttpApiId"]["Default"] == "2dd2aqb32j"
    assert "Default" not in parameters["ExistingJwtAuthorizerId"]
    assert parameters["AllowedOrigin"]["Default"] == "http://localhost:3000"
    assert "Default" not in parameters["MetadataApiBaseUrl"]
    assert "Default" not in parameters["InferenceApiUrl"]
    for name in ("MetadataApiBaseUrl", "InferenceApiUrl"):
        assert "AllowedPattern" in parameters[name]
        assert "HTTPS" in parameters[name]["ConstraintDescription"]
    assert parameters["InternalApiKey"] == {
        "Type": "String",
        "NoEcho": True,
        "Default": "",
    }
    assert parameters["FfmpegLayerArn"]["Default"] == ""


def test_endpoint_parameter_patterns_require_an_https_host_and_base_path(template):
    valid_urls = [
        "https://metadata.example",
        "https://metadata.example:8443/service",
        "https://api-id.execute-api.ap-southeast-2.amazonaws.com/prod",
    ]
    invalid_urls = [
        "http://metadata.example",
        "https:///missing-host",
        "https://user:password@metadata.example",
        "https://metadata.example/path?query=value",
        "https://metadata.example/path#fragment",
        "https://-invalid-host.example",
        "https://metadata.example:0/path",
        "https://metadata.example:65536/path",
        "https://metadata.example:invalid/path",
    ]

    for name in ("MetadataApiBaseUrl", "InferenceApiUrl"):
        pattern = re.compile(template["Parameters"][name]["AllowedPattern"])
        assert all(pattern.fullmatch(value) for value in valid_urls)
        assert all(not pattern.fullmatch(value) for value in invalid_urls)


def test_media_bucket_is_private_encrypted_and_recoverable(template):
    bucket = _properties(template, "MediaBucket")
    assert bucket["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }
    assert bucket["OwnershipControls"] == {
        "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]
    }
    encryption = bucket["BucketEncryption"]["ServerSideEncryptionConfiguration"]
    assert encryption == [
        {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
    ]
    cors = bucket["CorsConfiguration"]["CorsRules"]
    assert len(cors) == 1
    assert cors[0]["AllowedOrigins"] == [{"Ref": "AllowedOrigin"}]
    assert set(cors[0]["AllowedMethods"]) == {"PUT", "GET", "HEAD"}
    lifecycle = bucket["LifecycleConfiguration"]["Rules"]
    assert any(
        rule["Status"] == "Enabled"
        and rule["Prefix"] == "processing/"
        and rule["ExpirationInDays"] == 1
        for rule in lifecycle
    )


def test_s3_event_is_filtered_to_originals(template):
    event = _properties(template, "MediaProcessingFunction")["Events"][
        "OriginalUpload"
    ]
    assert event["Type"] == "S3"
    assert event["Properties"]["Events"] == "s3:ObjectCreated:*"
    assert event["Properties"]["Bucket"] == {"Ref": "MediaBucket"}
    rules = event["Properties"]["Filter"]["S3Key"]["Rules"]
    assert rules == [{"Name": "prefix", "Value": "originals/"}]


def test_function_runtime_and_handler_contracts(template):
    upload = _properties(template, "UploadFunction")
    assert upload["Runtime"] == "nodejs20.x"
    assert upload["CodeUri"] == "../../backend/lambdas/upload/"
    assert upload["Handler"] == "index.handler"

    processing = _properties(template, "MediaProcessingFunction")
    assert processing["Runtime"] == "python3.12"
    assert processing["CodeUri"] == "../../backend/lambdas/media-processing/"
    assert processing["Handler"] == "handler.handler"
    assert processing["Timeout"] == 900
    assert processing["MemorySize"] == 4096
    assert processing["EphemeralStorage"] == {"Size": 4096}
    assert processing["Layers"] == [
        {
            "Fn::If": [
                "HasFfmpegLayer",
                {"Ref": "FfmpegLayerArn"},
                {"Ref": "AWS::NoValue"},
            ]
        }
    ]

    storage_delete = _properties(template, "StorageDeleteFunction")
    assert storage_delete["Runtime"] == "nodejs20.x"
    assert storage_delete["CodeUri"] == "../../backend/lambdas/storage-delete/"
    assert storage_delete["Handler"] == "index.handler"

    asset_urls = _properties(template, "AssetUrlsFunction")
    assert asset_urls["Runtime"] == "nodejs20.x"
    assert asset_urls["CodeUri"] == "../../backend/lambdas/asset-urls/"
    assert asset_urls["Handler"] == "index.handler"


def test_function_environment_names_match_production_handlers(template):
    expected = {
        "UploadFunction": {
            "UPLOAD_BUCKET",
            "METADATA_API_BASE_URL",
            "INTERNAL_API_KEY",
            "MAX_UPLOAD_BYTES",
            "ALLOWED_ORIGIN",
        },
        "MediaProcessingFunction": {
            "MEDIA_BUCKET_NAME",
            "METADATA_API_BASE_URL",
            "INFERENCE_API_URL",
            "INTERNAL_API_KEY",
            "FFMPEG_PATH",
        },
        "StorageDeleteFunction": {"MEDIA_BUCKET_NAME"},
        "AssetUrlsFunction": {"MEDIA_BUCKET_NAME", "ALLOWED_ORIGIN"},
    }
    for function, names in expected.items():
        variables = _properties(template, function)["Environment"]["Variables"]
        assert set(variables) == names


def test_upload_post_route_uses_existing_jwt_authorizer(template):
    integration = _properties(template, "UploadIntegration")
    assert integration["ApiId"] == {"Ref": "ExistingHttpApiId"}
    assert integration["IntegrationType"] == "AWS_PROXY"
    assert integration["IntegrationUri"] == {
        "Fn::Sub": (
            "arn:${AWS::Partition}:apigateway:${AWS::Region}:lambda:path/"
            "2015-03-31/functions/${UploadFunction.Arn}/invocations"
        )
    }
    assert integration["PayloadFormatVersion"] == "2.0"

    route = _properties(template, "UploadRoute")
    assert route["ApiId"] == {"Ref": "ExistingHttpApiId"}
    assert route["RouteKey"] == "POST /upload-url"
    assert route["AuthorizationType"] == "JWT"
    assert route["AuthorizerId"] == {"Ref": "ExistingJwtAuthorizerId"}
    assert route["Target"] == {
        "Fn::Sub": "integrations/${UploadIntegration}"
    }

    permission = _properties(template, "UploadInvokePermission")
    assert permission["Principal"] == "apigateway.amazonaws.com"
    assert permission["Action"] == "lambda:InvokeFunction"
    assert permission["FunctionName"] == {"Ref": "UploadFunction"}
    assert permission["SourceArn"] == {
        "Fn::Sub": (
            "arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:"
            "${ExistingHttpApiId}/*/POST/upload-url"
        )
    }


def test_upload_options_route_is_unauthenticated_and_method_scoped(template):
    route = _properties(template, "UploadPreflightRoute")
    assert route == {
        "ApiId": {"Ref": "ExistingHttpApiId"},
        "RouteKey": "OPTIONS /upload-url",
        "AuthorizationType": "NONE",
        "Target": {"Fn::Sub": "integrations/${UploadIntegration}"},
    }

    permission = _properties(template, "UploadPreflightInvokePermission")
    assert permission == {
        "Action": "lambda:InvokeFunction",
        "FunctionName": {"Ref": "UploadFunction"},
        "Principal": "apigateway.amazonaws.com",
        "SourceArn": {
            "Fn::Sub": (
                "arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:"
                "${ExistingHttpApiId}/*/OPTIONS/upload-url"
            )
        },
    }


def test_asset_urls_routes_use_jwt_and_scoped_invoke_permissions(template):
    integration = _properties(template, "AssetUrlsIntegration")
    assert integration == {
        "ApiId": {"Ref": "ExistingHttpApiId"},
        "IntegrationType": "AWS_PROXY",
        "IntegrationMethod": "POST",
        "IntegrationUri": {
            "Fn::Sub": (
                "arn:${AWS::Partition}:apigateway:${AWS::Region}:lambda:path/"
                "2015-03-31/functions/${AssetUrlsFunction.Arn}/invocations"
            )
        },
        "PayloadFormatVersion": "2.0",
    }

    route = _properties(template, "AssetUrlsRoute")
    assert route == {
        "ApiId": {"Ref": "ExistingHttpApiId"},
        "RouteKey": "POST /asset-urls",
        "AuthorizationType": "JWT",
        "AuthorizerId": {"Ref": "ExistingJwtAuthorizerId"},
        "Target": {"Fn::Sub": "integrations/${AssetUrlsIntegration}"},
    }

    preflight = _properties(template, "AssetUrlsPreflightRoute")
    assert preflight == {
        "ApiId": {"Ref": "ExistingHttpApiId"},
        "RouteKey": "OPTIONS /asset-urls",
        "AuthorizationType": "NONE",
        "Target": {"Fn::Sub": "integrations/${AssetUrlsIntegration}"},
    }

    post_permission = _properties(template, "AssetUrlsInvokePermission")
    assert post_permission == {
        "Action": "lambda:InvokeFunction",
        "FunctionName": {"Ref": "AssetUrlsFunction"},
        "Principal": "apigateway.amazonaws.com",
        "SourceArn": {
            "Fn::Sub": (
                "arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:"
                "${ExistingHttpApiId}/*/POST/asset-urls"
            )
        },
    }

    options_permission = _properties(template, "AssetUrlsPreflightInvokePermission")
    assert options_permission == {
        "Action": "lambda:InvokeFunction",
        "FunctionName": {"Ref": "AssetUrlsFunction"},
        "Principal": "apigateway.amazonaws.com",
        "SourceArn": {
            "Fn::Sub": (
                "arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:"
                "${ExistingHttpApiId}/*/OPTIONS/asset-urls"
            )
        },
    }


def test_s3_policies_are_limited_to_owned_bucket_prefixes(template):
    expected = {
        "UploadFunction": [
            {
                "Effect": "Allow",
                "Action": "s3:PutObject",
                "Resource": {"Fn::Sub": "${MediaBucket.Arn}/originals/*"},
            }
        ],
        "MediaProcessingFunction": [
            {
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": [
                    {"Fn::Sub": "${MediaBucket.Arn}/originals/*"},
                    {"Fn::Sub": "${MediaBucket.Arn}/processing/*"},
                ],
            },
            {
                "Effect": "Allow",
                "Action": "s3:PutObject",
                "Resource": [
                    {"Fn::Sub": "${MediaBucket.Arn}/thumbnails/*"},
                    {"Fn::Sub": "${MediaBucket.Arn}/processing/*"},
                ],
            },
            {
                "Effect": "Allow",
                "Action": "s3:DeleteObject",
                "Resource": {"Fn::Sub": "${MediaBucket.Arn}/processing/*"},
            },
        ],
        "StorageDeleteFunction": [
            {
                "Effect": "Allow",
                "Action": "s3:DeleteObject",
                "Resource": [
                    {"Fn::Sub": "${MediaBucket.Arn}/originals/*"},
                    {"Fn::Sub": "${MediaBucket.Arn}/thumbnails/*"},
                    {"Fn::Sub": "${MediaBucket.Arn}/processing/*"},
                ],
            }
        ],
        "AssetUrlsFunction": [
            {
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": [
                    {"Fn::Sub": "${MediaBucket.Arn}/originals/*"},
                    {"Fn::Sub": "${MediaBucket.Arn}/thumbnails/*"},
                ],
            }
        ],
    }
    for function, wanted_statements in expected.items():
        policies = _properties(template, function)["Policies"]
        assert "AWSLambdaBasicExecutionRole" in policies
        statements = _custom_s3_statements(template, function)
        assert statements == wanted_statements
        assert all(statement["Resource"] != "*" for statement in statements)


def test_outputs_expose_bucket_and_function_arns(template):
    assert template["Outputs"] == {
        "MediaBucketName": {"Value": {"Ref": "MediaBucket"}},
        "UploadFunctionArn": {"Value": {"Fn::GetAtt": "UploadFunction.Arn"}},
        "MediaProcessingFunctionArn": {
            "Value": {"Fn::GetAtt": "MediaProcessingFunction.Arn"}
        },
        "StorageDeleteFunctionArn": {
            "Value": {"Fn::GetAtt": "StorageDeleteFunction.Arn"}
        },
        "AssetUrlsFunctionArn": {
            "Value": {"Fn::GetAtt": "AssetUrlsFunction.Arn"}
        },
    }
