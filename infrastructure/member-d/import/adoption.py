"""Pure, fail-closed descriptions for the Member D import change set.

This module deliberately has no AWS, process, filesystem, or network dependency.
"""

from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping


class AdoptionError(ValueError):
    """The live state cannot be represented without changing traffic."""


@dataclass(frozen=True)
class RouteContract:
    route_key: str
    authorization_type: str


@dataclass(frozen=True)
class CodeArtifact:
    bucket: str
    key: str
    version_id: str
    checksum_sha256: str | None = None


ROUTES_BY_LOGICAL_ID: dict[str, RouteContract] = {
    "AuthTestRoute": RouteContract("GET /auth-test", "JWT"),
    "QueryByTagsRoute": RouteContract("POST /query/by-tags", "JWT"),
    "QueryBySpeciesRoute": RouteContract("POST /query/by-species", "JWT"),
    "QueryByThumbnailRoute": RouteContract("GET /query/by-thumbnail", "JWT"),
    "QueryByFileRoute": RouteContract("POST /query/by-file", "JWT"),
    "EditTagsRoute": RouteContract("POST /tags/edit", "JWT"),
    "DeleteFilesRoute": RouteContract("POST /files/delete", "JWT"),
    "SubscribeRoute": RouteContract("POST /notifications/subscribe", "JWT"),
    "UnsubscribeRoute": RouteContract("DELETE /notifications/subscribe", "JWT"),
    "SubscriptionsRoute": RouteContract("GET /notifications/subscriptions", "JWT"),
    "NotificationsRoute": RouteContract("GET /notifications", "JWT"),
    "ReserveUploadRoute": RouteContract("POST /internal/uploads/reserve", "NONE"),
    "AcquireProcessingRoute": RouteContract("POST /internal/files/{file_id}/processing", "NONE"),
    "CompleteFileRoute": RouteContract("PUT /internal/files/{file_id}/complete", "NONE"),
    "FailFileRoute": RouteContract("PUT /internal/files/{file_id}/failed", "NONE"),
    "AuthorizeAssetsRoute": RouteContract("POST /internal/assets/authorize", "NONE"),
}

OPTIONS_ROUTES_BY_LOGICAL_ID: dict[str, RouteContract] = {
    "AuthTestOptionsRoute": RouteContract("OPTIONS /auth-test", "NONE"),
    "QueryByTagsOptionsRoute": RouteContract("OPTIONS /query/by-tags", "NONE"),
    "QueryBySpeciesOptionsRoute": RouteContract("OPTIONS /query/by-species", "NONE"),
    "QueryByThumbnailOptionsRoute": RouteContract(
        "OPTIONS /query/by-thumbnail", "NONE"
    ),
    "QueryByFileOptionsRoute": RouteContract("OPTIONS /query/by-file", "NONE"),
    "EditTagsOptionsRoute": RouteContract("OPTIONS /tags/edit", "NONE"),
    "DeleteFilesOptionsRoute": RouteContract("OPTIONS /files/delete", "NONE"),
    "SubscribeOptionsRoute": RouteContract(
        "OPTIONS /notifications/subscribe", "NONE"
    ),
    "SubscriptionsOptionsRoute": RouteContract(
        "OPTIONS /notifications/subscriptions", "NONE"
    ),
    "NotificationsOptionsRoute": RouteContract("OPTIONS /notifications", "NONE"),
}

