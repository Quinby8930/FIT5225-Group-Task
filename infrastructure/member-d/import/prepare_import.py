"""AWS CLI boundary for Member D adoption.

The command boundary is intentionally narrow: it audits, prepares files, and
reads change sets.  It never creates, executes, or deletes a change set.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import stat
import subprocess
import time
import yaml
from copy import deepcopy
from functools import wraps
from urllib.parse import unquote
from urllib.request import urlopen
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

from adoption import (
    AdoptionError,
    CodeArtifact,
    ROUTES_BY_LOGICAL_ID,
    SOURCE_STACK_NAME,
    TARGET_STACK_NAME,
    assert_import_preview_equivalent,
    assert_post_import_boundary_current,
    assert_post_import_equivalent,
    assert_runtime_unchanged,
    assert_update_rollback_equivalent,
    build_import_template,
    build_parameters_to_reuse,
    build_resources_to_import,
    classify_recovery_state,
    expected_imported_physical_ids,
    source_recovery_evidence_is_exact,
    validate_import_artifacts,
    validate_import_change_set,
    validate_import_preview_snapshot,
    validate_built_template,
    validate_hardening_function_transition,
    validate_hardening_parameter_transition,
    validate_import_owners,
    validate_lambda_policy_after_update,
    validate_post_import_snapshot,
    validate_snapshot,
    validate_stack_names,
    validate_update_artifacts,
    validate_update_change_set,
    validate_update_rollback_snapshot,
)


class AwsCli:
    """Argument-list AWS CLI adapter; stdout and stderr are never echoed."""

    def json(self, *args: str) -> Any:
        try:
            completed = subprocess.run(["aws", *args, "--output", "json", "--no-cli-pager"], check=True, capture_output=True, text=True)
            return json.loads(completed.stdout or "{}")
        except (subprocess.SubprocessError, json.JSONDecodeError):
            raise AdoptionError("AWS CLI query failed") from None

    def optional_json(self, expected_error: str, *args: str) -> Any | None:
        """Return None only for one explicitly approved AWS error code."""
        try:
            completed = subprocess.run(
                ["aws", *args, "--output", "json", "--no-cli-pager"],
                check=False,
                capture_output=True,
                text=True,
            )
        except subprocess.SubprocessError:
            raise AdoptionError("AWS CLI query failed") from None
        if completed.returncode == 0:
            try:
                return json.loads(completed.stdout or "{}")
            except json.JSONDecodeError:
                raise AdoptionError("AWS CLI query failed") from None
        if expected_error and expected_error in (completed.stderr or ""):
            return None
        raise AdoptionError("AWS CLI query failed") from None

    def run(self, *args: str) -> Any:
        try:
            completed = subprocess.run(["aws", *args, "--output", "json", "--no-cli-pager"], check=True, capture_output=True, text=True)
            return json.loads(completed.stdout or "{}")
        except (subprocess.SubprocessError, json.JSONDecodeError):
            raise AdoptionError("AWS CLI command failed") from None

    def pause(self, seconds: float) -> None:
        """Wait between eventually-consistent absence confirmations."""
        time.sleep(seconds)


@dataclass(frozen=True)
class AuditConfig:
    region: str
    stack: str
    api: str
    authorizer: str
    integration: str
    function: str
    workdir: Path


_SAFE_ENVIRONMENT_NAMES = (
    "REPO_BACKEND", "DYNAMODB_TABLE", "SUBSCRIPTIONS_TABLE",
    "NOTIFICATIONS_TABLE", "CORS_ORIGINS", "TAG_DETECTOR_BACKEND",
)
_RUNTIME_MANAGEMENT_KEYS = ("UpdateRuntimeOn", "RuntimeVersionArn")
_API_GATEWAY_OUTPUT_ONLY_KEYS = {
    "API Gateway integration": {
        "ApiGatewayManaged",
        "IntegrationResponseSelectionExpression",
    },
    "API Gateway route": {"ApiGatewayManaged"},
}
_RESERVATIONS_TABLE_NAME = "PacificBioArchiveUploadReservations"
_HARDENING_ENVIRONMENT_NAMES = (
    "REPO_BACKEND",
    "DYNAMODB_TABLE",
    "RESERVATIONS_TABLE",
    "SUBSCRIPTIONS_TABLE",
    "NOTIFICATIONS_TABLE",
    "STORAGE_BACKEND",
    "STORAGE_DELETE_FUNCTION_NAME",
    "TAG_DETECTOR_BACKEND",
    "QUERY_INPUT_BUCKET",
    "INFERENCE_API_URL",
    "ALLOW_LEGACY_PROCESSING_CALLBACKS",
    "NOTIFICATION_PUBLISHER",
    "SNS_TOPIC_ARN",
    "CORS_ORIGINS",
)
_HARDENING_FUNCTION_FIELDS = (
    "FunctionName",
    "Runtime",
    "Handler",
    "Role",
    "Timeout",
    "MemorySize",
    "Description",
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
    "CodeSha256",
)


class _CloudFormationLoader(yaml.SafeLoader):
    """Load CloudFormation short-form intrinsic tags without executing code."""


_INTRINSIC_NAMES = {
    "And": "Fn::And",
    "Base64": "Fn::Base64",
    "Cidr": "Fn::Cidr",
    "Equals": "Fn::Equals",
    "FindInMap": "Fn::FindInMap",
    "GetAtt": "Fn::GetAtt",
    "GetAZs": "Fn::GetAZs",
    "If": "Fn::If",
    "ImportValue": "Fn::ImportValue",
    "Join": "Fn::Join",
    "Length": "Fn::Length",
    "Not": "Fn::Not",
    "Or": "Fn::Or",
    "Select": "Fn::Select",
    "Split": "Fn::Split",
    "Sub": "Fn::Sub",
    "ToJsonString": "Fn::ToJsonString",
    "Transform": "Fn::Transform",
}


def _construct_cloudformation_intrinsic(
    loader: yaml.SafeLoader,
    tag_suffix: str,
    node: yaml.Node,
) -> dict[str, Any]:
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
        if tag_suffix == "GetAtt":
            logical_id, separator, attribute = value.partition(".")
            if not separator or not logical_id or not attribute:
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    "!GetAtt scalar must use Resource.Attribute form",
                    node.start_mark,
                )
            value = [logical_id, attribute]
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {_INTRINSIC_NAMES.get(tag_suffix, tag_suffix): value}


_CloudFormationLoader.add_multi_constructor(
    "!",
    _construct_cloudformation_intrinsic,
)


def _parse_processed_template(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            try:
                parsed = yaml.load(value, Loader=_CloudFormationLoader)
            except yaml.YAMLError:
                raise AdoptionError(
                    "processed template is not valid JSON or YAML"
                ) from None
        if isinstance(parsed, Mapping):
            return parsed
    raise AdoptionError("processed template is unavailable")


def _sanitized_runtime_management(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    return {key: value[key] for key in _RUNTIME_MANAGEMENT_KEYS if key in value}


def _sanitized_api_gateway_resource(value: Any, resource_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AdoptionError(f"{resource_name} is malformed")
    result = dict(value)
    managed = result.get("ApiGatewayManaged")
    if managed is True:
        raise AdoptionError(f"{resource_name} is managed by API Gateway")
    if managed is not None and managed is not False:
        raise AdoptionError(f"{resource_name} managed state is malformed")
    for key in _API_GATEWAY_OUTPUT_ONLY_KEYS[resource_name]:
        result.pop(key, None)
    return result


def _sanitized_function(configuration: Mapping[str, Any]) -> dict[str, Any]:
    """Derive only names and approved non-secret values, then drop raw env."""
    raw_environment = configuration.get("Environment", {})
    raw_names = (
        raw_environment.get("Names")
        if isinstance(raw_environment, Mapping)
        else None
    )
    raw_variables = (
        raw_environment.get("Variables")
        if isinstance(raw_environment, Mapping)
        else None
    )
    if (
        not isinstance(raw_names, list)
        or not all(isinstance(name, str) and name for name in raw_names)
        or len(raw_names) != len(set(raw_names))
        or not isinstance(raw_variables, Mapping)
        or not set(raw_variables) <= set(_SAFE_ENVIRONMENT_NAMES)
    ):
        raise AdoptionError("function environment is malformed")
    names = sorted(raw_names)
    safe = {
        name: value
        for name, value in raw_variables.items()
        if value is not None
    }
    # Construct from explicit fields only so the complete environment map cannot
    # accidentally become part of the snapshot or an exception.
    result = {key: configuration.get(key) for key in (
        "FunctionName", "Runtime", "Handler", "Role", "Timeout", "MemorySize", "Description", "SnapStart",
        "PackageType", "Architectures", "Layers", "EphemeralStorage", "VpcConfig",
        "FileSystemConfigs", "KmsKeyArn", "DeadLetterConfig", "TracingConfig",
        "LoggingConfig", "CodeSigningConfigArn", "RuntimeManagementConfig",
        "ReservedConcurrentExecutions", "CodeSha256", "RevisionId",
    )}
    result["environment_names"] = names
    result["safe_environment"] = safe
    runtime = result.get("RuntimeManagementConfig")
    if isinstance(runtime, Mapping):
        result["RuntimeManagementConfig"] = _sanitized_runtime_management(runtime)
    snap_start = result.get("SnapStart")
    if isinstance(snap_start, Mapping):
        result["SnapStart"] = {"ApplyOn": snap_start.get("ApplyOn")}
    return result


def _base_snapshot(config: AuditConfig, caller: Mapping[str, Any], function: Mapping[str, Any]) -> dict[str, Any]:
    """Use the audited identities; collection details are sanitized immediately."""
    route_contracts = (
        ("GET /auth-test", "JWT"), ("POST /query/by-tags", "JWT"),
        ("POST /query/by-species", "JWT"), ("GET /query/by-thumbnail", "JWT"),
        ("POST /query/by-file", "JWT"), ("POST /tags/edit", "JWT"),
        ("POST /files/delete", "JWT"), ("POST /notifications/subscribe", "JWT"),
        ("DELETE /notifications/subscribe", "JWT"), ("GET /notifications/subscriptions", "JWT"),
        ("GET /notifications", "JWT"), ("POST /internal/uploads/reserve", "NONE"),
        ("POST /internal/files/{file_id}/processing", "NONE"), ("PUT /internal/files/{file_id}/complete", "NONE"),
        ("PUT /internal/files/{file_id}/failed", "NONE"), ("POST /internal/assets/authorize", "NONE"),
    )
    routes = [{"RouteId": f"route{index:02d}", "RouteKey": key, "Target": f"integrations/{config.integration}", "AuthorizationType": auth, "AuthorizerId": config.authorizer if auth == "JWT" else None} for index, (key, auth) in enumerate(route_contracts, 1)]
    return {
        "caller": {"Arn": caller.get("Arn")}, "region": config.region,
        "stack": {"name": config.stack, "status": "UPDATE_ROLLBACK_COMPLETE", "parameters": ["InternalApiKey"], "template": {"AWSTemplateFormatVersion": "2010-09-09", "Resources": {"FilesTable": {"Type": "AWS::DynamoDB::Table"}, "SubscriptionsTable": {"Type": "AWS::DynamoDB::Table"}, "NotificationsTable": {"Type": "AWS::DynamoDB::Table"}, "QueryLambdaRole": {"Type": "AWS::IAM::Role"}}}, "managed": {"FilesTable": "PacificBioArchiveFiles", "SubscriptionsTable": "PacificBioArchiveSubscriptions", "NotificationsTable": "PacificBioArchiveNotifications", "QueryLambdaRole": "PacificBioArchive-QueryLambdaRole"}},
        "api": {"id": config.api, "stage": {"StageName": "dev", "AutoDeploy": True}, "authorizer": {"AuthorizerId": config.authorizer, "AuthorizerType": "JWT"}, "routes": routes},
        "function": dict(function),
        "integration": {"IntegrationId": config.integration, "IntegrationType": "AWS_PROXY", "IntegrationMethod": "POST", "PayloadFormatVersion": "2.0", "IntegrationUri": f"arn:aws:apigateway:{config.region}:lambda:path/2015-03-31/functions/arn:aws:lambda:{config.region}:111122223333:function:{config.function}/invocations"},
        "type_schemas": {"AWS::Lambda::Function": ["/properties/FunctionName"], "AWS::ApiGatewayV2::Integration": ["/properties/ApiId", "/properties/IntegrationId"], "AWS::ApiGatewayV2::Route": ["/properties/ApiId", "/properties/RouteId"]},
        "import_owners": {
            "ReservationsTable": None,
            "QueryFunction": None,
            "QueryIntegration": None,
            **{logical_id: None for logical_id in ROUTES_BY_LOGICAL_ID},
        },
    }


def _sanitize_audit_errors(operation: Callable[..., Any]) -> Callable[..., Any]:
    """Prevent dependency text from becoming a secret-bearing audit error."""
    @wraps(operation)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return operation(*args, **kwargs)
        except AdoptionError:
            raise
        except Exception:
            raise AdoptionError("AWS audit query failed") from None
    return wrapped


def _canonical(value: Any) -> str:
    """Canonicalize mappings without reordering CloudFormation intrinsic lists."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _semantic_tags(tags: Any) -> set[tuple[str, str]]:
    if not isinstance(tags, list):
        raise AdoptionError("role tags are malformed")
    result = set()
    for tag in tags:
        if not isinstance(tag, Mapping) or not isinstance(tag.get("Key"), str) or not isinstance(tag.get("Value"), str):
            raise AdoptionError("role tags are malformed")
        result.add((tag["Key"], tag["Value"]))
    return result


