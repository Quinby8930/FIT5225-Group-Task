"""Pure, fail-closed descriptions for the Member D import change set.

This module deliberately has no AWS, process, filesystem, or network dependency.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
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
_ADOPTED_LOGICAL_IDS = {"QueryFunction", "QueryIntegration", *ROUTES_BY_LOGICAL_ID}
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


def _validate_function(function: Mapping[str, Any]) -> None:
    _require(function.get("FunctionName") == "PacificBioArchive-QueryLambda", "function name mismatch")
    _require(function.get("PackageType") == "Zip", "package type is not Zip")
    _require(function.get("Runtime") == "python3.12", "function runtime mismatch")
    _require(function.get("Handler") == "lambda_function.handler", "function handler mismatch")
    _require(function.get("Timeout") == 30 and function.get("MemorySize") == 512, "function sizing mismatch")
    _require(function.get("Role", "").endswith(":role/PacificBioArchive-QueryLambdaRole"), "function role mismatch")
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
    _require(isinstance(statement, Mapping) and set(statement) == {"Effect", "Principal", "Action", "Resource"}, "function resource policy cannot be preserved")
    _require(statement.get("Effect") == "Allow" and statement.get("Principal") == {"Service": "apigateway.amazonaws.com"} and statement.get("Action") == "lambda:InvokeFunction", "function resource policy cannot be preserved")
    _require(isinstance(statement.get("Resource"), str) and statement["Resource"].endswith(":function:PacificBioArchive-QueryLambda"), "function resource policy cannot be preserved")


def validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Reject any state that cannot be imported without changing live traffic."""
    caller = snapshot.get("caller", {})
    arn = caller.get("Arn") if isinstance(caller, Mapping) else None
    account = caller.get("Account") if isinstance(caller, Mapping) else None
    _require(isinstance(arn, str) and not arn.endswith(":root"), "Root caller is not permitted")
    _require(arn.endswith("user/fit5225-cli-deployer"), "caller must be user/fit5225-cli-deployer")
    _require(isinstance(account, str) and arn.split(":")[4] == account, "caller account does not match ARN")
    stack = snapshot.get("stack", {})
    _require(isinstance(stack, Mapping) and stack.get("status") in _STABLE_STACK_STATUSES, "stack is not in an import-safe stable state")
    managed = stack.get("managed")
    _require(isinstance(managed, Mapping) and set(managed) in (_BASE_MANAGED, _BASE_MANAGED | _ADOPTED_LOGICAL_IDS), "managed resource set mismatch")
    template = stack.get("template", {})
    resources = template.get("Resources", {}) if isinstance(template, Mapping) else {}
    _require(isinstance(resources, Mapping) and resources.get("QueryLambdaRole", {}).get("Type") == "AWS::IAM::Role", "stack-owned QueryLambdaRole missing")
    schemas = snapshot.get("type_schemas")
    _require(isinstance(schemas, Mapping), "primary identifier schemas missing")
    for resource_type, expected in _REQUIRED_TYPE_SCHEMAS.items():
        _require(schemas.get(resource_type) == expected, f"primary identifier schema mismatch for {resource_type}")
    _require(not snapshot.get("owned_physical_ids"), "candidate resource is already owned by another stack")
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
    _validate_function(function)
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


def build_resources_to_import(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_snapshot(snapshot)
    api_id = snapshot["api"]["id"]
    manifest = [
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
        properties[key] = deepcopy(function[key])
    return properties


def build_import_template(snapshot: Mapping[str, Any], artifact: CodeArtifact) -> dict[str, Any]:
    validate_snapshot(snapshot)
    _require(all((artifact.bucket, artifact.key, artifact.version_id)), "artifact is incomplete")
    _require("InternalApiKey" in _stack_parameter_names(snapshot["stack"]), "InternalApiKey is not an existing stack parameter")
    template = deepcopy(snapshot["stack"]["template"])
    template.setdefault("Parameters", {})["InternalApiKey"] = {"Type": "String", "NoEcho": True, "MinLength": 1}
    resources = template.setdefault("Resources", {})
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
    return {"function": deepcopy(snapshot["function"]), "integration": deepcopy(snapshot["integration"]), "routes": {key: deepcopy(routes[key]) for key in sorted(contract.route_key for contract in ROUTES_BY_LOGICAL_ID.values())}}


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
            raise AdoptionError("change set must contain exactly 18 Import actions")
        actual_pairs.add((resource_change.get("LogicalResourceId"), resource_change.get("ResourceType")))
    _require(len(changes) == 18 and actual_pairs == expected_pairs and len(actual_pairs) == 18, "change set must contain exactly 18 Import actions")


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


def validate_update_change_set(changes: list[Mapping[str, Any]], processed: Mapping[str, Any], audited_role: Mapping[str, Any] | None = None) -> None:
    resources = processed.get("Resources", {})
    _require(isinstance(resources, Mapping) and "QueryFunctionRole" not in resources, "implicit role QueryFunctionRole is forbidden")
    protected = {"QueryFunction", "QueryIntegration", "QueryLambdaRole", *ROUTES_BY_LOGICAL_ID}
    _require(protected <= set(resources), "processed template is missing a protected adopted resource")
    role = resources.get("QueryLambdaRole", {})
    function = resources.get("QueryFunction", {})
    _require(function.get("Properties", {}).get("Role") == {"Fn::GetAtt": ["QueryLambdaRole", "Arn"]}, "QueryFunction role rebinding is forbidden")
    for change in changes:
        resource_change = change.get("ResourceChange", {})
        logical_id = resource_change.get("LogicalResourceId")
        action = resource_change.get("Action")
        if logical_id in protected and (action == "Remove" or resource_change.get("Replacement") not in ("False", False)):
            raise AdoptionError(f"replacement or removal of {logical_id} is forbidden")
        if logical_id == "QueryLambdaRole" and action not in (None, "Modify"):
            raise AdoptionError("QueryLambdaRole must be unchanged or an in-place modify")
        if logical_id == "QueryLambdaRole" and action == "Modify":
            definition = audited_role.get("processed_definition") if isinstance(audited_role, Mapping) else None
            _require(isinstance(definition, Mapping) and _role_continuity_with_retain(definition, resources.get("QueryLambdaRole", {})), "QueryLambdaRole Modify lacks audited role continuity")
