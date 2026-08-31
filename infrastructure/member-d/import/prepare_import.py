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
import subprocess
from functools import wraps
from urllib.request import urlopen
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping
from zipfile import BadZipFile, ZipFile

from adoption import (
    AdoptionError,
    CodeArtifact,
    ROUTES_BY_LOGICAL_ID,
    assert_runtime_unchanged,
    build_import_template,
    build_parameters_to_reuse,
    build_resources_to_import,
    validate_import_change_set,
    validate_snapshot,
    validate_update_change_set,
)


class AwsCli:
    """Argument-list AWS CLI adapter; stdout and stderr are never echoed."""

    def json(self, *args: str) -> Any:
        try:
            completed = subprocess.run(["aws", *args, "--output", "json", "--no-cli-pager"], check=True, capture_output=True, text=True)
            return json.loads(completed.stdout)
        except (subprocess.SubprocessError, json.JSONDecodeError):
            raise AdoptionError("AWS CLI query failed") from None

    def run(self, *args: str) -> Any:
        try:
            completed = subprocess.run(["aws", *args, "--output", "json", "--no-cli-pager"], check=True, capture_output=True, text=True)
            return json.loads(completed.stdout or "{}")
        except (subprocess.SubprocessError, json.JSONDecodeError):
            raise AdoptionError("AWS CLI command failed") from None


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
    raw_variables = raw_environment.get("Variables", {}) if isinstance(raw_environment, Mapping) else {}
    if not isinstance(raw_variables, Mapping):
        raise AdoptionError("function environment is malformed")
    names = sorted(raw_variables)
    safe = {name: raw_variables[name] for name in _SAFE_ENVIRONMENT_NAMES if name in raw_variables}
    # Construct from explicit fields only so the complete environment map cannot
    # accidentally become part of the snapshot or an exception.
    result = {key: configuration.get(key) for key in (
        "FunctionName", "Runtime", "Handler", "Role", "Timeout", "MemorySize", "Description", "SnapStart",
        "PackageType", "Architectures", "Layers", "EphemeralStorage", "VpcConfig",
        "FileSystemConfigs", "KmsKeyArn", "DeadLetterConfig", "TracingConfig",
        "LoggingConfig", "CodeSigningConfigArn", "RuntimeManagementConfig",
        "ReservedConcurrentExecutions", "CodeSha256",
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
        "owned_physical_ids": set(),
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


@_sanitize_audit_errors
def collect_snapshot(cli: AwsCli, config: AuditConfig) -> dict[str, Any]:
    caller = cli.json("sts", "get-caller-identity", "--region", config.region)
    arn = caller.get("Arn") if isinstance(caller, Mapping) else None
    account = caller.get("Account") if isinstance(caller, Mapping) else None
    if arn == f"arn:aws:iam::{account}:root":
        raise AdoptionError("Root caller is not permitted")
    if not isinstance(account, str) or arn != f"arn:aws:iam::{account}:user/fit5225-cli-deployer":
        raise AdoptionError("caller must be exact IAM user/fit5225-cli-deployer")
    stacks = cli.json("cloudformation", "describe-stacks", "--stack-name", config.stack, "--region", config.region)
    stack_items = stacks.get("Stacks", []) if isinstance(stacks, Mapping) else []
    if len(stack_items) != 1:
        raise AdoptionError("stack identity could not be verified")
    stack_view = stack_items[0]
    processed_response = cli.json("cloudformation", "get-template", "--stack-name", config.stack, "--template-stage", "Processed", "--region", config.region)
    template = processed_response.get("TemplateBody") if isinstance(processed_response, Mapping) else None
    if isinstance(template, str):
        try:
            template = json.loads(template)
        except json.JSONDecodeError as error:
            raise AdoptionError("processed template is not JSON") from None
    if not isinstance(template, Mapping):
        raise AdoptionError("processed template is unavailable")
    summaries = cli.json("cloudformation", "list-stack-resources", "--stack-name", config.stack, "--region", config.region)
    resource_summaries = summaries.get("StackResourceSummaries", []) if isinstance(summaries, Mapping) else []
    managed = {item.get("LogicalResourceId"): item.get("PhysicalResourceId") for item in resource_summaries if isinstance(item, Mapping)}
    expected_managed = {"FilesTable", "SubscriptionsTable", "NotificationsTable", "QueryLambdaRole"}
    adopted_managed = expected_managed | {"QueryFunction", "QueryIntegration", *ROUTES_BY_LOGICAL_ID}
    if set(managed) not in (expected_managed, adopted_managed) or any(not managed[key] for key in managed):
        raise AdoptionError("managed resource set mismatch")
    active = cli.json("cloudformation", "list-stacks", "--stack-status-filter", "CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE", "IMPORT_COMPLETE", "--region", config.region)
    other_stack_physical_ids: set[str] = set()
    for other in active.get("StackSummaries", []) if isinstance(active, Mapping) else []:
        other_name = other.get("StackName") if isinstance(other, Mapping) else None
        if other_name and other_name != config.stack:
            other_resources = cli.json("cloudformation", "list-stack-resources", "--stack-name", other_name, "--region", config.region)
            other_stack_physical_ids.update(str(item.get("PhysicalResourceId")) for item in other_resources.get("StackResourceSummaries", []) if isinstance(item, Mapping) and item.get("PhysicalResourceId"))
    fields = ["FunctionName", "Runtime", "Handler", "Role", "Timeout", "MemorySize", "Description", "SnapStart", "PackageType", "Architectures", "Layers", "EphemeralStorage", "VpcConfig", "FileSystemConfigs", "KmsKeyArn", "DeadLetterConfig", "TracingConfig", "LoggingConfig", "CodeSigningConfigArn", "RuntimeManagementConfig", "ReservedConcurrentExecutions", "CodeSha256", "Environment"]
    query = "{" + ",".join([*(f"{field}: {field}" for field in fields), *(f"{name}: Environment.Variables.{name}" for name in _SAFE_ENVIRONMENT_NAMES)]) + "}"
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
    concurrency = cli.json("lambda", "get-function-concurrency", "--function-name", config.function, "--region", config.region)
    function["ReservedConcurrentExecutions"] = concurrency.get("ReservedConcurrentExecutions") if isinstance(concurrency, Mapping) else None
    runtime_management = cli.json("lambda", "get-runtime-management-config", "--function-name", config.function, "--region", config.region)
    if isinstance(runtime_management, Mapping) and runtime_management:
        function["RuntimeManagementConfig"] = _sanitized_runtime_management(runtime_management)
    # The signed deployment URL is deliberately held only in this local object.
    cli.json("lambda", "get-function", "--function-name", config.function, "--region", config.region, "--query", "Code")
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
    inline = {name: cli.json("iam", "get-role-policy", "--role-name", role_name, "--policy-name", name) for name in inline_names}
    tags = cli.json("iam", "list-role-tags", "--role-name", role_name).get("Tags", [])
    role_view = {"role_name": role.get("RoleName"), "path": role.get("Path"), "trust_policy": role.get("AssumeRolePolicyDocument"), "permissions_boundary": role.get("PermissionsBoundary"), "managed_policies": attached, "inline_policies": inline, "tags": tags}
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
    live_inline = {(name, _canonical(value.get("PolicyDocument"))) for name, value in inline.items()}
    processed_inline = {(item.get("PolicyName"), _canonical(item.get("PolicyDocument"))) for item in processed_properties.get("Policies", [])}
    if live_inline != processed_inline:
        raise AdoptionError("QueryLambdaRole inline policies differ from processed template")
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
    candidate_ids = {config.function, config.integration}
    contracted_keys = {contract.route_key for contract in ROUTES_BY_LOGICAL_ID.values()}
    candidate_ids.update(str(route.get("RouteId")) for route in routes if route.get("RouteKey") in contracted_keys and route.get("RouteId"))
    owned_physical_ids = other_stack_physical_ids & candidate_ids
    schemas: dict[str, Any] = {}
    for resource_type in ("AWS::Lambda::Function", "AWS::ApiGatewayV2::Integration", "AWS::ApiGatewayV2::Route"):
        type_response = cli.json("cloudformation", "describe-type", "--type", "RESOURCE", "--type-name", resource_type, "--region", config.region)
        schema = type_response.get("Schema") if isinstance(type_response, Mapping) else None
        try:
            schemas[resource_type] = json.loads(schema)["primaryIdentifier"] if isinstance(schema, str) else schema["primaryIdentifier"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise AdoptionError("primary identifier schema is unavailable") from None
    snapshot = {
        "caller": {"Arn": arn, "Account": caller.get("Account")}, "region": config.region,
        "stack": {"name": config.stack, "status": stack_view.get("StackStatus"), "parameters": [parameter.get("ParameterKey") for parameter in stack_view.get("Parameters", []) if isinstance(parameter, Mapping)], "template": dict(template), "managed": managed},
        "api": {"id": config.api, "stage": dict(stage), "authorizer": dict(authorizer), "routes": routes},
        "function": function, "integration": dict(integration), "type_schemas": schemas,
        "owned_physical_ids": owned_physical_ids, "role": role_view,
    }
    validate_snapshot(snapshot)
    return snapshot


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
    code = code_response.get("Code") if isinstance(code_response, Mapping) else None
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
        policy_status = cli.json("s3api", "get-bucket-policy-status", "--bucket", artifact_bucket)
        cli.json("s3api", "head-bucket", "--bucket", artifact_bucket)
        if expected_location != region or policy_status.get("PolicyStatus", {}).get("IsPublic") is not False or not all(public.get(name) is True for name in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")) or not encryption.get("ServerSideEncryptionConfiguration") or versioning.get("Status") != "Enabled":
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
    validator = subcommands.add_parser("validate-change-set")
    validator.add_argument("--region", required=True)
    validator.add_argument("--stack", required=True)
    validator.add_argument("--change-set", required=True)
    validator.add_argument("--expected-type", choices=("IMPORT", "UPDATE"), required=True)
    validator.add_argument("--workdir", default=".work")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cli = AwsCli()
    if args.command in ("audit", "prepare"):
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
    change_set = cli.json("cloudformation", "describe-change-set", "--stack-name", args.stack, "--change-set-name", args.change_set, "--region", args.region)
    if change_set.get("ChangeSetType") != args.expected_type:
        raise AdoptionError("change set type does not match --expected-type")
    snapshot_path = Path(args.workdir) / "sanitized-snapshot.json"
    audited = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if args.expected_type == "IMPORT":
        expected = build_resources_to_import(audited)
        validate_import_change_set(change_set.get("Changes", []), expected)
    else:
        processed = cli.json("cloudformation", "get-template", "--stack-name", args.stack, "--change-set-name", args.change_set, "--template-stage", "Processed", "--region", args.region).get("TemplateBody", {})
        validate_update_change_set(change_set.get("Changes", []), processed, audited.get("role"))
    print("change set validated; no CloudFormation change set was created or executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