def _decode_json_document(value: Any, description: str) -> Any:
    if not isinstance(value, str):
        return deepcopy(value)
    candidates = (value, unquote(value))
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise AdoptionError(f"{description} is malformed")


def _normalize_policy_response(value: Any, expected_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AdoptionError("QueryLambdaRole inline policy is malformed")
    policy_name = value.get("PolicyName", expected_name)
    if policy_name != expected_name or "PolicyDocument" not in value:
        raise AdoptionError("QueryLambdaRole inline policy is malformed")
    return {
        "PolicyName": expected_name,
        "PolicyDocument": _decode_json_document(
            value.get("PolicyDocument"),
            "QueryLambdaRole inline policy document",
        ),
    }


def _normalize_drift_value(value: Any) -> Any:
    if value is None:
        return None
    decoded = _decode_json_document(value, "QueryLambdaRole drift value")
    if isinstance(decoded, Mapping) and "PolicyDocument" in decoded:
        decoded = dict(decoded)
        decoded["PolicyDocument"] = _decode_json_document(
            decoded["PolicyDocument"],
            "QueryLambdaRole drift policy document",
        )
    return decoded


def _normalize_role_drift(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AdoptionError("QueryLambdaRole drift evidence is unavailable")
    raw = value.get("StackResourceDrift", value)
    if not isinstance(raw, Mapping):
        raise AdoptionError("QueryLambdaRole drift evidence is unavailable")
    status = raw.get("StackResourceDriftStatus")
    raw_differences = raw.get("PropertyDifferences", [])
    if not isinstance(raw_differences, list):
        raise AdoptionError("QueryLambdaRole drift evidence is malformed")
    differences = []
    for item in raw_differences:
        if not isinstance(item, Mapping):
            raise AdoptionError("QueryLambdaRole drift evidence is malformed")
        differences.append(
            {
                "path": item.get("PropertyPath"),
                "type": item.get("DifferenceType"),
                "expected": _normalize_drift_value(item.get("ExpectedValue")),
                "actual": _normalize_drift_value(item.get("ActualValue")),
            }
        )
    return {"status": status, "differences": differences}


def _collect_reservations_table(
    cli: AwsCli,
    config: AuditConfig,
) -> dict[str, Any]:
    response = cli.json(
        "dynamodb",
        "describe-table",
        "--table-name",
        _RESERVATIONS_TABLE_NAME,
        "--region",
        config.region,
    )
    raw = response.get("Table") if isinstance(response, Mapping) else None
    if not isinstance(raw, Mapping):
        raise AdoptionError("ReservationsTable is unavailable")
    table_arn = raw.get("TableArn")
    if not isinstance(table_arn, str):
        raise AdoptionError("ReservationsTable ARN is unavailable")

    ttl_response = cli.json(
        "dynamodb",
        "describe-time-to-live",
        "--table-name",
        _RESERVATIONS_TABLE_NAME,
        "--region",
        config.region,
    )
    ttl = (
        ttl_response.get("TimeToLiveDescription", {})
        if isinstance(ttl_response, Mapping)
        else {}
    )
    backups_response = cli.json(
        "dynamodb",
        "describe-continuous-backups",
        "--table-name",
        _RESERVATIONS_TABLE_NAME,
        "--region",
        config.region,
    )
    continuous = (
        backups_response.get("ContinuousBackupsDescription", {})
        if isinstance(backups_response, Mapping)
        else {}
    )
    recovery = (
        continuous.get("PointInTimeRecoveryDescription", {})
        if isinstance(continuous, Mapping)
        else {}
    )
    tag_response = cli.json(
        "dynamodb",
        "list-tags-of-resource",
        "--resource-arn",
        table_arn,
        "--region",
        config.region,
    )
    tags = tag_response.get("Tags") if isinstance(tag_response, Mapping) else None
    if not isinstance(tags, list):
        raise AdoptionError("ReservationsTable tags are malformed")

    policy_response = None
    for attempt in range(3):
        policy_response = cli.optional_json(
            "PolicyNotFoundException",
            "dynamodb",
            "get-resource-policy",
            "--resource-arn",
            table_arn,
            "--region",
            config.region,
        )
        if policy_response is not None:
            break
        if attempt < 2:
            cli.pause(15.0)
    if policy_response is None:
        resource_policy = None
    else:
        raw_policy = (
            policy_response.get("Policy")
            if isinstance(policy_response, Mapping)
            else None
        )
        resource_policy = _decode_json_document(
            raw_policy,
            "ReservationsTable resource policy",
        )
        if not isinstance(resource_policy, Mapping):
            raise AdoptionError("ReservationsTable resource policy is malformed")

    kinesis_response = cli.json(
        "dynamodb",
        "describe-kinesis-streaming-destination",
        "--table-name",
        _RESERVATIONS_TABLE_NAME,
        "--region",
        config.region,
    )
    kinesis_destinations = (
        kinesis_response.get("KinesisDataStreamDestinations")
        if isinstance(kinesis_response, Mapping)
        else None
    )
    if not isinstance(kinesis_destinations, list):
        raise AdoptionError("ReservationsTable Kinesis destinations are malformed")

    insights_response = cli.json(
        "dynamodb",
        "describe-contributor-insights",
        "--table-name",
        _RESERVATIONS_TABLE_NAME,
        "--region",
        config.region,
    )
    insights_status = (
        insights_response.get("ContributorInsightsStatus")
        if isinstance(insights_response, Mapping)
        else None
    )
    if not isinstance(insights_status, str):
        raise AdoptionError("ReservationsTable Contributor Insights is malformed")

    sse = raw.get("SSEDescription")
    if sse in (None, {}):
        sse_mode = "AWS_OWNED"
    elif (
        isinstance(sse, Mapping)
        and sse.get("Status") == "ENABLED"
        and sse.get("SSEType") == "AES256"
        and not sse.get("KMSMasterKeyArn")
    ):
        sse_mode = "AWS_OWNED"
    else:
        sse_mode = "NON_DEFAULT"
    billing_summary = raw.get("BillingModeSummary", {})
    billing_mode = (
        billing_summary.get("BillingMode")
        if isinstance(billing_summary, Mapping)
        else None
    )
    if billing_mode is None:
        billing_mode = raw.get("BillingMode")
    table_class_summary = raw.get("TableClassSummary", {})
    table_class = (
        table_class_summary.get("TableClass", "STANDARD")
        if isinstance(table_class_summary, Mapping)
        else None
    )
    stream = raw.get("StreamSpecification")
    if stream == {"StreamEnabled": False}:
        stream = None
    return {
        "TableName": raw.get("TableName"),
        "TableStatus": raw.get("TableStatus"),
        "TableArn": table_arn,
        "BillingMode": billing_mode,
        "AttributeDefinitions": deepcopy(raw.get("AttributeDefinitions", [])),
        "KeySchema": deepcopy(raw.get("KeySchema", [])),
        "GlobalSecondaryIndexes": deepcopy(raw.get("GlobalSecondaryIndexes", [])),
        "LocalSecondaryIndexes": deepcopy(raw.get("LocalSecondaryIndexes", [])),
        "StreamSpecification": deepcopy(stream),
        "DeletionProtectionEnabled": raw.get("DeletionProtectionEnabled", False),
        "TableClass": table_class,
        "Replicas": deepcopy(raw.get("Replicas", [])),
        "Tags": deepcopy(tags),
        "TimeToLiveStatus": ttl.get("TimeToLiveStatus"),
        "PointInTimeRecoveryStatus": recovery.get(
            "PointInTimeRecoveryStatus"
        ),
        "SSEMode": sse_mode,
        "OnDemandThroughput": deepcopy(raw.get("OnDemandThroughput")),
        "WarmThroughput": deepcopy(raw.get("WarmThroughput")),
        "MultiRegionConsistency": raw.get("MultiRegionConsistency"),
        "ResourcePolicy": deepcopy(resource_policy),
        "KinesisDataStreamDestinations": deepcopy(kinesis_destinations),
        "ContributorInsightsStatus": insights_status,
        "VectorIndexes": deepcopy(raw.get("VectorIndexes", [])),
        "GlobalTableWitnesses": deepcopy(raw.get("GlobalTableWitnesses", [])),
    }


def _collect_paginated_items(
    cli: AwsCli,
    operation: str,
    result_key: str,
    *arguments: str,
) -> list[Mapping[str, Any]]:
    """Collect every CloudFormation page or reject incomplete evidence."""
    items: list[Mapping[str, Any]] = []
    token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        command = ["cloudformation", operation, *arguments]
        if token is not None:
            command.extend(("--starting-token", token))
        page = cli.json(*command)
        if (
            not isinstance(page, Mapping)
            or result_key not in page
            or not isinstance(page[result_key], list)
            or not all(isinstance(item, Mapping) for item in page[result_key])
        ):
            raise AdoptionError(
                f"CloudFormation {operation} owner evidence is malformed"
            )
        items.extend(deepcopy(page[result_key]))
        next_token = page.get("NextToken")
        if next_token is None:
            return items
        if (
            not isinstance(next_token, str)
            or not next_token
            or next_token in seen_tokens
        ):
            raise AdoptionError(
                f"CloudFormation {operation} pagination evidence is malformed"
            )
        seen_tokens.add(next_token)
        token = next_token


def _active_stack_statuses(
    summaries: list[Mapping[str, Any]],
    malformed_message: str,
) -> dict[str, str]:
    """Validate all summaries, then ignore retained deleted history."""
    active: dict[str, str] = {}
    for summary in summaries:
        if not isinstance(summary, Mapping):
            raise AdoptionError(malformed_message)
        name = summary.get("StackName")
        status = summary.get("StackStatus")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(status, str)
            or not status
        ):
            raise AdoptionError(malformed_message)
        if status == "DELETE_COMPLETE":
            continue
        if name in active:
            raise AdoptionError(malformed_message)
        active[name] = status
    return active


def _collect_provisioned_concurrency(
    cli: AwsCli,
    config: AuditConfig,
) -> list[dict[str, Any]]:
    """Collect every Lambda provisioned-concurrency page canonically."""
    configurations: list[dict[str, Any]] = []
    marker: str | None = None
    seen_markers: set[str] = set()
    seen_arns: set[str] = set()
    required_keys = {
        "FunctionArn",
        "RequestedProvisionedConcurrentExecutions",
        "AvailableProvisionedConcurrentExecutions",
        "AllocatedProvisionedConcurrentExecutions",
        "Status",
        "LastModified",
    }
    allowed_keys = required_keys | {"StatusReason"}
    while True:
        command = [
            "lambda",
            "list-provisioned-concurrency-configs",
            "--function-name",
            config.function,
            "--region",
            config.region,
        ]
        if marker is not None:
            command.extend(("--marker", marker))
        page = cli.json(*command)
        raw_items = (
            page.get("ProvisionedConcurrencyConfigs")
            if isinstance(page, Mapping)
            else None
        )
        if not isinstance(raw_items, list):
            raise AdoptionError(
                "Lambda provisioned concurrency evidence is malformed"
            )
        for raw in raw_items:
            if (
                not isinstance(raw, Mapping)
                or not required_keys <= set(raw)
                or not set(raw) <= allowed_keys
            ):
                raise AdoptionError(
                    "Lambda provisioned concurrency evidence is malformed"
                )
            function_arn = raw.get("FunctionArn")
            if (
                not isinstance(function_arn, str)
                or not function_arn
                or function_arn in seen_arns
            ):
                raise AdoptionError(
                    "Lambda provisioned concurrency evidence is malformed"
                )
            seen_arns.add(function_arn)
            configurations.append(deepcopy(dict(raw)))
        next_marker = page.get("NextMarker")
        if next_marker is None:
            return sorted(configurations, key=lambda item: item["FunctionArn"])
        if (
            not isinstance(next_marker, str)
            or not next_marker
            or next_marker in seen_markers
        ):
            raise AdoptionError(
                "Lambda provisioned concurrency pagination evidence is malformed"
            )
        seen_markers.add(next_marker)
        marker = next_marker


@_sanitize_audit_errors
def collect_snapshot(
    cli: AwsCli,
    config: AuditConfig,
    *,
    ownership_phase: str = "pre",
) -> dict[str, Any]:
    if ownership_phase not in {
        "pre",
        "preview",
        "post",
        "update-rollback",
    }:
        raise AdoptionError("snapshot ownership phase is invalid")
    caller = cli.json("sts", "get-caller-identity", "--region", config.region)
    arn = caller.get("Arn") if isinstance(caller, Mapping) else None
    account = caller.get("Account") if isinstance(caller, Mapping) else None
    if arn == f"arn:aws:iam::{account}:root":
        raise AdoptionError("Root caller is not permitted")
    if not isinstance(account, str) or arn != f"arn:aws:iam::{account}:user/fit5225-cli-deployer":
        raise AdoptionError("caller must be exact IAM user/fit5225-cli-deployer")
    stacks = cli.json(
        "cloudformation",
        "describe-stacks",
        "--stack-name",
        config.stack,
        "--region",
        config.region,
        "--query",
        "Stacks[0].{StackName:StackName,StackStatus:StackStatus}",
    )
    if (
        not isinstance(stacks, Mapping)
        or stacks.get("StackName") != config.stack
        or not isinstance(stacks.get("StackStatus"), str)
    ):
        raise AdoptionError("stack identity could not be verified")
    stack_view = stacks
    processed_response = cli.json("cloudformation", "get-template", "--stack-name", config.stack, "--template-stage", "Processed", "--region", config.region)
    template_body = (
        processed_response.get("TemplateBody")
        if isinstance(processed_response, Mapping)
        else None
    )
    template = _parse_processed_template(template_body)
    resource_summaries = _collect_paginated_items(
        cli,
        "list-stack-resources",
        "StackResourceSummaries",
        "--stack-name",
        config.stack,
        "--region",
        config.region,
    )
    managed: dict[str, str] = {}
    for item in resource_summaries:
        logical_id = item.get("LogicalResourceId")
        physical_id = item.get("PhysicalResourceId")
        if (
            not isinstance(logical_id, str)
            or not logical_id
            or not isinstance(physical_id, str)
            or not physical_id
            or logical_id in managed
        ):
            raise AdoptionError("source stack resource evidence is malformed")
        managed[logical_id] = physical_id
    expected_managed = {"FilesTable", "SubscriptionsTable", "NotificationsTable", "QueryLambdaRole"}
    if set(managed) != expected_managed:
        raise AdoptionError("managed resource set mismatch")
    active_summaries = _collect_paginated_items(
        cli,
        "list-stacks",
        "StackSummaries",
        "--region",
        config.region,
    )
    active_stacks = _active_stack_statuses(
        active_summaries,
        "CloudFormation stack owner evidence is malformed",
    )
    if active_stacks.get(config.stack) != stack_view.get("StackStatus"):
        raise AdoptionError("source stack owner evidence is incomplete")

    physical_owners: dict[str, set[str]] = {}
    target_resources: dict[str, dict[str, str]] = {}
    for stack_name in active_stacks:
        stack_resources = (
            resource_summaries
            if stack_name == config.stack
            else _collect_paginated_items(
                cli,
                "list-stack-resources",
                "StackResourceSummaries",
                "--stack-name",
                stack_name,
                "--region",
                config.region,
            )
        )
        stack_logical_ids: set[str] = set()
        for resource in stack_resources:
            logical_id = resource.get("LogicalResourceId")
            physical_id = resource.get("PhysicalResourceId")
            if (
                not isinstance(logical_id, str)
                or not logical_id
                or logical_id in stack_logical_ids
                or not isinstance(physical_id, str)
                or not physical_id
            ):
                raise AdoptionError(
                    "CloudFormation stack resource owner evidence is malformed"
                )
            stack_logical_ids.add(logical_id)
            physical_owners.setdefault(physical_id, set()).add(stack_name)
            if stack_name == TARGET_STACK_NAME:
                resource_type = resource.get("ResourceType")
                if not isinstance(resource_type, str) or not resource_type:
                    raise AdoptionError(
                        "target stack resource type evidence is malformed"
                    )
                target_resources[logical_id] = {
                    "physical_id": physical_id,
                    "resource_type": resource_type,
                }
    reservations_table = _collect_reservations_table(cli, config)
    fields = ["FunctionName", "Runtime", "Handler", "Role", "Timeout", "MemorySize", "Description", "SnapStart", "PackageType", "Architectures", "Layers", "EphemeralStorage", "VpcConfig", "FileSystemConfigs", "KmsKeyArn", "DeadLetterConfig", "TracingConfig", "LoggingConfig", "CodeSigningConfigArn", "RuntimeManagementConfig", "ReservedConcurrentExecutions", "CodeSha256", "RevisionId"]
    safe_variables = ",".join(
        f"{name}: Environment.Variables.{name}"
        for name in sorted(_SAFE_ENVIRONMENT_NAMES)
    )
    query = (
        "{"
        + ",".join(f"{field}: {field}" for field in fields)
        + ",Environment: {Names: keys(Environment.Variables),Variables: {"
        + safe_variables
        + "}}}"
    )
    configuration = cli.json("lambda", "get-function-configuration", "--function-name", config.function, "--region", config.region, "--query", query)
    function = _sanitized_function(configuration)
    function_arn = configuration.get("FunctionArn")
    if not isinstance(function_arn, str):
        function_arn = f"arn:aws:lambda:{config.region}:{caller.get('Account')}:function:{config.function}"
    tag_response = cli.json("lambda", "list-tags", "--resource", function_arn, "--region", config.region)
    tags = tag_response.get("Tags") if isinstance(tag_response, Mapping) else None
    if not isinstance(tags, Mapping) or not all(isinstance(key, str) and isinstance(value, str) for key, value in tags.items()):
        raise AdoptionError("Lambda tags are malformed")
    function["Tags"] = dict(tags)
    policy_response = cli.json("lambda", "get-policy", "--function-name", config.function, "--region", config.region)
    try:
        function["resource_policy"] = json.loads(policy_response["Policy"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise AdoptionError("Lambda resource policy is unavailable") from None
    policy_revision = (
        policy_response.get("RevisionId")
        if isinstance(policy_response, Mapping)
        else None
    )
    if not isinstance(policy_revision, str) or not policy_revision:
        raise AdoptionError("Lambda resource policy revision is unavailable")
    function["resource_policy_revision_id"] = policy_revision
    concurrency = cli.json("lambda", "get-function-concurrency", "--function-name", config.function, "--region", config.region)
    function["ReservedConcurrentExecutions"] = concurrency.get("ReservedConcurrentExecutions") if isinstance(concurrency, Mapping) else None
    function["provisioned_concurrency"] = _collect_provisioned_concurrency(
        cli,
        config,
    )
    runtime_management = cli.json("lambda", "get-runtime-management-config", "--function-name", config.function, "--region", config.region)
    if isinstance(runtime_management, Mapping) and runtime_management:
        function["RuntimeManagementConfig"] = _sanitized_runtime_management(runtime_management)
    role_name = str(function.get("Role", "")).rsplit("/", 1)[-1]
    if managed.get("QueryLambdaRole") != role_name:
        raise AdoptionError("QueryLambdaRole physical identity differs from Lambda role")
    role_response = cli.json("iam", "get-role", "--role-name", role_name)
    role = role_response.get("Role") if isinstance(role_response, Mapping) else None
    if not isinstance(role, Mapping):
        raise AdoptionError("stack-owned role is unavailable")
    if role.get("RoleName") != role_name:
        raise AdoptionError("IAM role identity differs from Lambda role")
    attached = cli.json("iam", "list-attached-role-policies", "--role-name", role_name).get("AttachedPolicies", [])
    inline_names = cli.json("iam", "list-role-policies", "--role-name", role_name).get("PolicyNames", [])
    if not isinstance(inline_names, list) or not all(
        isinstance(name, str) and name for name in inline_names
    ):
        raise AdoptionError("QueryLambdaRole inline policy names are malformed")
    inline = {
        name: _normalize_policy_response(
            cli.json(
                "iam",
                "get-role-policy",
                "--role-name",
                role_name,
                "--policy-name",
                name,
            ),
            name,
        )
        for name in inline_names
    }
    tags = cli.json("iam", "list-role-tags", "--role-name", role_name).get("Tags", [])
    drift = _normalize_role_drift(
        cli.json(
            "cloudformation",
            "detect-stack-resource-drift",
            "--stack-name",
            config.stack,
            "--logical-resource-id",
            "QueryLambdaRole",
            "--region",
            config.region,
        )
    )
    trust_policy = _decode_json_document(
        role.get("AssumeRolePolicyDocument"),
        "QueryLambdaRole trust policy",
    )
    if not isinstance(trust_policy, Mapping):
        raise AdoptionError("QueryLambdaRole trust policy is malformed")
    role_view = {
        "role_name": role.get("RoleName"),
        "account": account,
        "region": config.region,
        "path": role.get("Path"),
        "trust_policy": trust_policy,
        "permissions_boundary": role.get("PermissionsBoundary"),
        "managed_policies": attached,
        "inline_policies": inline,
        "tags": tags,
        "drift": drift,
    }
    processed_role = template.get("Resources", {}).get("QueryLambdaRole", {})
    processed_properties = processed_role.get("Properties", {}) if isinstance(processed_role, Mapping) else {}
    if processed_properties.get("RoleName") != role_name:
        raise AdoptionError("processed QueryLambdaRole identity differs from Lambda role")
    checks = {"Path": role_view["path"], "AssumeRolePolicyDocument": role_view["trust_policy"], "PermissionsBoundary": role_view["permissions_boundary"]}
    for key, live_value in checks.items():
        if key == "Path" and key not in processed_properties and live_value == "/":
            continue
        if (key in processed_properties and _canonical(processed_properties[key]) != _canonical(live_value)) or (live_value not in (None, {}, [], "") and key not in processed_properties):
            raise AdoptionError("QueryLambdaRole live definition differs from processed template")
    live_arns = {policy.get("PolicyArn") for policy in attached}
    if (live_arns and "ManagedPolicyArns" not in processed_properties) or ("ManagedPolicyArns" in processed_properties and {_canonical(value) for value in processed_properties["ManagedPolicyArns"]} != {_canonical(value) for value in live_arns}):
        raise AdoptionError("QueryLambdaRole managed policies differ from processed template")
    if _semantic_tags(processed_properties.get("Tags", [])) != _semantic_tags(tags):
        raise AdoptionError("QueryLambdaRole tags differ from processed template")
    role_view["processed_definition"] = processed_role
    integration = _sanitized_api_gateway_resource(
        cli.json("apigatewayv2", "get-integration", "--api-id", config.api, "--integration-id", config.integration, "--region", config.region),
        "API Gateway integration",
    )
    routes_response = cli.json("apigatewayv2", "get-routes", "--api-id", config.api, "--region", config.region)
    raw_routes = routes_response.get("Items") if isinstance(routes_response, Mapping) else None
    if not isinstance(raw_routes, list):
        raise AdoptionError("API Gateway routes are malformed")
    routes = [
        _sanitized_api_gateway_resource(route, "API Gateway route")
        for route in raw_routes
    ]
    stage = cli.json("apigatewayv2", "get-stage", "--api-id", config.api, "--stage-name", "dev", "--region", config.region)
    authorizers = cli.json("apigatewayv2", "get-authorizers", "--api-id", config.api, "--region", config.region).get("Items", [])
    authorizer = next((item for item in authorizers if item.get("AuthorizerId") == config.authorizer), None)
    if not isinstance(authorizer, Mapping):
        raise AdoptionError("API authorizer is unavailable")
    candidate_physical_ids = {
        "ReservationsTable": _RESERVATIONS_TABLE_NAME,
        "QueryFunction": config.function,
        "QueryIntegration": config.integration,
    }
    for logical_id, contract in ROUTES_BY_LOGICAL_ID.items():
        matching_routes = [
            route for route in routes if route.get("RouteKey") == contract.route_key
        ]
        route_id = (
            matching_routes[0].get("RouteId")
            if len(matching_routes) == 1
            else None
        )
        if not isinstance(route_id, str) or not route_id:
            raise AdoptionError(
                f"owner evidence is missing route {contract.route_key}"
            )
        candidate_physical_ids[logical_id] = route_id
    import_owners: dict[str, str | None] = {}
    for logical_id, physical_id in candidate_physical_ids.items():
        owners = physical_owners.get(physical_id, set())
        if len(owners) > 1:
            raise AdoptionError(
                f"duplicate resource owner evidence for {logical_id}"
            )
        import_owners[logical_id] = next(iter(owners)) if owners else None
    schemas: dict[str, Any] = {}
    for resource_type in (
        "AWS::DynamoDB::Table",
        "AWS::Lambda::Function",
        "AWS::ApiGatewayV2::Integration",
        "AWS::ApiGatewayV2::Route",
    ):
        type_response = cli.json("cloudformation", "describe-type", "--type", "RESOURCE", "--type-name", resource_type, "--region", config.region)
        schema = type_response.get("Schema") if isinstance(type_response, Mapping) else None
        try:
            schemas[resource_type] = json.loads(schema)["primaryIdentifier"] if isinstance(schema, str) else schema["primaryIdentifier"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise AdoptionError("primary identifier schema is unavailable") from None
    sanitized_template = deepcopy(dict(template))
    declared_parameters = sanitized_template.get("Parameters")
    if isinstance(declared_parameters, Mapping):
        sanitized_parameters = {
            key: deepcopy(value)
            for key, value in declared_parameters.items()
            if key != "InternalApiKey"
        }
        if sanitized_parameters:
            sanitized_template["Parameters"] = sanitized_parameters
        else:
            sanitized_template.pop("Parameters", None)
    snapshot = {
        "caller": {"Arn": arn, "Account": caller.get("Account")}, "region": config.region,
        "stack": {"name": config.stack, "status": stack_view.get("StackStatus"), "parameters": [], "template": sanitized_template, "managed": managed},
        "api": {"id": config.api, "stage": dict(stage), "authorizer": dict(authorizer), "routes": routes},
        "function": function,
        "integration": dict(integration),
        "reservations_table": reservations_table,
        "type_schemas": schemas,
        "import_owners": import_owners, "role": role_view,
        "target_stack": {
            "name": TARGET_STACK_NAME,
            "status": active_stacks.get(TARGET_STACK_NAME),
            "resources": target_resources,
        },
    }
    validators = {
        "pre": validate_snapshot,
        "preview": validate_import_preview_snapshot,
        "post": validate_post_import_snapshot,
        "update-rollback": validate_update_rollback_snapshot,
    }
    validators[ownership_phase](snapshot)
    return snapshot


@_sanitize_audit_errors
def collect_stack_parameter_names(
    cli: AwsCli,
    stack: str,
    region: str,
) -> set[str]:
    """Read only parameter names so a NoEcho value never enters this process."""
    response = cli.json(
        "cloudformation",
        "describe-stacks",
        "--stack-name",
        stack,
        "--region",
        region,
        "--query",
        "Stacks[0].{StackName:StackName,"
        "ParameterNames:Parameters[].ParameterKey}",
    )
    if (
        not isinstance(response, Mapping)
        or response.get("StackName") != stack
    ):
        raise AdoptionError("stack parameter names are unavailable or duplicated")
    names = response.get("ParameterNames")
    if names is None:
        names = []
    if (
        not isinstance(names, list)
        or not all(isinstance(name, str) and name for name in names)
        or len(names) != len(set(names))
    ):
        raise AdoptionError("stack parameter names are unavailable or duplicated")
    return set(names)


@_sanitize_audit_errors
def collect_recovery_ownership(
    cli: AwsCli,
    region: str,
    source_stack: str,
    target_stack: str,
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    """Collect sanitized ownership only; never inspect runtime or secrets."""
    validate_stack_names(source_stack, target_stack)
    validate_snapshot(baseline)
    adoption_expected = {
        logical_id: dict(evidence)
        for logical_id, evidence in expected_imported_physical_ids(
            baseline
        ).items()
    }
    summaries = _collect_paginated_items(
        cli,
        "list-stacks",
        "StackSummaries",
        "--region",
        region,
    )
    active = _active_stack_statuses(
        summaries,
        "recovery stack evidence is malformed",
    )
    owners_by_physical_id: dict[str, set[str]] = {}
    source_resources: dict[str, dict[str, str]] = {}
    target_resources: dict[str, dict[str, str]] = {}
    for stack_name in active:
        resources = _collect_paginated_items(
            cli,
            "list-stack-resources",
            "StackResourceSummaries",
            "--stack-name",
            stack_name,
            "--region",
            region,
        )
        seen_logical_ids: set[str] = set()
        for resource in resources:
            logical_id = resource.get("LogicalResourceId")
            physical_id = resource.get("PhysicalResourceId")
            if (
                not isinstance(logical_id, str)
                or not logical_id
                or logical_id in seen_logical_ids
                or not isinstance(physical_id, str)
                or not physical_id
            ):
                raise AdoptionError("recovery resource evidence is malformed")
            seen_logical_ids.add(logical_id)
            owners_by_physical_id.setdefault(physical_id, set()).add(stack_name)
            if stack_name in {source_stack, target_stack}:
                resource_type = resource.get("ResourceType")
                if not isinstance(resource_type, str) or not resource_type:
                    raise AdoptionError(
                        "stack recovery resource type evidence is malformed"
                    )
                evidence = {
                    "physical_id": physical_id,
                    "resource_type": resource_type,
                }
                if stack_name == source_stack:
                    source_resources[logical_id] = evidence
                else:
                    target_resources[logical_id] = evidence

    import_owners: dict[str, str | None] = {}
    for logical_id, evidence in adoption_expected.items():
        owners = owners_by_physical_id.get(evidence["physical_id"], set())
        if len(owners) > 1:
            raise AdoptionError(
                f"duplicate recovery owner evidence for {logical_id}"
            )
        import_owners[logical_id] = next(iter(owners)) if owners else None
    return {
        "source_stack": {
            "name": source_stack,
            "status": active.get(source_stack),
            "resources": source_resources,
        },
        "target_stack": {
            "name": target_stack,
            "status": active.get(target_stack),
            "resources": target_resources,
        },
        "import_owners": import_owners,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def run_audit(cli: AwsCli, config: AuditConfig, baseline: Path | None = None) -> Path:
    snapshot = collect_snapshot(cli, config)
    if baseline is not None:
        try:
            previous = json.loads(baseline.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AdoptionError("baseline snapshot is unavailable") from None
        assert_runtime_unchanged(previous, snapshot)
    config.workdir.mkdir(parents=True, exist_ok=True)
    path = config.workdir / "sanitized-snapshot.json"
    path.write_text(json.dumps(_json_safe(snapshot), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def run_prepare(cli: AwsCli, config: AuditConfig, artifact_bucket: str, downloader: Callable[[str, Path], None]) -> list[Path]:
    """Create only deterministic, sanitized operator-review files."""
    snapshot = collect_snapshot(cli, config)
    code_response = cli.json("lambda", "get-function", "--function-name", config.function, "--region", config.region, "--query", "Code")
    code = code_response if isinstance(code_response, Mapping) else None
    if isinstance(code, Mapping) and isinstance(code.get("Code"), Mapping):
        # Accept old wrapper-shaped fakes/clients, while matching the real
        # `--query Code` AWS CLI response (the Code object itself).
        code = code["Code"]
    if not isinstance(code, Mapping):
        raise AdoptionError("Lambda package location is unavailable")
    artifact = backup_function_package(cli, {"Location": code.get("Location"), "CodeSha256": snapshot["function"]["CodeSha256"]}, artifact_bucket, downloader, config.region)
    config.workdir.mkdir(parents=True, exist_ok=True)
    files = {
        "sanitized-snapshot.json": _json_safe(snapshot),
        "import-template.json": build_import_template(snapshot, artifact),
        "resources-to-import.json": build_resources_to_import(snapshot),
        "import-parameters.json": build_parameters_to_reuse(snapshot),
    }
    paths: list[Path] = []
    for name, value in files.items():
        path = config.workdir / name
        path.write_text(json.dumps(_json_safe(value), sort_keys=True, indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def production_downloader(url: str, destination: Path) -> None:
    """Stream a presigned URL to disk without ever logging its value."""
    try:
        with urlopen(url) as response, destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except Exception as error:
        raise AdoptionError("Lambda package download failed") from None


def verify_artifact_bucket(cli: AwsCli, artifact_bucket: str, region: str = "ap-southeast-2") -> None:
    try:
        owned = cli.json("s3api", "list-buckets")
        if artifact_bucket not in {item.get("Name") for item in owned.get("Buckets", []) if isinstance(item, Mapping)}:
            raise AdoptionError("artifact bucket is unsafe")
        location = cli.json("s3api", "get-bucket-location", "--bucket", artifact_bucket)
        raw_location = location.get("LocationConstraint")
        expected_location = "us-east-1" if raw_location is None else ("eu-west-1" if raw_location == "EU" else raw_location)
        public = cli.json("s3api", "get-public-access-block", "--bucket", artifact_bucket)["PublicAccessBlockConfiguration"]
        encryption = cli.json("s3api", "get-bucket-encryption", "--bucket", artifact_bucket)
        versioning = cli.json("s3api", "get-bucket-versioning", "--bucket", artifact_bucket)
        cli.json("s3api", "get-bucket-ownership-controls", "--bucket", artifact_bucket)
        policy_status = cli.optional_json(
            "NoSuchBucketPolicy",
            "s3api",
            "get-bucket-policy-status",
            "--bucket",
            artifact_bucket,
        )
        cli.json("s3api", "head-bucket", "--bucket", artifact_bucket)
        if expected_location != region or (policy_status is not None and policy_status.get("PolicyStatus", {}).get("IsPublic") is not False) or not all(public.get(name) is True for name in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")) or not encryption.get("ServerSideEncryptionConfiguration") or versioning.get("Status") != "Enabled":
            raise AdoptionError("artifact bucket is unsafe")
    except (KeyError, AdoptionError, Exception) as error:
        if isinstance(error, AdoptionError):
            raise
        raise AdoptionError("artifact bucket is unsafe") from None


def backup_function_package(cli: AwsCli, code: Mapping[str, str], artifact_bucket: str, downloader: Callable[[str, Path], None], region: str = "ap-southeast-2") -> CodeArtifact:
    location, expected_digest = code.get("Location"), code.get("CodeSha256")
    if not isinstance(location, str) or not isinstance(expected_digest, str):
        raise AdoptionError("Lambda code metadata is incomplete")
    verify_artifact_bucket(cli, artifact_bucket, region)
    with TemporaryDirectory() as directory:
        package_path = Path(directory) / "query-function.zip"
        try:
            downloader(location, package_path)
        except Exception:
            raise AdoptionError("Lambda package download failed") from None
        package = package_path.read_bytes()
        raw_digest = hashlib.sha256(package).digest()
        base64_digest = base64.b64encode(raw_digest).decode("ascii")
        if not hmac.compare_digest(base64_digest, expected_digest):
            raise AdoptionError("Lambda package SHA-256 does not match live code")
        try:
            with ZipFile(package_path) as archive:
                if archive.testzip() is not None:
                    raise AdoptionError("Lambda package zip is corrupt")
        except BadZipFile as error:
            raise AdoptionError("Lambda package is not a zip") from None
        artifact_key = f"member-d/adoption/{raw_digest.hex()}.zip"
        result = cli.run("s3api", "put-object", "--bucket", artifact_bucket, "--key", artifact_key, "--body", str(package_path), "--checksum-algorithm", "SHA256", "--checksum-sha256", base64_digest, "--server-side-encryption", "AES256")
        version_id = result.get("VersionId") if isinstance(result, Mapping) else None
        if not version_id:
            raise AdoptionError("uploaded artifact has no version ID")
        uploaded = cli.json("s3api", "head-object", "--bucket", artifact_bucket, "--key", artifact_key, "--version-id", version_id, "--checksum-mode", "ENABLED")
        if not hmac.compare_digest(str(uploaded.get("ChecksumSHA256", "")), base64_digest):
            raise AdoptionError("uploaded checksum does not match Lambda package")
        return CodeArtifact(bucket=artifact_bucket, key=artifact_key, version_id=version_id)


def _write_deterministic_zip(
    source: Path,
    destination: Path,
    included_files: Mapping[str, Path] | None = None,
) -> tuple[str, str]:
    """Archive exactly one SAM build tree and return hex/base64 SHA-256."""
    if not source.is_dir():
        raise AdoptionError("SAM built QueryFunction directory is unavailable")
    selected = (
        dict(included_files)
        if included_files is not None
        else _regular_files(source, "SAM built QueryFunction directory")
    )
    files: list[tuple[Path, str]] = []
    total_size = 0
    try:
        source_resolved = source.resolve()
        for relative, path in selected.items():
            if (
                not relative
                or relative.startswith("/")
                or any(part in {"", ".", ".."} for part in relative.split("/"))
                or path.is_symlink()
                or not path.is_file()
                or path.resolve().relative_to(source_resolved).as_posix() != relative
            ):
                raise AdoptionError("SAM built QueryFunction path is unsafe")
            size = path.stat().st_size
            total_size += size
            if total_size > 250 * 1024 * 1024:
                raise AdoptionError("SAM built QueryFunction exceeds Lambda size limit")
            files.append((path, relative))
    except (OSError, ValueError):
        raise AdoptionError("SAM built QueryFunction cannot be read") from None
    if not files:
        raise AdoptionError("SAM built QueryFunction is empty")
    files.sort(key=lambda item: item[1])
    try:
        with ZipFile(
            destination,
            "w",
            compression=ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for path, relative in files:
                info = ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.create_system = 3
                archive.writestr(info, path.read_bytes(), compresslevel=9)
        package = destination.read_bytes()
    except OSError:
        raise AdoptionError("SAM built QueryFunction cannot be packaged") from None
    if len(package) > 50 * 1024 * 1024:
        raise AdoptionError("SAM built QueryFunction zip exceeds direct upload limit")
    raw_digest = hashlib.sha256(package).digest()
    return raw_digest.hex(), base64.b64encode(raw_digest).decode("ascii")


def _regular_files(root: Path, description: str) -> dict[str, Path]:
    if not root.is_dir():
        raise AdoptionError(f"{description} is unavailable")
    result: dict[str, Path] = {}
    try:
        for path in root.rglob("*"):
            if path.is_symlink():
                raise AdoptionError(f"{description} contains a symlink")
            if path.is_dir():
                continue
            if not path.is_file():
                raise AdoptionError(f"{description} contains an unsafe entry")
            relative = path.relative_to(root).as_posix()
            if (
                not relative
                or relative.startswith("/")
                or any(part in {"", ".", ".."} for part in relative.split("/"))
            ):
                raise AdoptionError(f"{description} path is unsafe")
            result[relative] = path
    except OSError:
        raise AdoptionError(f"{description} cannot be read") from None
    return result


def _git_tracked_source_files(
    source: Path, expected_commit: str
) -> dict[str, Path]:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise AdoptionError("deployment commit is malformed")
    try:
        root_result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
        repository = Path(root_result.stdout.strip()).resolve()
        source_resolved = source.resolve()
        source_relative = source_resolved.relative_to(repository).as_posix()
        head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                source_relative,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        tracked = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "ls-files",
                "-z",
                "--",
                source_relative,
            ],
            check=True,
            capture_output=True,
        ).stdout.split(b"\0")
    except (OSError, subprocess.SubprocessError, ValueError):
        raise AdoptionError("repository source identity cannot be verified") from None
    if head != expected_commit or status:
        raise AdoptionError("repository source differs from the approved commit")
    result: dict[str, Path] = {}
    prefix = source_relative.rstrip("/") + "/"
    for raw in tracked:
        if not raw:
            continue
        try:
            repository_relative = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise AdoptionError("repository source path is malformed") from None
        if not repository_relative.startswith(prefix):
            raise AdoptionError("repository source path escaped its directory")
        relative = repository_relative[len(prefix) :]
        path = repository / PurePosixPath(repository_relative)
        if path.is_symlink() or not path.is_file():
            raise AdoptionError("repository source contains an unsafe entry")
        result[relative] = path
    if not result:
        raise AdoptionError("repository source has no tracked files")
    return result


def _verify_committed_file(path: Path, expected_commit: str) -> None:
    """Bind a repository file's bytes to one clean, full Git commit."""
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise AdoptionError("deployment commit is malformed")
    try:
        resolved = path.resolve()
        repository = Path(
            subprocess.run(
                ["git", "-C", str(resolved.parent), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        ).resolve()
        relative = resolved.relative_to(repository).as_posix()
        head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                relative,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        subprocess.run(
            ["git", "-C", str(repository), "ls-files", "--error-unmatch", "--", relative],
            check=True,
            capture_output=True,
        )
        committed = subprocess.run(
            ["git", "-C", str(repository), "show", f"{expected_commit}:{relative}"],
            check=True,
            capture_output=True,
        ).stdout
        working = resolved.read_bytes()
    except (OSError, subprocess.SubprocessError, ValueError):
        raise AdoptionError("repository file identity cannot be verified") from None
    if head != expected_commit or status or not hmac.compare_digest(working, committed):
        raise AdoptionError("repository file differs from the approved commit")


_DEPENDENCY_GENERATED_EXCLUSIONS = (
    "bin/**",
    "*.dist-info/RECORD",
    "*.dist-info/INSTALLER",
    "*.dist-info/REQUESTED",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    ".pytest_cache/**",
    "data/pacific_bioarchive.db",
)


def _trusted_dependency_files(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if (
        not isinstance(manifest, Mapping)
        or set(manifest)
        != {
            "schema",
            "runtime",
            "architecture",
            "generated_files_excluded",
            "files",
        }
        or manifest.get("schema") != 1
        or manifest.get("runtime") != "python3.12"
        or manifest.get("architecture") != "x86_64"
        or tuple(manifest.get("generated_files_excluded", ()))
        != _DEPENDENCY_GENERATED_EXCLUSIONS
        or not isinstance(manifest.get("files"), Mapping)
    ):
        raise AdoptionError("trusted dependency manifest is malformed")
    result: dict[str, Mapping[str, Any]] = {}
    for relative, evidence in manifest["files"].items():
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or not isinstance(evidence, Mapping)
            or set(evidence) != {"sha256", "size"}
            or not isinstance(evidence.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", evidence["sha256"])
            or not isinstance(evidence.get("size"), int)
            or isinstance(evidence.get("size"), bool)
            or evidence["size"] < 0
            or relative in result
        ):
            raise AdoptionError("trusted dependency manifest entry is malformed")
        result[relative] = evidence
    if not result:
        raise AdoptionError("trusted dependency manifest is empty")
    return result


def validate_built_code_tree(
    source_code_dir: Path,
    built_code_dir: Path,
    expected_commit: str | None = None,
    trusted_dependency_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    """Return the exact package set after binding Git source and locked dependencies."""
    source_files = (
        _git_tracked_source_files(source_code_dir, expected_commit)
        if expected_commit is not None
        else _regular_files(source_code_dir, "source QueryFunction directory")
    )
    built_files = _regular_files(
        built_code_dir,
        "SAM built QueryFunction directory",
    )
    for relative, source_path in source_files.items():
        built_path = built_files.get(relative)
        if built_path is None:
            raise AdoptionError(f"SAM build omitted source file {relative}")
        try:
            if not hmac.compare_digest(
                hashlib.sha256(source_path.read_bytes()).digest(),
                hashlib.sha256(built_path.read_bytes()).digest(),
            ):
                raise AdoptionError(f"SAM build changed source file {relative}")
        except OSError:
            raise AdoptionError("source/build byte comparison failed") from None

    if trusted_dependency_manifest is None:
        raise AdoptionError("trusted dependency manifest is required")
    dependencies = _trusted_dependency_files(trusted_dependency_manifest)
    collision = set(source_files) & set(dependencies)
    if collision:
        raise AdoptionError("trusted dependency manifest overlaps repository source")

    dist_info_directories = {
        relative.split("/", 1)[0]
        for relative in dependencies
        if relative.split("/", 1)[0].endswith(".dist-info")
    }
    generated: set[str] = set()
    for relative in built_files:
        parts = relative.split("/")
        if parts[0] == "bin" and len(parts) > 1:
            generated.add(relative)
            continue
        if (
            relative.endswith((".pyc", ".pyo"))
            or parts[0] == ".pytest_cache"
            or relative == "data/pacific_bioarchive.db"
        ):
            generated.add(relative)
            continue
        if (
            len(parts) == 2
            and parts[0].endswith(".dist-info")
            and parts[1] in {"RECORD", "INSTALLER", "REQUESTED"}
        ):
            if parts[0] not in dist_info_directories:
                raise AdoptionError("SAM build contains untrusted dependency metadata")
            generated.add(relative)

    packageable = set(built_files) - generated
    expected = set(source_files) | set(dependencies)
    if packageable != expected:
        unexpected = sorted(packageable - expected)
        missing = sorted(expected - packageable)
        detail = unexpected[0] if unexpected else missing[0]
        raise AdoptionError(f"SAM build dependency set differs from lock: {detail}")
    for relative, evidence in dependencies.items():
        try:
            path = built_files[relative]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            size = path.stat().st_size
        except OSError:
            raise AdoptionError("SAM dependency byte comparison failed") from None
        if size != evidence["size"] or not hmac.compare_digest(
            digest,
            evidence["sha256"],
        ):
            raise AdoptionError(f"SAM dependency differs from trusted lock: {relative}")
    return {relative: built_files[relative] for relative in sorted(expected)}


def package_update_function(
    cli: AwsCli,
    built_code_dir: Path,
    artifact_bucket: str,
    output_template: Path,
    built_template: Mapping[str, Any] | Path,
    region: str,
    maintained_template: Mapping[str, Any] | None = None,
    source_code_dir: Path | None = None,
    expected_commit: str | None = None,
    trusted_dependency_manifest: Mapping[str, Any] | None = None,
) -> CodeArtifact:
    """Upload an immutable content-addressed SAM build and pin its version."""
    if isinstance(built_template, Path):
        try:
            built_template = _parse_processed_template(
                built_template.read_text(encoding="utf-8")
            )
        except OSError:
            raise AdoptionError("SAM built UPDATE template is unavailable") from None
    if maintained_template is None:
        try:
            maintained_template = _parse_processed_template(
                (Path(__file__).resolve().parents[1] / "query-adoption.yaml").read_text(
                    encoding="utf-8"
                )
            )
        except OSError:
            raise AdoptionError("maintained Member D template is unavailable") from None
    validate_built_template(built_template, maintained_template)
    package_files = validate_built_code_tree(
        source_code_dir or built_code_dir,
        built_code_dir,
        expected_commit,
        trusted_dependency_manifest,
    )
    verify_artifact_bucket(cli, artifact_bucket, region)
    with TemporaryDirectory() as directory:
        package_path = Path(directory) / "query-function.zip"
        hex_digest, base64_digest = _write_deterministic_zip(
            built_code_dir,
            package_path,
            package_files,
        )
        artifact_key = f"member-d/update/{hex_digest}.zip"
        response = cli.run(
            "s3api",
            "put-object",
            "--bucket",
            artifact_bucket,
            "--key",
            artifact_key,
            "--body",
            str(package_path),
            "--checksum-algorithm",
            "SHA256",
            "--checksum-sha256",
            base64_digest,
            "--server-side-encryption",
            "AES256",
            "--region",
            region,
        )
        version_id = response.get("VersionId") if isinstance(response, Mapping) else None
        if not isinstance(version_id, str) or not version_id:
            raise AdoptionError("uploaded UPDATE artifact has no version ID")
        uploaded = cli.json(
            "s3api",
            "head-object",
            "--bucket",
            artifact_bucket,
            "--key",
            artifact_key,
            "--version-id",
            version_id,
            "--checksum-mode",
            "ENABLED",
            "--region",
            region,
        )
        if (
            not isinstance(uploaded, Mapping)
            or uploaded.get("VersionId") != version_id
            or not hmac.compare_digest(
                str(uploaded.get("ChecksumSHA256", "")),
                base64_digest,
            )
        ):
            raise AdoptionError("uploaded UPDATE artifact checksum is unavailable or wrong")
    pinned = deepcopy(dict(built_template))
    pinned["Resources"]["QueryFunction"]["Properties"]["CodeUri"] = {
        "Bucket": artifact_bucket,
        "Key": artifact_key,
        "Version": version_id,
    }
    try:
        output_template.parent.mkdir(parents=True, exist_ok=True)
        output_template.write_text(
            json.dumps(_json_safe(pinned), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        raise AdoptionError("pinned UPDATE template cannot be written") from None
    return CodeArtifact(artifact_bucket, artifact_key, version_id, base64_digest)


def verify_update_artifact(
    cli: AwsCli,
    built_code_dir: Path,
    packaged_template: Mapping[str, Any],
    artifact_bucket: str,
    region: str,
    source_code_dir: Path | None = None,
    expected_commit: str | None = None,
    trusted_dependency_manifest: Mapping[str, Any] | None = None,
) -> CodeArtifact:
    """Re-hash the build and prove the exact immutable S3 version still matches."""
    code_uri = (
        packaged_template.get("Resources", {})
        .get("QueryFunction", {})
        .get("Properties", {})
        .get("CodeUri")
        if isinstance(packaged_template, Mapping)
        else None
    )
    if (
        not isinstance(code_uri, Mapping)
        or set(code_uri) != {"Bucket", "Key", "Version"}
        or code_uri.get("Bucket") != artifact_bucket
        or not isinstance(code_uri.get("Key"), str)
        or not isinstance(code_uri.get("Version"), str)
        or not code_uri["Version"]
    ):
        raise AdoptionError("pinned UPDATE artifact is malformed or uses another bucket")
    package_files = validate_built_code_tree(
        source_code_dir or built_code_dir,
        built_code_dir,
        expected_commit,
        trusted_dependency_manifest,
    )
    with TemporaryDirectory() as directory:
        package_path = Path(directory) / "query-function.zip"
        hex_digest, base64_digest = _write_deterministic_zip(
            built_code_dir,
            package_path,
            package_files,
        )
    expected_key = f"member-d/update/{hex_digest}.zip"
    if code_uri["Key"] != expected_key:
        raise AdoptionError("pinned UPDATE artifact key differs from built code bytes")
    uploaded = cli.json(
        "s3api",
        "head-object",
        "--bucket",
        artifact_bucket,
        "--key",
        expected_key,
        "--version-id",
        code_uri["Version"],
        "--checksum-mode",
        "ENABLED",
        "--region",
        region,
    )
    if (
        not isinstance(uploaded, Mapping)
        or uploaded.get("VersionId") != code_uri["Version"]
        or not hmac.compare_digest(
            str(uploaded.get("ChecksumSHA256", "")),
            base64_digest,
        )
    ):
        raise AdoptionError("pinned UPDATE artifact version or checksum is wrong")
    return CodeArtifact(
        artifact_bucket,
        expected_key,
        code_uri["Version"],
        base64_digest,
    )


def validate_hardening_runtime_evidence(
    current_processed: Mapping[str, Any],
    candidate_processed: Mapping[str, Any],
    current_parameters: Any,
    candidate_parameters: Any,
    drift: Mapping[str, Any],
    live_function: Mapping[str, Any],
    artifact: CodeArtifact,
    *,
    expected_callback: str,
) -> None:
    """Bind the final UPDATE to one in-sync Lambda callback transition."""
    if current_processed != candidate_processed:
        raise AdoptionError(
            "hardening candidate template differs from the currently deployed template"
        )
    validate_hardening_parameter_transition(
        current_parameters,
        candidate_parameters,
    )
    if (
        not isinstance(drift, Mapping)
        or drift.get("LogicalResourceId") != "QueryFunction"
        or drift.get("Status") != "IN_SYNC"
        or drift.get("Differences") not in (None, [])
    ):
        raise AdoptionError("QueryFunction must be IN_SYNC before hardening")
    if (
        not isinstance(artifact.checksum_sha256, str)
        or not artifact.checksum_sha256
        or not isinstance(live_function, Mapping)
        or not hmac.compare_digest(
            str(live_function.get("CodeSha256", "")),
            artifact.checksum_sha256,
        )
    ):
        raise AdoptionError("live QueryFunction code differs from the pinned artifact")
    before = deepcopy(dict(live_function))
    after = deepcopy(before)
    try:
        names = before["Environment"]["Names"]
        before_variables = before["Environment"]["Variables"]
        after_variables = after["Environment"]["Variables"]
    except (KeyError, TypeError):
        raise AdoptionError("live QueryFunction environment evidence is malformed") from None
    expected_names = set(before_variables) | {"INTERNAL_API_KEY"}
    if (
        not isinstance(names, list)
        or len(names) != len(expected_names)
        or set(names) != expected_names
    ):
        raise AdoptionError("live QueryFunction environment names are unexpected")
    after_variables["ALLOW_LEGACY_PROCESSING_CALLBACKS"] = "false"
    validate_hardening_function_transition(
        before,
        after,
        expected_callback=expected_callback,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit and prepare Member D import without CloudFormation writes")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("audit", "prepare"):
        command = subcommands.add_parser(name)
        for argument in ("region", "stack", "api", "authorizer", "integration", "function"):
            command.add_argument(f"--{argument}", required=True)
        command.add_argument("--workdir", default=".work")
        command.add_argument("--baseline")
        if name == "prepare":
            command.add_argument("--artifact-bucket", required=True)
    for verifier_name in ("verify-post-import", "verify-update-rollback"):
        verifier = subcommands.add_parser(verifier_name)
        for argument in (
            "region",
            "source-stack",
            "target-stack",
            "baseline",
            "api",
            "authorizer",
            "integration",
            "function",
            "workdir",
        ):
            verifier.add_argument(f"--{argument}", required=True)
    recovery = subcommands.add_parser("recovery-report")
    for argument in ("region", "source-stack", "target-stack", "workdir"):
        recovery.add_argument(f"--{argument}", required=True)
    recovery.add_argument(
        "--import-change-set-creation-failed",
        action="store_true",
    )
    validator = subcommands.add_parser("validate-change-set")
    validator.add_argument("--region", required=True)
    validator.add_argument("--stack", required=True)
    validator.add_argument("--source-stack", default=SOURCE_STACK_NAME)
    validator.add_argument("--change-set", required=True)
    validator.add_argument("--expected-type", choices=("IMPORT", "UPDATE"), required=True)
    validator.add_argument("--workdir", default=".work")
    validator.add_argument("--artifact-bucket")
    validator.add_argument("--built-template")
    validator.add_argument("--packaged-template")
    validator.add_argument("--expected-http-api-id")
    validator.add_argument("--expected-jwt-authorizer-id")
    validator.add_argument("--expected-query-input-bucket")
    validator.add_argument("--expected-storage-delete-function")
    validator.add_argument("--expected-inference-api-base-url")
    validator.add_argument(
        "--expected-allow-legacy-processing-callbacks",
        choices=("true", "false"),
    )
    validator.add_argument(
        "--expect-role-reconciliation",
        choices=("true", "false"),
    )
    policy_validator = subcommands.add_parser("validate-lambda-policy")
    policy_validator.add_argument("--region", required=True)
    policy_validator.add_argument("--function", required=True)
    policy_validator.add_argument("--workdir", default=".work")
    policy_validator.add_argument(
        "--removed-legacy-count",
        type=int,
        choices=range(4),
        required=True,
    )
    policy_validator.add_argument("--emit-revision", action="store_true")
    packager = subcommands.add_parser("package-update")
    packager.add_argument("--region", required=True)
    packager.add_argument("--artifact-bucket", required=True)
    packager.add_argument("--built-template", required=True)
    packager.add_argument("--built-code-dir", required=True)
    packager.add_argument("--source-code-dir", required=True)
    packager.add_argument("--dependency-manifest", required=True)
    packager.add_argument("--expected-commit", required=True)
    packager.add_argument("--output-template", required=True)
    for argument in ("api", "authorizer", "integration", "function"):
        validator.add_argument(f"--{argument}")
    validator.add_argument("--built-code-dir")
    validator.add_argument("--source-code-dir")
    validator.add_argument("--dependency-manifest")
    validator.add_argument("--expected-commit")
    return parser


def _read_template_file(path: Path, description: str) -> Mapping[str, Any]:
    try:
        return _parse_processed_template(path.read_text(encoding="utf-8"))
    except OSError:
        raise AdoptionError(f"{description} is unavailable") from None


def _read_json_file(path: Path, description: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise AdoptionError(f"{description} is unavailable") from None


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cli = AwsCli()
    if args.command in {"verify-post-import", "verify-update-rollback"}:
        validate_stack_names(args.source_stack, args.target_stack)
        rollback_verification = args.command == "verify-update-rollback"
        baseline = _read_json_file(
            Path(args.baseline),
            (
                "sanitized IMPORT_COMPLETE baseline"
                if rollback_verification
                else "sanitized pre-import baseline"
            ),
        )
        if not isinstance(baseline, Mapping):
            raise AdoptionError("sanitized verification baseline is malformed")
        if rollback_verification:
            validate_post_import_snapshot(baseline)
        else:
            validate_snapshot(baseline)
        if (
            baseline.get("region") != args.region
            or baseline.get("stack", {}).get("name") != args.source_stack
            or baseline.get("api", {}).get("id") != args.api
            or baseline.get("api", {}).get("authorizer", {}).get(
                "AuthorizerId"
            )
            != args.authorizer
            or baseline.get("integration", {}).get("IntegrationId")
            != args.integration
            or baseline.get("function", {}).get("FunctionName")
            != args.function
        ):
            raise AdoptionError("verification scope differs from baseline")
        workdir = Path(args.workdir)
        observed = collect_snapshot(
            cli,
            AuditConfig(
                args.region,
                args.source_stack,
                args.api,
                args.authorizer,
                args.integration,
                args.function,
                workdir,
            ),
            ownership_phase=(
                "update-rollback" if rollback_verification else "post"
            ),
        )
        if rollback_verification:
            assert_update_rollback_equivalent(baseline, observed)
        else:
            assert_post_import_equivalent(baseline, observed)
        workdir.mkdir(parents=True, exist_ok=True)
        path = workdir / (
            "update-rollback-evidence.json"
            if rollback_verification
            else "post-import-evidence.json"
        )
        path.write_text(
            json.dumps(_json_safe(observed), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(path)
        return 0
    if args.command == "recovery-report":
        validate_stack_names(args.source_stack, args.target_stack)
        workdir = Path(args.workdir)
        baseline = _read_json_file(
            workdir / "sanitized-snapshot.json",
            "sanitized pre-import baseline",
        )
        if not isinstance(baseline, Mapping):
            raise AdoptionError("sanitized pre-import baseline is malformed")
        ownership = collect_recovery_ownership(
            cli,
            args.region,
            args.source_stack,
            args.target_stack,
            baseline,
        )
        managed = set(ownership["target_stack"]["resources"])
        managed.update(
            logical_id
            for logical_id, owner in ownership["import_owners"].items()
            if owner is not None
        )
        expected_target = expected_imported_physical_ids(baseline)
        exact_target_owners = {
            logical_id: TARGET_STACK_NAME for logical_id in expected_target
        }
        target_requires_exact_boundary = ownership["target_stack"][
            "status"
        ] in {"IMPORT_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"}
        exact_target_boundary = (
            ownership["target_stack"]["resources"] == expected_target
            and ownership["import_owners"] == exact_target_owners
        )
        source_is_exact = source_recovery_evidence_is_exact(
            baseline,
            ownership["source_stack"],
        )
        classification = classify_recovery_state(
            ownership["target_stack"]["status"],
            managed,
            import_change_set_creation_failed=(
                args.import_change_set_creation_failed
            ),
        )
        if not source_is_exact or (
            target_requires_exact_boundary and not exact_target_boundary
        ):
            classification["action"] = "stop"
            classification["empty_shell_cleanup_candidate"] = False
            classification["deletion_requires_separate_approval"] = False
        report = {
            "classification": classification,
            "ownership": ownership,
        }
        workdir.mkdir(parents=True, exist_ok=True)
        path = workdir / "recovery-report.json"
        path.write_text(
            json.dumps(_json_safe(report), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(_json_safe(report), sort_keys=True))
        return 0
    if args.command in ("audit", "prepare"):
        validate_stack_names(args.stack, TARGET_STACK_NAME)
        config = AuditConfig(args.region, args.stack, args.api, args.authorizer, args.integration, args.function, Path(args.workdir))
        baseline = Path(args.baseline) if args.baseline else None
        snapshot_path = run_audit(cli, config, baseline=baseline)
        if args.command == "audit":
            print(snapshot_path)
            return 0
        paths = run_prepare(cli, config, args.artifact_bucket, production_downloader)
        for path in paths:
            print(path)
        template = json.loads((config.workdir / "import-template.json").read_text(encoding="utf-8"))
        code = template["Resources"]["QueryFunction"]["Properties"]["Code"]
        print(f"s3://{code['S3Bucket']}/{code['S3Key']}")
        print("no CloudFormation change set was created")
        return 0
    if args.command == "validate-lambda-policy":
        audited = _read_json_file(
            Path(args.workdir) / "sanitized-snapshot.json",
            "sanitized adoption snapshot",
        )
        if (
            not isinstance(audited, Mapping)
            or audited.get("region") != args.region
            or audited.get("function", {}).get("FunctionName") != args.function
        ):
            raise AdoptionError("Lambda policy validation scope mismatch")
        response = cli.json(
            "lambda",
            "get-policy",
            "--region",
            args.region,
            "--function-name",
            args.function,
        )
        raw_policy = response.get("Policy") if isinstance(response, Mapping) else None
        live_policy = _decode_json_document(raw_policy, "live Lambda policy")
        if not isinstance(live_policy, Mapping):
            raise AdoptionError("live Lambda policy is malformed")
        next_legacy_sid = validate_lambda_policy_after_update(
            live_policy,
            audited,
            removed_legacy_count=args.removed_legacy_count,
        )
        if args.emit_revision:
            if next_legacy_sid is None:
                raise AdoptionError(
                    "revision guard cannot be emitted after all historical permissions are removed"
                )
            revision = response.get("RevisionId")
            if not isinstance(revision, str) or not revision:
                raise AdoptionError("live Lambda policy revision is unavailable")
            print(
                json.dumps(
                    {
                        "next_legacy_sid": next_legacy_sid,
                        "revision_id": revision,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        else:
            print("Lambda policy validated against all route-scoped permissions")
        return 0
    if args.command == "package-update":
        built = _read_template_file(
            Path(args.built_template),
            "SAM built UPDATE template",
        )
        maintained_path = Path(__file__).resolve().parents[1] / "query-adoption.yaml"
        dependency_manifest_path = Path(args.dependency_manifest)
        _verify_committed_file(maintained_path, args.expected_commit)
        _verify_committed_file(dependency_manifest_path, args.expected_commit)
        maintained = _read_template_file(
            maintained_path,
            "maintained Member D template",
        )
        dependency_manifest = _read_json_file(
            dependency_manifest_path,
            "trusted dependency manifest",
        )
        artifact = package_update_function(
            cli,
            Path(args.built_code_dir),
            args.artifact_bucket,
            Path(args.output_template),
            built,
            args.region,
            maintained,
            Path(args.source_code_dir),
            args.expected_commit,
            dependency_manifest,
        )
        print(args.output_template)
        print(f"s3://{artifact.bucket}/{artifact.key}?versionId={artifact.version_id}")
        print("no CloudFormation change set was created or executed")
        return 0
    validate_stack_names(args.source_stack, args.stack)
    change_set = cli.json("cloudformation", "describe-change-set", "--stack-name", args.stack, "--change-set-name", args.change_set, "--region", args.region)
    described_type = change_set.get("ChangeSetType")
    if described_type != args.expected_type:
        raise AdoptionError(
            f"described change set type must be exactly {args.expected_type}"
        )
    described_stack = change_set.get("StackName")
    if described_stack != args.stack:
        raise AdoptionError("described change set target stack differs")
    if (
        change_set.get("Status") != "CREATE_COMPLETE"
        or change_set.get("ExecutionStatus") != "AVAILABLE"
    ):
        raise AdoptionError("change set is not CREATE_COMPLETE and AVAILABLE")
    workdir = Path(args.workdir)
    snapshot_path = workdir / (
        "sanitized-snapshot.json"
        if args.expected_type == "IMPORT"
        else "post-import-evidence.json"
    )
    audited = _read_json_file(snapshot_path, "saved validation evidence")
    if not isinstance(audited, Mapping):
        raise AdoptionError("saved validation evidence is malformed")
    if args.expected_type == "UPDATE":
        validate_post_import_snapshot(audited)
        if args.expect_role_reconciliation == "true":
            raise AdoptionError(
                "query-stack UPDATE role reconciliation is disabled"
            )
    if not all(
        isinstance(value, str) and value
        for value in (
            args.api,
            args.authorizer,
            args.integration,
            args.function,
        )
    ):
        raise AdoptionError(
            "--api, --authorizer, --integration and --function are required for live recollection"
        )
    fresh = collect_snapshot(
        cli,
        AuditConfig(
            args.region,
            args.source_stack,
            args.api,
            args.authorizer,
            args.integration,
            args.function,
            workdir,
        ),
        ownership_phase=(
            "preview" if args.expected_type == "IMPORT" else "post"
        ),
    )
    if args.expected_type == "IMPORT":
        assert_import_preview_equivalent(audited, fresh)
    else:
        assert_post_import_boundary_current(audited, fresh)
        audited = fresh
    processed_response = cli.json("cloudformation", "get-template", "--stack-name", args.stack, "--change-set-name", args.change_set, "--template-stage", "Processed", "--region", args.region)
    processed = _parse_processed_template(
        processed_response.get("TemplateBody")
        if isinstance(processed_response, Mapping)
        else None
    )
    if args.expected_type == "IMPORT":
        if not args.artifact_bucket:
            raise AdoptionError("--artifact-bucket is required for IMPORT validation")
        expected = build_resources_to_import(audited)
        artifact = validate_import_artifacts(
            processed,
            _read_template_file(
                workdir / "import-template.json",
                "generated import template",
            ),
            change_set.get("Parameters"),
            _read_json_file(
                workdir / "import-parameters.json",
                "generated import parameters",
            ),
            audited,
            args.artifact_bucket,
        )
        uploaded = cli.json(
            "s3api",
            "head-object",
            "--bucket",
            artifact.bucket,
            "--key",
            artifact.key,
            "--version-id",
            artifact.version_id,
            "--checksum-mode",
            "ENABLED",
            "--region",
            args.region,
        )
        expected_checksum = audited.get("function", {}).get("CodeSha256")
        if (
            not isinstance(uploaded, Mapping)
            or uploaded.get("VersionId") != artifact.version_id
            or not isinstance(expected_checksum, str)
            or not hmac.compare_digest(
                str(uploaded.get("ChecksumSHA256", "")),
                expected_checksum,
            )
        ):
            raise AdoptionError(
                "IMPORT artifact version or checksum differs from audited Lambda code"
            )
        validate_import_change_set(
            change_set.get("Changes", []),
            expected,
            change_set_type=described_type,
        )
    else:
        if not args.artifact_bucket:
            raise AdoptionError("--artifact-bucket is required for UPDATE validation")
        if not args.built_template:
            raise AdoptionError("--built-template is required for UPDATE validation")
        if not args.built_code_dir:
            raise AdoptionError("--built-code-dir is required for UPDATE validation")
        if not args.source_code_dir:
            raise AdoptionError("--source-code-dir is required for UPDATE validation")
        if not args.dependency_manifest:
            raise AdoptionError("--dependency-manifest is required for UPDATE validation")
        if not args.expected_commit:
            raise AdoptionError("--expected-commit is required for UPDATE validation")
        if not args.packaged_template:
            raise AdoptionError("--packaged-template is required for UPDATE validation")
        if args.expected_allow_legacy_processing_callbacks is None:
            raise AdoptionError(
                "--expected-allow-legacy-processing-callbacks is required for UPDATE validation"
            )
        built = _read_template_file(
            Path(args.built_template),
            "SAM built UPDATE template",
        )
        packaged = _read_template_file(
            Path(args.packaged_template),
            "packaged UPDATE template",
        )
        maintained_path = Path(__file__).resolve().parents[1] / "query-adoption.yaml"
        dependency_manifest_path = Path(args.dependency_manifest)
        _verify_committed_file(maintained_path, args.expected_commit)
        _verify_committed_file(dependency_manifest_path, args.expected_commit)
        maintained = _read_template_file(
            maintained_path,
            "maintained Member D template",
        )
        dependency_manifest = _read_json_file(
            dependency_manifest_path,
            "trusted dependency manifest",
        )
        artifact = verify_update_artifact(
            cli,
            Path(args.built_code_dir),
            packaged,
            args.artifact_bucket,
            args.region,
            Path(args.source_code_dir),
            args.expected_commit,
            dependency_manifest,
        )
        current_parameter_names = collect_stack_parameter_names(
            cli,
            args.stack,
            args.region,
        )
        if current_parameter_names != {
            "ExistingQueryLambdaRoleArn",
            "ExistingHttpApiId",
            "ExistingJwtAuthorizerId",
        }:
            raise AdoptionError(
                "query stack parameter names differ from the exact IMPORT boundary"
            )
        safe_environment = audited.get("function", {}).get(
            "safe_environment", {}
        )
        audited_api_id = audited.get("api", {}).get("id")
        audited_authorizer_id = audited.get("api", {}).get(
            "authorizer", {}
        ).get("AuthorizerId")
        if (
            args.expected_http_api_id != audited_api_id
            or args.expected_jwt_authorizer_id != audited_authorizer_id
        ):
            raise AdoptionError(
                "UPDATE API or authorizer parameter differs from post-import evidence"
            )
        validate_update_artifacts(
            processed,
            built,
            packaged,
            maintained,
            change_set.get("Parameters"),
            {
                "ExistingQueryLambdaRoleArn": audited.get("function", {}).get(
                    "Role"
                ),
                "ExistingHttpApiId": args.expected_http_api_id,
                "ExistingJwtAuthorizerId": args.expected_jwt_authorizer_id,
                "ExistingFilesTableName": safe_environment.get(
                    "DYNAMODB_TABLE"
                ),
                "ExistingSubscriptionsTableName": safe_environment.get(
                    "SUBSCRIPTIONS_TABLE"
                ),
                "ExistingNotificationsTableName": safe_environment.get(
                    "NOTIFICATIONS_TABLE"
                ),
                "AllowedOrigin": "http://localhost:3000",
                "PublicAllowedOrigin": "https://quinby8930.github.io",
                "QueryInputBucketName": args.expected_query_input_bucket,
                "StorageDeleteFunctionName": args.expected_storage_delete_function,
                "InferenceApiBaseUrl": args.expected_inference_api_base_url,
                "AllowLegacyProcessingCallbacks": (
                    args.expected_allow_legacy_processing_callbacks
                ),
            },
            artifact,
            internal_key_already_exists=False,
        )
        validate_update_change_set(
            change_set.get("Changes", []),
            processed,
        )
    print("change set validated; no CloudFormation change set was created or executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