_SAFE_ENVIRONMENT_NAMES = {
    "REPO_BACKEND",
    "DYNAMODB_TABLE",
    "SUBSCRIPTIONS_TABLE",
    "NOTIFICATIONS_TABLE",
    "CORS_ORIGINS",
    "TAG_DETECTOR_BACKEND",
}
_EXPECTED_ENVIRONMENT_NAMES = _SAFE_ENVIRONMENT_NAMES | {"INTERNAL_API_KEY"}
_REQUIRED_TYPE_SCHEMAS = {
    "AWS::DynamoDB::Table": ["/properties/TableName"],
    "AWS::Lambda::Function": ["/properties/FunctionName"],
    "AWS::ApiGatewayV2::Integration": ["/properties/ApiId", "/properties/IntegrationId"],
    "AWS::ApiGatewayV2::Route": ["/properties/ApiId", "/properties/RouteId"],
}
_STABLE_STACK_STATUSES = {
    "CREATE_COMPLETE",
    "UPDATE_COMPLETE",
    "UPDATE_ROLLBACK_COMPLETE",
    "IMPORT_COMPLETE",
}
_FUNCTION_PROPERTIES = (
    "Runtime", "Handler", "Timeout", "MemorySize", "Description", "Tags", "SnapStart", "Architectures", "Layers",
    "EphemeralStorage", "VpcConfig", "FileSystemConfigs", "KmsKeyArn",
    "DeadLetterConfig", "TracingConfig", "LoggingConfig", "CodeSigningConfigArn",
    "RuntimeManagementConfig", "ReservedConcurrentExecutions",
)
_BASE_MANAGED = {"FilesTable", "SubscriptionsTable", "NotificationsTable", "QueryLambdaRole"}
_ADOPTED_LOGICAL_IDS = {
    "ReservationsTable",
    "QueryFunction",
    "QueryIntegration",
    *ROUTES_BY_LOGICAL_ID,
}
_INTEGRATION_KEYS = {"IntegrationId", "IntegrationType", "IntegrationSubtype", "IntegrationMethod", "PayloadFormatVersion", "IntegrationUri", "ConnectionType", "ConnectionId", "ContentHandlingStrategy", "CredentialsArn", "Description", "PassthroughBehavior", "RequestParameters", "RequestTemplates", "ResponseParameters", "TemplateSelectionExpression", "TlsConfig", "TimeoutInMillis"}
_ROUTE_KEYS = {"RouteId", "RouteKey", "Target", "AuthorizationType", "AuthorizerId", "ApiKeyRequired", "AuthorizationScopes", "ModelSelectionExpression", "OperationName", "RequestModels", "RequestParameters", "RouteResponseSelectionExpression"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AdoptionError(message)


def _stack_parameter_names(stack: Mapping[str, Any]) -> set[str]:
    parameters = stack.get("parameters", [])
    if isinstance(parameters, Mapping):
        return set(parameters)
    names: set[str] = set()
    for parameter in parameters:
        if isinstance(parameter, str):
            names.add(parameter)
        elif isinstance(parameter, Mapping) and isinstance(parameter.get("ParameterKey"), str):
            names.add(parameter["ParameterKey"])
    return names


def _route_lookup(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    routes = snapshot.get("api", {}).get("routes", [])
    result: dict[str, Mapping[str, Any]] = {}
    for route in routes:
        if not isinstance(route, Mapping):
            raise AdoptionError("route is malformed")
        key = route.get("RouteKey")
        if not isinstance(key, str):
            raise AdoptionError("route key is malformed")
        if key in result:
            raise AdoptionError(f"duplicate route {key}")
        result[key] = route
    return result


def _role_name_from_arn(value: Any, account: str) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(rf"arn:aws:iam::{re.escape(account)}:role/(?:[^/]+/)*([^/]+)", value)
    return match.group(1) if match else None


_BASE_DYNAMODB_ACTIONS = {
    "dynamodb:PutItem",
    "dynamodb:GetItem",
    "dynamodb:Scan",
    "dynamodb:Query",
    "dynamodb:UpdateItem",
    "dynamodb:DeleteItem",
}
_RESERVATION_DYNAMODB_ACTIONS = _BASE_DYNAMODB_ACTIONS | {
    "dynamodb:TransactWriteItems"
}


def _as_unique_string_set(value: Any, field: str) -> set[str]:
    values = [value] if isinstance(value, str) else value
    _require(
        isinstance(values, list)
        and values
        and all(isinstance(item, str) and item for item in values),
        f"QueryLambdaRole drift {field} is malformed",
    )
    result = set(values)
    _require(
        len(result) == len(values),
        f"QueryLambdaRole drift {field} contains duplicates",
    )
    return result


def _policy_signature(value: Any) -> tuple[set[str], set[str]]:
    _require(
        isinstance(value, Mapping)
        and set(value) == {"Version", "Statement"}
        and value.get("Version") == "2012-10-17",
        "QueryLambdaRole drift policy is malformed",
    )
    statements = value.get("Statement")
    _require(
        isinstance(statements, list) and len(statements) == 1,
        "QueryLambdaRole drift policy statement is malformed",
    )
    statement = statements[0]
    _require(
        isinstance(statement, Mapping)
        and set(statement) == {"Effect", "Action", "Resource"}
        and statement.get("Effect") == "Allow",
        "QueryLambdaRole drift policy statement is malformed",
    )
    return (
        _as_unique_string_set(statement.get("Action"), "actions"),
        _as_unique_string_set(statement.get("Resource"), "resources"),
    )


def _expected_table_arns(account: str, region: str) -> tuple[set[str], str]:
    prefix = f"arn:aws:dynamodb:{region}:{account}:table/"
    reservation = f"{prefix}PacificBioArchiveUploadReservations"
    return (
        {
            f"{prefix}PacificBioArchiveFiles",
            f"{prefix}PacificBioArchiveSubscriptions",
            f"{prefix}PacificBioArchiveNotifications",
        },
        reservation,
    )


def _validate_role_drift(role: Mapping[str, Any], account: str, region: str) -> None:
    drift = role.get("drift")
    _require(isinstance(drift, Mapping), "QueryLambdaRole drift evidence is missing")
    status = drift.get("status")
    differences = drift.get("differences")
    _require(isinstance(differences, list), "QueryLambdaRole drift is malformed")
    processed = role.get("processed_definition", {})
    processed_policies = (
        processed.get("Properties", {}).get("Policies", [])
        if isinstance(processed, Mapping)
        else []
    )
    processed_names = {
        item.get("PolicyName")
        for item in processed_policies
        if isinstance(item, Mapping)
    }
    inline = role.get("inline_policies")
    _require(isinstance(inline, Mapping), "QueryLambdaRole inline policies are malformed")

    if status == "IN_SYNC":
        _require(not differences, "QueryLambdaRole IN_SYNC drift has differences")
        _require(
            set(inline) == processed_names,
            "QueryLambdaRole inline policy names differ from processed template",
        )
        return

    _require(
        status == "MODIFIED" and len(differences) == 2,
        "QueryLambdaRole has unapproved drift",
    )
    by_path = {
        item.get("path"): item
        for item in differences
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    _require(
        len(by_path) == 2
        and set(by_path) == {"/Policies/0/PolicyDocument", "/Policies/1"},
        "QueryLambdaRole has unapproved drift paths",
    )
    baseline_tables, reservation_table = _expected_table_arns(account, region)

    policy_delta = by_path["/Policies/0/PolicyDocument"]
    _require(
        policy_delta.get("type") == "NOT_EQUAL",
        "QueryLambdaRole QueryServiceAccess drift type is unapproved",
    )
    expected_actions, expected_resources = _policy_signature(
        policy_delta.get("expected")
    )
    actual_actions, actual_resources = _policy_signature(
        policy_delta.get("actual")
    )
    _require(
        expected_actions == _BASE_DYNAMODB_ACTIONS
        and expected_resources == baseline_tables
        and actual_actions == _RESERVATION_DYNAMODB_ACTIONS
        and actual_resources == baseline_tables | {reservation_table},
        "QueryLambdaRole QueryServiceAccess drift is unapproved",
    )

    added = by_path["/Policies/1"]
    actual_added = added.get("actual")
    _require(
        added.get("type") == "ADD"
        and added.get("expected") is None
        and isinstance(actual_added, Mapping)
        and set(actual_added) == {"PolicyName", "PolicyDocument"}
        and actual_added.get("PolicyName") == "UploadReservationsAccess",
        "QueryLambdaRole added policy drift is unapproved",
    )
    added_actions, added_resources = _policy_signature(
        actual_added.get("PolicyDocument")
    )
    _require(
        added_actions == _RESERVATION_DYNAMODB_ACTIONS
        and added_resources == {reservation_table},
        "QueryLambdaRole UploadReservationsAccess drift is unapproved",
    )

    _require(
        processed_names == {"QueryServiceAccess"}
        and set(inline) == {"QueryServiceAccess", "UploadReservationsAccess"},
        "QueryLambdaRole inline policy set is unapproved",
    )
    live_query = inline.get("QueryServiceAccess")
    live_added = inline.get("UploadReservationsAccess")
    _require(
        isinstance(live_query, Mapping)
        and live_query.get("PolicyName") in (None, "QueryServiceAccess")
        and _policy_signature(live_query.get("PolicyDocument"))
        == (_RESERVATION_DYNAMODB_ACTIONS, baseline_tables | {reservation_table}),
        "QueryLambdaRole live QueryServiceAccess is unapproved",
    )
    _require(
        isinstance(live_added, Mapping)
        and live_added.get("PolicyName") in (None, "UploadReservationsAccess")
        and _policy_signature(live_added.get("PolicyDocument"))
        == (_RESERVATION_DYNAMODB_ACTIONS, {reservation_table}),
        "QueryLambdaRole live UploadReservationsAccess is unapproved",
    )


def _validate_reservations_table(
    table: Mapping[str, Any], account: str, region: str
) -> None:
    expected_keys = {
        "TableName",
        "TableStatus",
        "TableArn",
        "BillingMode",
        "AttributeDefinitions",
        "KeySchema",
        "GlobalSecondaryIndexes",
        "LocalSecondaryIndexes",
        "StreamSpecification",
        "DeletionProtectionEnabled",
        "TableClass",
        "Replicas",
        "Tags",
        "TimeToLiveStatus",
        "PointInTimeRecoveryStatus",
        "SSEMode",
        "OnDemandThroughput",
        "WarmThroughput",
        "MultiRegionConsistency",
        "ResourcePolicy",
        "KinesisDataStreamDestinations",
        "ContributorInsightsStatus",
        "VectorIndexes",
        "GlobalTableWitnesses",
    }
    _require(
        set(table) == expected_keys,
        "ReservationsTable shape cannot be represented safely",
    )
    expected_arn = (
        f"arn:aws:dynamodb:{region}:{account}:"
        "table/PacificBioArchiveUploadReservations"
    )
    _require(
        table.get("TableName") == "PacificBioArchiveUploadReservations"
        and table.get("TableArn") == expected_arn,
        "ReservationsTable identity mismatch",
    )
    _require(
        table.get("TableStatus") == "ACTIVE"
        and table.get("BillingMode") == "PAY_PER_REQUEST",
        "ReservationsTable status or billing mode mismatch",
    )
    _require(
        table.get("AttributeDefinitions")
        == [{"AttributeName": "reservation_key", "AttributeType": "S"}]
        and table.get("KeySchema")
        == [{"AttributeName": "reservation_key", "KeyType": "HASH"}],
        "ReservationsTable key schema mismatch",
    )
    _require(
        table.get("GlobalSecondaryIndexes") == []
        and table.get("LocalSecondaryIndexes") == []
        and table.get("StreamSpecification") is None
        and table.get("Replicas") == []
        and table.get("VectorIndexes") == []
        and table.get("GlobalTableWitnesses") == [],
        "ReservationsTable indexes, stream, or replicas are unsupported",
    )
    _require(
        table.get("DeletionProtectionEnabled") is False
        and table.get("TableClass") == "STANDARD"
        and table.get("Tags") == []
        and table.get("TimeToLiveStatus") == "DISABLED"
        and table.get("PointInTimeRecoveryStatus") == "DISABLED"
        and table.get("SSEMode") == "AWS_OWNED",
        "ReservationsTable has unsupported managed properties",
    )
    on_demand = table.get("OnDemandThroughput")
    _require(
        on_demand in (None, {})
        or (
            isinstance(on_demand, Mapping)
            and set(on_demand) <= {
                "MaxReadRequestUnits",
                "MaxWriteRequestUnits",
            }
            and on_demand
            and all(value == -1 for value in on_demand.values())
        ),
        "ReservationsTable has a configured on-demand throughput cap",
    )
    warm = table.get("WarmThroughput")
    _require(
        warm in (None, {})
        or (
            isinstance(warm, Mapping)
            and set(warm)
            == {
                "ReadUnitsPerSecond",
                "WriteUnitsPerSecond",
                "Status",
            }
            and warm.get("Status") == "ACTIVE"
            and isinstance(warm.get("ReadUnitsPerSecond"), int)
            and not isinstance(warm.get("ReadUnitsPerSecond"), bool)
            and warm["ReadUnitsPerSecond"] >= 12000
            and isinstance(warm.get("WriteUnitsPerSecond"), int)
            and not isinstance(warm.get("WriteUnitsPerSecond"), bool)
            and warm["WriteUnitsPerSecond"] >= 4000
        ),
        "ReservationsTable has unsupported warm throughput",
    )
    _require(
        table.get("MultiRegionConsistency") in (None, "EVENTUAL"),
        "ReservationsTable has unsupported multi-region consistency",
    )
    _require(
        table.get("ResourcePolicy") is None,
        "ReservationsTable has an unmanaged resource policy",
    )
    _require(
        isinstance(table.get("KinesisDataStreamDestinations"), list)
        and all(
            isinstance(destination, Mapping)
            and destination.get("DestinationStatus") == "DISABLED"
            for destination in table["KinesisDataStreamDestinations"]
        ),
        "ReservationsTable has an unmanaged Kinesis streaming destination",
    )
    _require(
        table.get("ContributorInsightsStatus") == "DISABLED",
        "ReservationsTable has unmanaged Contributor Insights configuration",
    )


def _validate_function(function: Mapping[str, Any], account: str) -> None:
    _require(function.get("FunctionName") == "PacificBioArchive-QueryLambda", "function name mismatch")
    _require(function.get("PackageType") == "Zip", "package type is not Zip")
    _require(function.get("Runtime") == "python3.12", "function runtime mismatch")
    _require(function.get("Handler") == "lambda_function.handler", "function handler mismatch")
    _require(function.get("Timeout") == 30 and function.get("MemorySize") == 512, "function sizing mismatch")
    _require(_role_name_from_arn(function.get("Role"), account) == "PacificBioArchive-QueryLambdaRole", "function role mismatch")
    for key in _FUNCTION_PROPERTIES:
        _require(key in function, f"unsupported function configuration: {key}")
    _require(function.get("Architectures") == ["x86_64"], "function architectures mismatch")
    _require(function.get("Layers") == [], "function layers cannot be preserved")
    _require(function.get("VpcConfig") == {"SubnetIds": [], "SecurityGroupIds": [], "Ipv6AllowedForDualStack": False}, "function VPC configuration cannot be preserved")
    _require(function.get("FileSystemConfigs") == [], "function file system configuration cannot be preserved")
    _require(function.get("CodeSha256"), "function code digest missing")
    names = function.get("environment_names")
    safe = function.get("safe_environment")
    _require(isinstance(names, list) and set(names) == _EXPECTED_ENVIRONMENT_NAMES and len(names) == len(_EXPECTED_ENVIRONMENT_NAMES), "function environment names cannot be preserved")
    _require(isinstance(safe, Mapping) and set(safe) == _SAFE_ENVIRONMENT_NAMES, "function environment cannot be preserved")
    _require(all(isinstance(value, str) for value in safe.values()), "function environment contains unsupported value")
    policy = function.get("resource_policy")
    _require(isinstance(policy, Mapping) and policy.get("Statement"), "function resource policy missing")
    statements = policy.get("Statement")
    _require(isinstance(statements, list) and len(statements) == 1, "function resource policy cannot be preserved")
    statement = statements[0]
    _require(isinstance(statement, Mapping) and set(statement) == {"Sid", "Effect", "Principal", "Action", "Resource"}, "function resource policy cannot be preserved")
    _require(
        isinstance(statement.get("Sid"), str)
        and re.fullmatch(r"[A-Za-z0-9-_]{1,100}", statement["Sid"]),
        "function resource policy Sid cannot be preserved",
    )
    _require(statement.get("Effect") == "Allow" and statement.get("Principal") == {"Service": "apigateway.amazonaws.com"} and statement.get("Action") == "lambda:InvokeFunction", "function resource policy cannot be preserved")
    _require(isinstance(statement.get("Resource"), str) and statement["Resource"].endswith(":function:PacificBioArchive-QueryLambda"), "function resource policy cannot be preserved")


def validate_lambda_policy_after_update(
    live_policy: Mapping[str, Any],
    audited: Mapping[str, Any],
    *,
    expect_legacy: bool,
) -> str:
    """Prove all route-scoped permissions and the exact legacy statement state."""
    validate_snapshot(audited)
    account = audited["caller"]["Account"]
    region = audited["region"]
    api_id = audited["api"]["id"]
    function_arn = (
        f"arn:aws:lambda:{region}:{account}:"
        "function:PacificBioArchive-QueryLambda"
    )
    audited_statements = audited["function"]["resource_policy"]["Statement"]
    legacy = audited_statements[0]
    legacy_sid = legacy["Sid"]

    expected_source_arns: set[str] = set()
    for contract in (
        list(ROUTES_BY_LOGICAL_ID.values())
        + list(OPTIONS_ROUTES_BY_LOGICAL_ID.values())
    ):
        method, path = contract.route_key.split(" ", 1)
        normalized_path = re.sub(r"\{[^/{}]+\}", "*", path)
        expected_source_arns.add(
            f"arn:aws:execute-api:{region}:{account}:{api_id}"
            f"/*/{method}{normalized_path}"
        )

    statements = live_policy.get("Statement")
    _require(
        isinstance(statements, list),
        "live Lambda resource policy is malformed",
    )
    broad: list[Mapping[str, Any]] = []
    scoped_source_arns: set[str] = set()
    seen_sids: set[str] = set()
    for statement in statements:
        _require(
            isinstance(statement, Mapping)
            and isinstance(statement.get("Sid"), str)
            and re.fullmatch(r"[A-Za-z0-9-_]{1,100}", statement["Sid"])
            and statement["Sid"] not in seen_sids,
            "live Lambda resource policy has malformed or duplicate Sid",
        )
        seen_sids.add(statement["Sid"])
        _require(
            statement.get("Effect") == "Allow"
            and statement.get("Principal")
            == {"Service": "apigateway.amazonaws.com"}
            and statement.get("Action") == "lambda:InvokeFunction"
            and statement.get("Resource") == function_arn,
            "live Lambda resource policy contains an unapproved statement",
        )
        if "Condition" not in statement:
            _require(
                set(statement) == {
                    "Sid",
                    "Effect",
                    "Principal",
                    "Action",
                    "Resource",
                },
                "live Lambda resource policy contains an unscoped statement",
            )
            broad.append(statement)
            continue
        _require(
            set(statement)
            == {
                "Sid",
                "Effect",
                "Principal",
                "Action",
                "Resource",
                "Condition",
            }
            and isinstance(statement.get("Condition"), Mapping)
            and set(statement["Condition"]) == {"ArnLike"}
            and isinstance(statement["Condition"].get("ArnLike"), Mapping)
            and set(statement["Condition"]["ArnLike"])
            == {"AWS:SourceArn"}
            and isinstance(
                statement["Condition"]["ArnLike"].get("AWS:SourceArn"),
                str,
            ),
            "live Lambda scoped permission is malformed",
        )
        source_arn = statement["Condition"]["ArnLike"]["AWS:SourceArn"]
        _require(
            source_arn not in scoped_source_arns,
            "live Lambda scoped permission is duplicated",
        )
        scoped_source_arns.add(source_arn)

    _require(
        scoped_source_arns == expected_source_arns,
        "live Lambda policy does not contain the exact route-scoped permissions",
    )
    if expect_legacy:
        _require(
            broad == [legacy],
            "live Lambda legacy permission differs from the audited statement",
        )
    else:
        _require(
            broad == [],
            "live Lambda policy still contains an unscoped permission",
        )
    return legacy_sid


def validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Reject any state that cannot be imported without changing live traffic."""
    caller = snapshot.get("caller", {})
    arn = caller.get("Arn") if isinstance(caller, Mapping) else None
    account = caller.get("Account") if isinstance(caller, Mapping) else None
    _require(arn != f"arn:aws:iam::{account}:root", "Root caller is not permitted")
    _require(isinstance(account, str) and isinstance(arn, str) and arn == f"arn:aws:iam::{account}:user/fit5225-cli-deployer", "caller must be exact IAM user/fit5225-cli-deployer")
    stack = snapshot.get("stack", {})
    _require(isinstance(stack, Mapping) and stack.get("status") in _STABLE_STACK_STATUSES, "stack is not in an import-safe stable state")
    managed = stack.get("managed")
    _require(isinstance(managed, Mapping) and set(managed) in (_BASE_MANAGED, _BASE_MANAGED | _ADOPTED_LOGICAL_IDS), "managed resource set mismatch")
    template = stack.get("template", {})
    resources = template.get("Resources", {}) if isinstance(template, Mapping) else {}
    _require(isinstance(resources, Mapping) and resources.get("QueryLambdaRole", {}).get("Type") == "AWS::IAM::Role", "stack-owned QueryLambdaRole missing")
    role_name = "PacificBioArchive-QueryLambdaRole"
    processed_role_name = resources["QueryLambdaRole"].get("Properties", {}).get("RoleName")
    role_snapshot = snapshot.get("role", {})
    role_snapshot_name = role_snapshot.get("role_name") if isinstance(role_snapshot, Mapping) else None
    role_snapshot_processed_name = role_snapshot.get("processed_definition", {}).get("Properties", {}).get("RoleName") if isinstance(role_snapshot, Mapping) else None
    _require(
        managed.get("QueryLambdaRole") == role_name
        and processed_role_name == role_name
        and role_snapshot_name == role_name
        and role_snapshot_processed_name == role_name,
        "QueryLambdaRole role identity mismatch",
    )
    _require(
        role_snapshot.get("account") == account
        and role_snapshot.get("region") == snapshot.get("region"),
        "QueryLambdaRole drift scope mismatch",
    )
    _validate_role_drift(role_snapshot, account, snapshot.get("region"))
    schemas = snapshot.get("type_schemas")
    _require(isinstance(schemas, Mapping), "primary identifier schemas missing")
    for resource_type, expected in _REQUIRED_TYPE_SCHEMAS.items():
        _require(schemas.get(resource_type) == expected, f"primary identifier schema mismatch for {resource_type}")
    _require(not snapshot.get("owned_physical_ids"), "candidate resource is already owned by another stack")
    reservations_table = snapshot.get("reservations_table")
    _require(isinstance(reservations_table, Mapping), "ReservationsTable is missing")
    _validate_reservations_table(
        reservations_table,
        account,
        snapshot.get("region"),
    )
    api = snapshot.get("api", {})
    _require(isinstance(api, Mapping) and isinstance(api.get("id"), str), "API identity missing")
    authorizer = api.get("authorizer", {})
    _require(isinstance(authorizer, Mapping) and authorizer.get("AuthorizerId") == "7ir7fs" and authorizer.get("AuthorizerType") == "JWT", "API authorizer mismatch")
    integration = snapshot.get("integration", {})
    _require(isinstance(integration, Mapping), "integration missing")
    _require(set(integration) <= _INTEGRATION_KEYS, "integration contains unsupported configuration")
    _require(integration.get("IntegrationId") == "fbjojun", "integration identity mismatch")
    _require(integration.get("IntegrationType") == "AWS_PROXY" and integration.get("IntegrationMethod") == "POST", "integration configuration mismatch")
    _require(integration.get("PayloadFormatVersion") == "2.0", "integration payload format mismatch")
    uri = integration.get("IntegrationUri")
    expected_suffix = f"functions/arn:aws:lambda:{snapshot.get('region')}:{account}:function:PacificBioArchive-QueryLambda/invocations"
    _require(isinstance(uri, str) and uri.endswith(expected_suffix) and ":function:PacificBioArchive-QueryLambda:" not in uri, "integration URI is not bound to the unqualified Query Lambda account and region")
    function = snapshot.get("function", {})
    _require(isinstance(function, Mapping), "function missing")
    _validate_function(function, account)
    routes = _route_lookup(snapshot)
    for contract in ROUTES_BY_LOGICAL_ID.values():
        route = routes.get(contract.route_key)
        _require(route is not None, f"missing route {contract.route_key}")
        _require(set(route) <= _ROUTE_KEYS, f"route contains unsupported configuration for {contract.route_key}")
        _require(route.get("Target") == "integrations/fbjojun", f"route integration mismatch for {contract.route_key}")
        _require(route.get("AuthorizationType") == contract.authorization_type, f"route authorization mismatch for {contract.route_key}")
        if contract.authorization_type == "JWT":
            _require(route.get("AuthorizerId") == "7ir7fs", f"route authorizer mismatch for {contract.route_key}")
        else:
            _require(route.get("AuthorizerId") in (None, ""), f"internal route authorizer mismatch for {contract.route_key}")
    if "ReservationsTable" in managed:
        expected_physical_ids = {
            "ReservationsTable": reservations_table["TableName"],
            "QueryFunction": function["FunctionName"],
            "QueryIntegration": integration["IntegrationId"],
            **{
                logical_id: routes[contract.route_key]["RouteId"]
                for logical_id, contract in ROUTES_BY_LOGICAL_ID.items()
            },
        }
        _require(
            all(
                managed.get(logical_id) == physical_id
                for logical_id, physical_id in expected_physical_ids.items()
            ),
            "adopted resource physical identity mismatch",
        )


def build_resources_to_import(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_snapshot(snapshot)
    api_id = snapshot["api"]["id"]
    manifest = [
        {"ResourceType": "AWS::DynamoDB::Table", "LogicalResourceId": "ReservationsTable", "ResourceIdentifier": {"TableName": snapshot["reservations_table"]["TableName"]}},
        {"ResourceType": "AWS::Lambda::Function", "LogicalResourceId": "QueryFunction", "ResourceIdentifier": {"FunctionName": snapshot["function"]["FunctionName"]}},
        {"ResourceType": "AWS::ApiGatewayV2::Integration", "LogicalResourceId": "QueryIntegration", "ResourceIdentifier": {"ApiId": api_id, "IntegrationId": snapshot["integration"]["IntegrationId"]}},
    ]
    routes = _route_lookup(snapshot)
    for logical_id, contract in ROUTES_BY_LOGICAL_ID.items():
        manifest.append({"ResourceType": "AWS::ApiGatewayV2::Route", "LogicalResourceId": logical_id, "ResourceIdentifier": {"ApiId": api_id, "RouteId": routes[contract.route_key]["RouteId"]}})
    return manifest


def _retained(resource_type: str, properties: Mapping[str, Any]) -> dict[str, Any]:
    return {"Type": resource_type, "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": dict(properties)}


def _function_properties(function: Mapping[str, Any], artifact: CodeArtifact) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "FunctionName": function["FunctionName"], "Runtime": function["Runtime"], "Handler": function["Handler"],
        "Role": {"Fn::GetAtt": ["QueryLambdaRole", "Arn"]}, "Timeout": function["Timeout"], "MemorySize": function["MemorySize"],
        "Code": {"S3Bucket": artifact.bucket, "S3Key": artifact.key, "S3ObjectVersion": artifact.version_id},
        "Environment": {"Variables": {**dict(function["safe_environment"]), "INTERNAL_API_KEY": {"Ref": "InternalApiKey"}}},
    }
    for key in _FUNCTION_PROPERTIES[4:]:
        if function[key] is not None:
            properties[key] = deepcopy(function[key])
    return properties


def build_import_template(snapshot: Mapping[str, Any], artifact: CodeArtifact) -> dict[str, Any]:
    validate_snapshot(snapshot)
    _require(all((artifact.bucket, artifact.key, artifact.version_id)), "artifact is incomplete")
    _require("InternalApiKey" in _stack_parameter_names(snapshot["stack"]), "InternalApiKey is not an existing stack parameter")
    template = deepcopy(snapshot["stack"]["template"])
    template.setdefault("Parameters", {})["InternalApiKey"] = {"Type": "String", "NoEcho": True, "MinLength": 1}
    resources = template.setdefault("Resources", {})
    table = snapshot["reservations_table"]
    resources["ReservationsTable"] = _retained(
        "AWS::DynamoDB::Table",
        {
            "TableName": table["TableName"],
            "BillingMode": table["BillingMode"],
            "AttributeDefinitions": deepcopy(table["AttributeDefinitions"]),
            "KeySchema": deepcopy(table["KeySchema"]),
        },
    )
    resources["QueryFunction"] = _retained("AWS::Lambda::Function", _function_properties(snapshot["function"], artifact))
    api_id = snapshot["api"]["id"]
    integration = snapshot["integration"]
    integration_properties = {"ApiId": api_id, **{key: deepcopy(value) for key, value in integration.items() if key != "IntegrationId"}}
    resources["QueryIntegration"] = _retained("AWS::ApiGatewayV2::Integration", integration_properties)
    for logical_id, contract in ROUTES_BY_LOGICAL_ID.items():
        properties: dict[str, Any] = {"ApiId": api_id, "RouteKey": contract.route_key, "Target": {"Fn::Join": ["", ["integrations/", {"Ref": "QueryIntegration"}]]}, "AuthorizationType": contract.authorization_type}
        if contract.authorization_type == "JWT":
            properties["AuthorizerId"] = snapshot["api"]["authorizer"]["AuthorizerId"]
        live_route = _route_lookup(snapshot)[contract.route_key]
        properties.update({key: deepcopy(value) for key, value in live_route.items() if key not in {"RouteId", "RouteKey", "Target", "AuthorizationType", "AuthorizerId"} and value is not None})
        resources[logical_id] = _retained("AWS::ApiGatewayV2::Route", properties)
    return template


def build_parameters_to_reuse(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_snapshot(snapshot)
    names = _stack_parameter_names(snapshot["stack"])
    _require("InternalApiKey" in names, "InternalApiKey is not an existing stack parameter")
    return [{"ParameterKey": name, "UsePreviousValue": True} for name in sorted(names)]


def _runtime_fingerprint(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    routes = _route_lookup(snapshot)
    return {
        "reservations_table": deepcopy(snapshot["reservations_table"]),
        "role": deepcopy(snapshot["role"]),
        "function": deepcopy(snapshot["function"]),
        "integration": deepcopy(snapshot["integration"]),
        "routes": {
            key: deepcopy(routes[key])
            for key in sorted(
                contract.route_key for contract in ROUTES_BY_LOGICAL_ID.values()
            )
        },
    }


def assert_runtime_unchanged(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    validate_snapshot(before)
    validate_snapshot(after)
    _require(_runtime_fingerprint(before) == _runtime_fingerprint(after), "runtime changed after import")


def validate_import_change_set(changes: list[Mapping[str, Any]], expected: list[Mapping[str, Any]]) -> None:
    expected_pairs = {(item["LogicalResourceId"], item["ResourceType"]) for item in expected}
    actual_pairs = set()
    for change in changes:
        resource_change = change.get("ResourceChange", {})
        if resource_change.get("Action") != "Import" or resource_change.get("Replacement") not in ("False", False, None):
            raise AdoptionError("change set must contain exactly 19 Import actions")
        actual_pairs.add((resource_change.get("LogicalResourceId"), resource_change.get("ResourceType")))
    _require(
        len(expected) == 19
        and len(expected_pairs) == 19
        and len(changes) == 19
        and actual_pairs == expected_pairs
        and len(actual_pairs) == 19,
        "change set must contain exactly 19 Import actions",
    )


def _role_continuity_with_retain(audited: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """Allow only the resource-level Retain delta needed by maintained SAM."""
    if current.get("DeletionPolicy") != "Retain" or current.get("UpdateReplacePolicy") != "Retain":
        return False
    baseline = deepcopy(dict(audited))
    target = deepcopy(dict(current))
    for key in ("DeletionPolicy", "UpdateReplacePolicy"):
        target.pop(key, None)
        if baseline.get(key) == "Retain":
            baseline.pop(key)
    return baseline == target


def _maintained_reservations_table_target() -> dict[str, Any]:
    return {
        "Type": "AWS::DynamoDB::Table",
        "DeletionPolicy": "Retain",
        "UpdateReplacePolicy": "Retain",
        "Properties": {
            "TableName": "PacificBioArchiveUploadReservations",
            "BillingMode": "PAY_PER_REQUEST",
            "AttributeDefinitions": [
                {"AttributeName": "reservation_key", "AttributeType": "S"}
            ],
            "KeySchema": [
                {"AttributeName": "reservation_key", "KeyType": "HASH"}
            ],
        },
    }


def _maintained_table_target(
    table_name: str,
    attribute_definitions: list[dict[str, str]],
    key_schema: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "Type": "AWS::DynamoDB::Table",
        "DeletionPolicy": "Retain",
        "UpdateReplacePolicy": "Retain",
        "Properties": {
            "TableName": table_name,
            "BillingMode": "PAY_PER_REQUEST",
            "AttributeDefinitions": deepcopy(attribute_definitions),
            "KeySchema": deepcopy(key_schema),
        },
    }


def _maintained_base_table_targets() -> dict[str, dict[str, Any]]:
    return {
        "FilesTable": _maintained_table_target(
            "PacificBioArchiveFiles",
            [{"AttributeName": "file_id", "AttributeType": "S"}],
            [{"AttributeName": "file_id", "KeyType": "HASH"}],
        ),
        "SubscriptionsTable": _maintained_table_target(
            "PacificBioArchiveSubscriptions",
            [
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "species", "AttributeType": "S"},
            ],
            [
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "species", "KeyType": "RANGE"},
            ],
        ),
        "NotificationsTable": _maintained_table_target(
            "PacificBioArchiveNotifications",
            [
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "notification_id", "AttributeType": "S"},
            ],
            [
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "notification_id", "KeyType": "RANGE"},
            ],
        ),
    }


def _maintained_role_target() -> dict[str, Any]:
    """Exact processed role contract from the maintained Member D template."""
    return {
        "Type": "AWS::IAM::Role",
        "DeletionPolicy": "Retain",
        "UpdateReplacePolicy": "Retain",
        "Properties": {
            "RoleName": "PacificBioArchive-QueryLambdaRole",
            "AssumeRolePolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            },
            "ManagedPolicyArns": [
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            ],
            "Policies": [
                {
                    "PolicyName": "QueryServiceAccess",
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
                                        "arn:${AWS::Partition}:lambda:"
                                        "${AWS::Region}:${AWS::AccountId}:"
                                        "function:${StorageDeleteFunctionName}"
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
                }
            ],
        },
    }


def _maintained_integration_target() -> dict[str, Any]:
    return {
        "Type": "AWS::ApiGatewayV2::Integration",
        "DeletionPolicy": "Retain",
        "UpdateReplacePolicy": "Retain",
        "Properties": {
            "ApiId": {"Ref": "ExistingHttpApiId"},
            "IntegrationType": "AWS_PROXY",
            "IntegrationMethod": "POST",
            "IntegrationUri": {
                "Fn::Sub": (
                    "arn:${AWS::Partition}:apigateway:${AWS::Region}:lambda:"
                    "path/2015-03-31/functions/${QueryFunction.Arn}/invocations"
                )
            },
            "PayloadFormatVersion": "2.0",
        },
    }


def _maintained_route_target(
    contract: RouteContract, *, retained: bool
) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "Type": "AWS::ApiGatewayV2::Route",
        "Properties": {
            "ApiId": {"Ref": "ExistingHttpApiId"},
            "RouteKey": contract.route_key,
            "AuthorizationType": contract.authorization_type,
            "Target": {"Fn::Sub": "integrations/${QueryIntegration}"},
        },
    }
    if contract.authorization_type == "JWT":
        resource["Properties"]["AuthorizerId"] = {
            "Ref": "ExistingJwtAuthorizerId"
        }
    if retained:
        resource["DeletionPolicy"] = "Retain"
        resource["UpdateReplacePolicy"] = "Retain"
    return resource


def _permission_logical_id(route_logical_id: str) -> str:
    _require(route_logical_id.endswith("Route"), "route logical ID is malformed")
    return f"{route_logical_id[:-5]}Permission"


def _maintained_permission_target(contract: RouteContract) -> dict[str, Any]:
    method, path = contract.route_key.split(" ", 1)
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


def _maintained_plain_resource_targets() -> dict[str, dict[str, Any]]:
    targets = {
        **_maintained_base_table_targets(),
        "ReservationsTable": _maintained_reservations_table_target(),
        "QueryLambdaRole": _maintained_role_target(),
        "QueryIntegration": _maintained_integration_target(),
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
    targets.update(
        {
            logical_id: _maintained_route_target(contract, retained=True)
            for logical_id, contract in ROUTES_BY_LOGICAL_ID.items()
        }
    )
    targets.update(
        {
            logical_id: _maintained_route_target(contract, retained=False)
            for logical_id, contract in OPTIONS_ROUTES_BY_LOGICAL_ID.items()
        }
    )
    for logical_id, contract in {
        **ROUTES_BY_LOGICAL_ID,
        **OPTIONS_ROUTES_BY_LOGICAL_ID,
    }.items():
        targets[_permission_logical_id(logical_id)] = (
            _maintained_permission_target(contract)
        )
    return targets


def _validate_maintained_query_function(resource: Any) -> None:
    _require(
        isinstance(resource, Mapping)
        and resource.get("Type") == "AWS::Lambda::Function"
        and resource.get("DeletionPolicy") == "Retain"
        and resource.get("UpdateReplacePolicy") == "Retain",
        "QueryFunction processed definition differs from maintained contract",
    )
    properties = resource.get("Properties")
    _require(
        isinstance(properties, Mapping),
        "QueryFunction processed definition differs from maintained contract",
    )
    expected = {
        "FunctionName": "PacificBioArchive-QueryLambda",
        "Role": {"Fn::GetAtt": ["QueryLambdaRole", "Arn"]},
        "Handler": "lambda_function.handler",
        "Runtime": "python3.12",
        "Timeout": 30,
        "MemorySize": 1024,
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
    }
    _require(
        all(properties.get(key) == value for key, value in expected.items()),
        "QueryFunction processed definition differs from maintained contract",
    )
    allowed = set(expected) | {"Code", "Tags", "Architectures", "EphemeralStorage"}
    _require(
        set(properties) <= allowed,
        "QueryFunction contains unsupported processed properties",
    )
    code = properties.get("Code")
    _require(
        isinstance(code, Mapping)
        and set(code) <= {"S3Bucket", "S3Key", "S3ObjectVersion"}
        and code.get("S3Bucket")
        and code.get("S3Key"),
        "QueryFunction code must be a packaged S3 artifact",
    )
    _require(
        properties.get("Architectures", ["x86_64"]) == ["x86_64"]
        and properties.get("EphemeralStorage", {"Size": 512}) == {"Size": 512},
        "QueryFunction architecture or ephemeral storage is unsupported",
    )
    tags = properties.get("Tags")
    _require(
        tags == [{"Key": "lambda:createdBy", "Value": "SAM"}],
        "QueryFunction processed tags are unsupported",
    )


def _normalize_packaged_code_uri(value: Any) -> dict[str, Any]:
    _require(
        isinstance(value, Mapping)
        and set(value) == {"Bucket", "Key", "Version"}
        and isinstance(value.get("Bucket"), str)
        and bool(value.get("Bucket"))
        and isinstance(value.get("Key"), str)
        and bool(value.get("Key"))
        and isinstance(value.get("Version"), str)
        and bool(value.get("Version")),
        "packaged QueryFunction CodeUri must pin an exact S3 object version",
    )
    return {
        "S3Bucket": value["Bucket"],
        "S3Key": value["Key"],
        "S3ObjectVersion": value["Version"],
    }


def validate_built_template(
    built: Mapping[str, Any], maintained: Mapping[str, Any]
) -> None:
    """Bind the SAM build template to the maintained repository template."""
    _require(
        isinstance(built, Mapping) and isinstance(maintained, Mapping),
        "SAM built template evidence is unavailable",
    )
    built_function = built.get("Resources", {}).get("QueryFunction", {})
    maintained_function = maintained.get("Resources", {}).get(
        "QueryFunction", {}
    )
    built_properties = (
        built_function.get("Properties", {})
        if isinstance(built_function, Mapping)
        else None
    )
    maintained_properties = (
        maintained_function.get("Properties", {})
        if isinstance(maintained_function, Mapping)
        else None
    )
    _require(
        isinstance(built_properties, Mapping)
        and isinstance(maintained_properties, Mapping)
        and built_properties.get("CodeUri") == "QueryFunction"
        and "CodeUri" in maintained_properties,
        "SAM built QueryFunction CodeUri is not the controlled build artifact",
    )
    normalized_built = deepcopy(dict(built))
    normalized_built["Resources"]["QueryFunction"]["Properties"][
        "CodeUri"
    ] = deepcopy(maintained_properties["CodeUri"])
    _require(
        normalized_built == maintained,
        "SAM built UPDATE template differs from the maintained source",
    )


def _change_set_parameter_map(value: Any) -> dict[str, Mapping[str, Any]]:
    _require(isinstance(value, list), "change-set parameters are unavailable")
    result: dict[str, Mapping[str, Any]] = {}
    for item in value:
        _require(
            isinstance(item, Mapping)
            and set(item)
            <= {
                "ParameterKey",
                "ParameterValue",
                "UsePreviousValue",
                "ResolvedValue",
            }
            and isinstance(item.get("ParameterKey"), str)
            and bool(item.get("ParameterKey"))
            and item["ParameterKey"] not in result,
            "change-set parameters are malformed or duplicated",
        )
        _require(
            "ResolvedValue" not in item,
            "resolved parameter values are not permitted",
        )
        result[item["ParameterKey"]] = item
    return result


def validate_hardening_function_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    expected_callback: str,
) -> None:
    """Prove the only effective Lambda configuration change is true -> false."""
    _require(
        expected_callback == "false",
        "hardening must explicitly disable legacy processing callbacks",
    )
    _require(
        isinstance(before, Mapping) and isinstance(after, Mapping),
        "hardening Lambda configuration evidence is unavailable",
    )
    before_copy = deepcopy(dict(before))
    after_copy = deepcopy(dict(after))
    before_environment = before_copy.get("Environment")
    after_environment = after_copy.get("Environment")
    _require(
        isinstance(before_environment, dict)
        and isinstance(after_environment, dict)
        and isinstance(before_environment.get("Variables"), dict)
        and isinstance(after_environment.get("Variables"), dict),
        "hardening Lambda environment evidence is malformed",
    )
    before_variables = before_environment["Variables"]
    after_variables = after_environment["Variables"]
    key = "ALLOW_LEGACY_PROCESSING_CALLBACKS"
    _require(
        before_variables.get(key) == "true" and after_variables.get(key) == "false",
        "hardening callback transition must be true to false",
    )
    before_variables.pop(key)
    after_variables.pop(key)
    _require(
        before_copy == after_copy,
        "hardening changes Lambda configuration beyond the callback switch",
    )


def validate_hardening_parameter_transition(
    current_parameters: Any,
    candidate_parameters: Any,
) -> None:
    """Prove the effective stack parameter delta is exactly callback true -> false."""
    _require(
        isinstance(current_parameters, list),
        "current stack parameters are unavailable",
    )
    current: dict[str, str] = {}
    for item in current_parameters:
        _require(
            isinstance(item, Mapping)
            and set(item) <= {"ParameterKey", "ParameterValue", "ResolvedValue"}
            and isinstance(item.get("ParameterKey"), str)
            and bool(item.get("ParameterKey"))
            and isinstance(item.get("ParameterValue"), str)
            and item["ParameterKey"] not in current,
            "current stack parameters are malformed or duplicated",
        )
        current[item["ParameterKey"]] = item["ParameterValue"]
    candidate = _change_set_parameter_map(candidate_parameters)
    _require(
        set(candidate) == set(current),
        "hardening candidate parameter set differs from the current stack",
    )
    callback = "AllowLegacyProcessingCallbacks"
    _require(
        current.get(callback) == "true",
        "current stack callback switch is not true",
    )
    for name, current_value in current.items():
        item = candidate[name]
        if name == "InternalApiKey":
            _require(
                item.get("UsePreviousValue") is True
                and "ParameterValue" not in item,
                "hardening must reuse the InternalApiKey value",
            )
            continue
        expected = "false" if name == callback else current_value
        _require(
            item.get("ParameterValue") == expected
            and item.get("UsePreviousValue") in (None, False),
            f"hardening changes unexpected stack parameter {name}",
        )


def validate_import_artifacts(
    processed: Mapping[str, Any],
    local_template: Mapping[str, Any],
    change_set_parameters: Any,
    local_parameters: Any,
    audited: Mapping[str, Any],
    artifact_bucket: str,
) -> CodeArtifact:
    """Rebuild and bind IMPORT evidence to the audited live Lambda digest."""
    validate_snapshot(audited)
    _require(
        isinstance(artifact_bucket, str) and bool(artifact_bucket),
        "IMPORT artifact bucket is unavailable",
    )
    code = (
        local_template.get("Resources", {})
        .get("QueryFunction", {})
        .get("Properties", {})
        .get("Code")
        if isinstance(local_template, Mapping)
        else None
    )
    _require(
        isinstance(code, Mapping)
        and set(code) == {"S3Bucket", "S3Key", "S3ObjectVersion"}
        and code.get("S3Bucket") == artifact_bucket
        and isinstance(code.get("S3ObjectVersion"), str)
        and bool(code["S3ObjectVersion"]),
        "generated IMPORT code artifact is malformed or uses another bucket",
    )
    encoded_digest = audited.get("function", {}).get("CodeSha256")
    try:
        raw_digest = base64.b64decode(encoded_digest, validate=True)
    except (TypeError, ValueError, binascii.Error):
        raise AdoptionError("audited Lambda code digest is malformed") from None
    _require(
        len(raw_digest) == 32
        and base64.b64encode(raw_digest).decode("ascii") == encoded_digest,
        "audited Lambda code digest is malformed",
    )
    expected_key = f"member-d/adoption/{raw_digest.hex()}.zip"
    _require(
        code.get("S3Key") == expected_key,
        "generated IMPORT artifact key differs from the audited Lambda digest",
    )
    artifact = CodeArtifact(
        bucket=artifact_bucket,
        key=expected_key,
        version_id=code["S3ObjectVersion"],
    )
    expected_template = build_import_template(audited, artifact)
    _require(
        isinstance(processed, Mapping)
        and isinstance(local_template, Mapping)
        and local_template == expected_template
        and processed == expected_template,
        "IMPORT local or processed template differs from audited reconstruction",
    )
    expected_parameters = build_parameters_to_reuse(audited)
    _require(
        isinstance(local_parameters, list)
        and local_parameters == expected_parameters,
        "generated import parameters are unavailable",
    )
    expected_keys: set[str] = set()
    for item in expected_parameters:
        _require(
            isinstance(item, Mapping)
            and set(item) == {"ParameterKey", "UsePreviousValue"}
            and isinstance(item.get("ParameterKey"), str)
            and bool(item.get("ParameterKey"))
            and item.get("UsePreviousValue") is True
            and item["ParameterKey"] not in expected_keys,
            "generated import parameters are malformed",
        )
        expected_keys.add(item["ParameterKey"])
    actual = _change_set_parameter_map(change_set_parameters)
    _require(
        set(actual) == expected_keys
        and all(
            item.get("UsePreviousValue") is True
            and "ParameterValue" not in item
            for item in actual.values()
        ),
        "IMPORT must reuse every existing parameter without a value override",
    )
    return artifact


def validate_update_artifacts(
    processed: Mapping[str, Any],
    built: Mapping[str, Any],
    packaged: Mapping[str, Any],
    maintained: Mapping[str, Any],
    change_set_parameters: Any,
    expected_values: Mapping[str, str],
    artifact: CodeArtifact,
) -> None:
    """Bind UPDATE source -> SAM build -> package -> processed evidence."""
    _require(
        all(
            isinstance(item, Mapping)
            for item in (processed, built, packaged, maintained)
        ),
        "UPDATE template evidence is unavailable",
    )
    built_function = built.get("Resources", {}).get("QueryFunction", {})
    packaged_function = packaged.get("Resources", {}).get("QueryFunction", {})
    maintained_function = maintained.get("Resources", {}).get("QueryFunction", {})
    _require(
        isinstance(built_function, Mapping)
        and isinstance(packaged_function, Mapping)
        and isinstance(maintained_function, Mapping),
        "built or packaged QueryFunction is unavailable",
    )
    built_properties = built_function.get("Properties", {})
    packaged_properties = packaged_function.get("Properties", {})
    maintained_properties = maintained_function.get("Properties", {})
    _require(
        isinstance(built_properties, Mapping)
        and isinstance(packaged_properties, Mapping)
        and isinstance(maintained_properties, Mapping)
        and "CodeUri" in built_properties
        and "CodeUri" in packaged_properties
        and "CodeUri" in maintained_properties,
        "built or packaged QueryFunction CodeUri is unavailable",
    )
    validate_built_template(built, maintained)
    packaged_code_uri = packaged_properties["CodeUri"]
    expected_packaged_code_uri = {
        "Bucket": artifact.bucket,
        "Key": artifact.key,
        "Version": artifact.version_id,
    }
    _require(
        packaged_code_uri == expected_packaged_code_uri,
        "packaged QueryFunction CodeUri differs from the verified artifact",
    )
    expected_code = _normalize_packaged_code_uri(packaged_code_uri)
    normalized_packaged = deepcopy(dict(packaged))
    normalized_packaged["Resources"]["QueryFunction"]["Properties"][
        "CodeUri"
    ] = deepcopy(built_properties["CodeUri"])
    _require(
        normalized_packaged == built,
        "packaged UPDATE template differs from the controlled SAM build",
    )
    packaged_top = {
        key: deepcopy(value)
        for key, value in packaged.items()
        if key not in {"Transform", "Resources"}
    }
    processed_top = {
        key: deepcopy(value)
        for key, value in processed.items()
        if key not in {"Transform", "Resources"}
    }
    _require(
        processed_top == packaged_top
        and (
            "Transform" not in processed
            or processed.get("Transform") == maintained.get("Transform")
        ),
        "UPDATE processed template top-level sections differ from packaged template",
    )
    processed_code = (
        processed.get("Resources", {})
        .get("QueryFunction", {})
        .get("Properties", {})
        .get("Code")
    )
    _require(
        processed_code == expected_code,
        "QueryFunction processed Code differs from packaged artifact",
    )

    required_names = {
        "ExistingHttpApiId",
        "ExistingJwtAuthorizerId",
        "QueryInputBucketName",
        "StorageDeleteFunctionName",
        "InferenceApiBaseUrl",
        "AllowLegacyProcessingCallbacks",
    }
    _require(
        isinstance(expected_values, Mapping)
        and set(expected_values) == required_names
        and all(isinstance(value, str) and value for value in expected_values.values()),
        "expected UPDATE parameter values are incomplete",
    )
    actual = _change_set_parameter_map(change_set_parameters)
    _require(
        required_names | {"InternalApiKey"} <= set(actual),
        "UPDATE change set is missing an explicit parameter",
    )
    for name, expected in expected_values.items():
        item = actual[name]
        _require(
            item.get("ParameterValue") == expected
            and item.get("UsePreviousValue") in (None, False),
            f"UPDATE parameter {name} differs from the operator-approved value",
        )
    internal = actual["InternalApiKey"]
    _require(
        internal.get("UsePreviousValue") is True
        and "ParameterValue" not in internal,
        "InternalApiKey must use its previous NoEcho value",
    )
    _require(
        expected_values["AllowLegacyProcessingCallbacks"] in {"true", "false"},
        "AllowLegacyProcessingCallbacks must be explicitly true or false",
    )
    optional_defaults = {
        "AllowedOrigin": "http://localhost:3000",
        "PublicAllowedOrigin": "https://quinby8930.github.io",
        "NotificationEmailEndpoint": "",
    }
    _require(
        set(actual)
        <= required_names | {"InternalApiKey"} | set(optional_defaults),
        "UPDATE change set contains an unexpected parameter",
    )
    for name in set(actual) & set(optional_defaults):
        item = actual[name]
        _require(
            item.get("ParameterValue") == optional_defaults[name]
            and item.get("UsePreviousValue") in (None, False),
            f"UPDATE default parameter {name} is not maintained",
        )


def _approved_role_reconciliation(
    audited_role: Mapping[str, Any], current: Mapping[str, Any]
) -> bool:
    account = audited_role.get("account")
    region = audited_role.get("region")
    if not isinstance(account, str) or not isinstance(region, str):
        return False
    try:
        _validate_role_drift(audited_role, account, region)
    except AdoptionError:
        return False
    baseline = deepcopy(audited_role.get("processed_definition"))
    target = _maintained_role_target()
    if not isinstance(baseline, dict):
        return False
    for resource in (baseline, target):
        resource.pop("DeletionPolicy", None)
        resource.pop("UpdateReplacePolicy", None)
        properties = resource.get("Properties")
        if not isinstance(properties, dict):
            return False
        properties.pop("Policies", None)
    return (
        audited_role.get("drift", {}).get("status") == "MODIFIED"
        and baseline == target
        and current == _maintained_role_target()
    )


def validate_update_change_set(
    changes: list[Mapping[str, Any]],
    processed: Mapping[str, Any],
    audited_role: Mapping[str, Any] | None = None,
    *,
    hardening_only: bool = False,
) -> None:
    resources = processed.get("Resources", {})
    _require(isinstance(resources, Mapping) and "QueryFunctionRole" not in resources, "implicit role QueryFunctionRole is forbidden")
    audited_drift: Mapping[str, Any] | None = None
    if isinstance(audited_role, Mapping) and "drift" in audited_role:
        account = audited_role.get("account")
        region = audited_role.get("region")
        _require(
            isinstance(account, str) and isinstance(region, str),
            "QueryLambdaRole audited drift scope is malformed",
        )
        _validate_role_drift(audited_role, account, region)
        candidate_drift = audited_role.get("drift")
        _require(
            isinstance(candidate_drift, Mapping),
            "QueryLambdaRole audited drift is malformed",
        )
        audited_drift = candidate_drift
    plain_targets = _maintained_plain_resource_targets()
    expected_logical_ids = set(plain_targets) | {"QueryFunction"}
    _require(
        set(resources) == expected_logical_ids,
        "processed template resource set differs from maintained contract",
    )
    for logical_id, target in plain_targets.items():
        _require(
            resources.get(logical_id) == target,
            f"{logical_id} processed definition differs from maintained contract",
        )
    _validate_maintained_query_function(resources.get("QueryFunction"))

    protected = _BASE_MANAGED | _ADOPTED_LOGICAL_IDS
    additions = expected_logical_ids - protected
    expected_types = {
        logical_id: target["Type"]
        for logical_id, target in plain_targets.items()
    }
    expected_types["QueryFunction"] = "AWS::Lambda::Function"
    if hardening_only:
        _require(
            len(changes) == 1
            and isinstance(changes[0], Mapping)
            and changes[0].get("ResourceChange", {}).get("Action")
            == "Modify"
            and changes[0].get("ResourceChange", {}).get(
                "LogicalResourceId"
            )
            == "QueryFunction"
            and changes[0].get("ResourceChange", {}).get("ResourceType")
            == "AWS::Lambda::Function"
            and changes[0].get("ResourceChange", {}).get("Replacement")
            in ("False", False),
            "hardening UPDATE must contain exactly one in-place QueryFunction Modify",
        )
    role_changes: list[Mapping[str, Any]] = []
    changed_logical_ids: set[str] = set()
    for change in changes:
        resource_change = change.get("ResourceChange", {})
        logical_id = resource_change.get("LogicalResourceId")
        action = resource_change.get("Action")
        _require(
            isinstance(logical_id, str)
            and logical_id in expected_logical_ids
            and logical_id not in changed_logical_ids,
            "UPDATE change set contains an unexpected or duplicate resource",
        )
        changed_logical_ids.add(logical_id)
        if logical_id in protected and (
            action != "Modify"
            or resource_change.get("Replacement") not in ("False", False)
        ):
            raise AdoptionError(f"replacement or removal of {logical_id} is forbidden")
        if logical_id in additions and (
            action != "Add"
            or resource_change.get("Replacement") not in (None, "False", False)
        ):
            raise AdoptionError(f"non-Add change for {logical_id} is forbidden")
        _require(
            resource_change.get("ResourceType") == expected_types[logical_id],
            f"resource type mismatch for {logical_id}",
        )
        if logical_id == "QueryLambdaRole":
            role_changes.append(resource_change)
        if logical_id == "QueryLambdaRole" and action == "Modify":
            definition = audited_role.get("processed_definition") if isinstance(audited_role, Mapping) else None
            current_role = resources.get("QueryLambdaRole", {})
            _require(
                isinstance(definition, Mapping)
                and (
                    _role_continuity_with_retain(definition, current_role)
                    or (
                        isinstance(audited_role, Mapping)
                        and _approved_role_reconciliation(
                            audited_role,
                            current_role,
                        )
                    )
                ),
                "QueryLambdaRole Modify lacks audited role continuity",
            )
    _require(
        len(role_changes) <= 1,
        "QueryLambdaRole change is duplicated",
    )
    if audited_drift is not None and audited_drift.get("status") == "MODIFIED":
        _require(
            resources.get("QueryLambdaRole") == _maintained_role_target()
            and len(role_changes) == 1
            and role_changes[0].get("Action") == "Modify"
            and role_changes[0].get("Replacement") in ("False", False),
            "QueryLambdaRole approved drift must be reconciled by one in-place Modify",
        )
