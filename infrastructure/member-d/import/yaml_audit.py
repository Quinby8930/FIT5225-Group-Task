"""Fail-closed, supply-chain-pinned CloudFormation YAML support.

This module deliberately imports only the Python standard library until the
committed parser archive has been checked byte-for-byte against its lock file.
The archive contains a renamed, pure-Python copy of PyYAML so an ambient
``yaml`` installation can never satisfy the audit dependency.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import math
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from importlib.machinery import ModuleSpec
from types import CodeType, MappingProxyType, ModuleType
from typing import Any, Mapping, NoReturn
from zipfile import BadZipFile, ZIP_STORED, ZipFile


class YamlAuditError(ValueError):
    """The controlled parser or template input failed a security gate."""


_HERE = Path(__file__).resolve().parent
DEFAULT_ARCHIVE = _HERE / "member-d-yaml-audit.pyz"
DEFAULT_MANIFEST = _HERE / "member-d-yaml-audit.lock.json"

UPSTREAM_NAME = "PyYAML"
UPSTREAM_VERSION = "6.0.3"
UPSTREAM_LICENSE = "MIT"
UPSTREAM_SDIST_FILENAME = "pyyaml-6.0.3.tar.gz"
UPSTREAM_SDIST_SIZE = 130_960
UPSTREAM_SDIST_SHA256 = (
    "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
)
UPSTREAM_SDIST_URL = (
    "https://files.pythonhosted.org/packages/05/8e/"
    "961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/"
    "pyyaml-6.0.3.tar.gz"
)
CONTROLLED_ARCHIVE_SIZE = 217_611
CONTROLLED_ARCHIVE_SHA256 = (
    "b1e2b30684bd9dc27e35b868c4629c3ee92f9440b84b832c20c006a8db68f9ca"
)

MAX_MANIFEST_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 64
MAX_ARCHIVE_ENTRY_BYTES = 512 * 1024
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_DECOMPRESSION_RATIO = 100
MAX_TEMPLATE_BYTES = 1_048_576
MAX_YAML_NODES = 100_000
MAX_YAML_DEPTH = 100
MAX_COLLECTION_MEMBERS = 100_000
MAX_SCALAR_BYTES = 1_048_576
MAX_NUMERIC_SCALAR_BYTES = 128

_MODULE_FILES = frozenset(
    {
        "__init__.py",
        "composer.py",
        "constructor.py",
        "dumper.py",
        "emitter.py",
        "error.py",
        "events.py",
        "loader.py",
        "nodes.py",
        "parser.py",
        "reader.py",
        "representer.py",
        "resolver.py",
        "scanner.py",
        "serializer.py",
        "tokens.py",
    }
)
_EXPECTED_ARCHIVE_ENTRIES = frozenset(
    {f"_pba_yaml/{name}" for name in _MODULE_FILES}
    | {
        "THIRD_PARTY_LICENSES/PyYAML-6.0.3-LICENSE.txt",
        "THIRD_PARTY_NOTICES.md",
    }
)

_MANIFEST_TOP_KEYS = frozenset(
    {"schema_version", "artifact", "upstream", "zip", "files"}
)
_ARTIFACT_KEYS = frozenset({"name", "size", "sha256"})
_UPSTREAM_KEYS = frozenset(
    {
        "name",
        "version",
        "license",
        "sdist_filename",
        "sdist_size",
        "sdist_sha256",
        "sdist_url",
    }
)
_ZIP_KEYS = frozenset(
    {
        "compression",
        "entry_count",
        "create_system",
        "timestamp",
        "file_mode",
        "comment",
        "max_entries",
        "max_entry_uncompressed_bytes",
        "max_total_uncompressed_bytes",
        "max_decompression_ratio",
    }
)
_FILE_KEYS = frozenset({"size", "sha256", "source_member"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DRIVE_RE = re.compile(r"[A-Za-z]:")

_SHORT_INTRINSICS = {
    "!And": "Fn::And",
    "!Base64": "Fn::Base64",
    "!Cidr": "Fn::Cidr",
    "!Condition": "Condition",
    "!Equals": "Fn::Equals",
    "!FindInMap": "Fn::FindInMap",
    "!GetAtt": "Fn::GetAtt",
    "!GetAZs": "Fn::GetAZs",
    "!If": "Fn::If",
    "!ImportValue": "Fn::ImportValue",
    "!Join": "Fn::Join",
    "!Length": "Fn::Length",
    "!Not": "Fn::Not",
    "!Or": "Fn::Or",
    "!Ref": "Ref",
    "!Select": "Fn::Select",
    "!Split": "Fn::Split",
    "!Sub": "Fn::Sub",
    "!ToJsonString": "Fn::ToJsonString",
    "!Transform": "Fn::Transform",
}
_ALLOWED_CORE_TAGS = frozenset(
    {
        "tag:yaml.org,2002:str",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:bool",
        "tag:yaml.org,2002:null",
        "tag:yaml.org,2002:map",
        "tag:yaml.org,2002:seq",
    }
)

class _VerifiedArchive:
    """Immutable binding between verified archive bytes and module sources."""

    __slots__ = ("archive_path", "key", "manifest", "module_sources")

    def __init__(
        self,
        *,
        archive_path: Path,
        artifact_sha: str,
        manifest: Mapping[str, Any],
        module_sources: Mapping[str, bytes],
    ) -> None:
        self.archive_path = archive_path
        self.key = (os.path.normcase(str(archive_path)), artifact_sha)
        self.manifest = MappingProxyType(dict(manifest))
        self.module_sources = MappingProxyType(dict(module_sources))


class _VerifiedArchiveImporter:
    """Import only code already read and authenticated by the archive gate."""

    __slots__ = ("_records",)

    def __init__(self, verified: _VerifiedArchive) -> None:
        records: dict[str, tuple[CodeType, str, bool]] = {}
        for entry_name, source in verified.module_sources.items():
            relative = entry_name.removeprefix("_pba_yaml/")
            is_package = relative == "__init__.py"
            if is_package:
                module_name = "_pba_yaml"
            else:
                module_name = f"_pba_yaml.{relative.removesuffix('.py')}"
            origin = f"{verified.archive_path}/{entry_name}"
            try:
                code = compile(source, origin, "exec", dont_inherit=True)
            except (SyntaxError, ValueError, TypeError):
                _fail("controlled YAML parser source could not be compiled")
            records[module_name] = (code, origin, is_package)
        self._records = MappingProxyType(records)

    def find_spec(
        self,
        fullname: str,
        _path: Any = None,
        _target: Any = None,
    ) -> ModuleSpec | None:
        record = self._records.get(fullname)
        if record is None:
            return None
        _code, origin, is_package = record
        spec = ModuleSpec(fullname, self, origin=origin, is_package=is_package)
        spec.has_location = True
        return spec

    @staticmethod
    def create_module(_spec: ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        record = self._records.get(module.__name__)
        if record is None:
            raise ImportError("module is absent from the verified YAML archive")
        code, _origin, _is_package = record
        exec(code, module.__dict__)


def _fail(message: str) -> NoReturn:
    raise YamlAuditError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_bounded(path: Path, maximum: int, description: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError:
        _fail(f"{description} is unavailable")
    if size < 0 or size > maximum:
        _fail(f"{description} exceeds its size limit")
    try:
        data = path.read_bytes()
    except OSError:
        _fail(f"{description} is unavailable")
    if len(data) != size or len(data) > maximum:
        _fail(f"{description} changed while it was being read")
    return data


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON contains a duplicate key")
        result[key] = value
    return result


def _bounded_json_int(value: str) -> int:
    if len(value) > MAX_NUMERIC_SCALAR_BYTES:
        _fail("JSON numeric scalar exceeds its size limit")
    try:
        return int(value, 10)
    except ValueError:
        _fail("JSON integer is malformed")


def _bounded_json_float(value: str) -> float:
    if len(value) > MAX_NUMERIC_SCALAR_BYTES:
        _fail("JSON numeric scalar exceeds its size limit")
    try:
        result = float(value)
    except ValueError:
        _fail("JSON float is malformed")
    if not math.isfinite(result):
        _fail("JSON float is not finite")
    return result


def _load_manifest(path: Path) -> Mapping[str, Any]:
    raw = _read_bounded(path, MAX_MANIFEST_BYTES, "YAML audit manifest")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_json_object,
            parse_int=_bounded_json_int,
            parse_float=_bounded_json_float,
            parse_constant=lambda _: _fail("manifest contains a non-finite number"),
        )
    except (OverflowError, RecursionError, UnicodeError, ValueError):
        _fail("YAML audit manifest is malformed")
    if not isinstance(value, Mapping):
        _fail("YAML audit manifest is malformed")
    return value


def _require_exact_keys(
    value: Any,
    keys: frozenset[str],
    description: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail(f"{description} is malformed")
    return value


def _require_sha256(value: Any, description: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        _fail(f"{description} is malformed")
    return value


def _safe_archive_name(name: Any) -> str:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        _fail("YAML audit archive contains an unsafe entry name")
    if name == "__main__.py" or name.endswith("/__main__.py"):
        _fail("YAML audit archive must not be executable")
    path = PurePosixPath(name)
    if path.is_absolute() or _DRIVE_RE.match(name) or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        _fail("YAML audit archive contains an unsafe entry name")
    return name


def verify_controlled_archive(
    archive_path: Path = DEFAULT_ARCHIVE,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> _VerifiedArchive:
    """Verify the complete parser artifact without importing any archive code."""
    archive_path = archive_path.resolve()
    manifest_path = manifest_path.resolve()
    manifest = _load_manifest(manifest_path)
    if set(manifest) != _MANIFEST_TOP_KEYS or manifest.get("schema_version") != 1:
        _fail("YAML audit manifest schema is unsupported")

    artifact = _require_exact_keys(
        manifest.get("artifact"), _ARTIFACT_KEYS, "artifact manifest"
    )
    if artifact.get("name") != "member-d-yaml-audit.pyz":
        _fail("artifact manifest names an unexpected archive")
    if archive_path.name != artifact["name"]:
        _fail("YAML audit archive name differs from its manifest")
    artifact_size = artifact.get("size")
    if (
        not isinstance(artifact_size, int)
        or isinstance(artifact_size, bool)
        or artifact_size != CONTROLLED_ARCHIVE_SIZE
    ):
        _fail("artifact manifest size is malformed")
    artifact_sha = _require_sha256(
        artifact.get("sha256"), "artifact manifest digest"
    )
    if artifact_sha != CONTROLLED_ARCHIVE_SHA256:
        _fail("artifact manifest digest differs from the approved archive")

    upstream = _require_exact_keys(
        manifest.get("upstream"), _UPSTREAM_KEYS, "upstream manifest"
    )
    if upstream != {
        "name": UPSTREAM_NAME,
        "version": UPSTREAM_VERSION,
        "license": UPSTREAM_LICENSE,
        "sdist_filename": UPSTREAM_SDIST_FILENAME,
        "sdist_size": UPSTREAM_SDIST_SIZE,
        "sdist_sha256": UPSTREAM_SDIST_SHA256,
        "sdist_url": UPSTREAM_SDIST_URL,
    }:
        _fail("upstream manifest differs from the approved release")

    zip_policy = _require_exact_keys(
        manifest.get("zip"), _ZIP_KEYS, "ZIP policy manifest"
    )
    expected_zip_policy = {
        "compression": "ZIP_STORED",
        "entry_count": len(_EXPECTED_ARCHIVE_ENTRIES),
        "create_system": 3,
        "timestamp": [1980, 1, 1, 0, 0, 0],
        "file_mode": "0100644",
        "comment": "",
        "max_entries": MAX_ARCHIVE_ENTRIES,
        "max_entry_uncompressed_bytes": MAX_ARCHIVE_ENTRY_BYTES,
        "max_total_uncompressed_bytes": MAX_ARCHIVE_UNCOMPRESSED_BYTES,
        "max_decompression_ratio": MAX_ARCHIVE_DECOMPRESSION_RATIO,
    }
    if zip_policy != expected_zip_policy:
        _fail("ZIP policy manifest differs from the runtime gate")

    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != _EXPECTED_ARCHIVE_ENTRIES:
        _fail("artifact file manifest is incomplete or contains unknown files")
    for entry_name, metadata_value in files.items():
        _safe_archive_name(entry_name)
        metadata = _require_exact_keys(
            metadata_value, _FILE_KEYS, "artifact file metadata"
        )
        size = metadata.get("size")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or size > MAX_ARCHIVE_ENTRY_BYTES
        ):
            _fail("artifact file size is malformed")
        _require_sha256(metadata.get("sha256"), "artifact file digest")
        source_member = metadata.get("source_member")
        if source_member is not None and (
            not isinstance(source_member, str)
            or not source_member
            or "\x00" in source_member
            or PurePosixPath(source_member).is_absolute()
            or ".." in PurePosixPath(source_member).parts
        ):
            _fail("artifact source member is malformed")
        if entry_name.startswith("_pba_yaml/"):
            expected_source_member = (
                f"pyyaml-{UPSTREAM_VERSION}/lib/yaml/"
                f"{entry_name.removeprefix('_pba_yaml/')}"
            )
        elif entry_name == (
            "THIRD_PARTY_LICENSES/PyYAML-6.0.3-LICENSE.txt"
        ):
            expected_source_member = f"pyyaml-{UPSTREAM_VERSION}/LICENSE"
        else:
            expected_source_member = None
        if source_member != expected_source_member:
            _fail("artifact source member differs from the approved provenance")

    archive = _read_bounded(archive_path, MAX_ARCHIVE_BYTES, "YAML audit archive")
    if len(archive) != artifact_size or _sha256_bytes(archive) != artifact_sha:
        _fail("YAML audit archive does not match its manifest")

    module_sources: dict[str, bytes] = {}
    try:
        # Parse and read entries from the exact byte string authenticated above.
        # Reopening archive_path here would create a replace-between-check/use
        # window before any module import.
        with ZipFile(io.BytesIO(archive), "r") as bundle:
            if bundle.comment:
                _fail("YAML audit archive comment is not allowed")
            infos = bundle.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                _fail("YAML audit archive contains too many entries")
            names = [_safe_archive_name(info.filename) for info in infos]
            if len(names) != len(set(names)):
                _fail("YAML audit archive contains a duplicate entry")
            if set(names) != _EXPECTED_ARCHIVE_ENTRIES:
                _fail("YAML audit archive contains unknown or missing files")
            total_uncompressed = 0
            for info in infos:
                if info.is_dir():
                    _fail("YAML audit archive contains a directory entry")
                if info.flag_bits & 0x1:
                    _fail("YAML audit archive contains an encrypted entry")
                unix_mode = info.external_attr >> 16
                if (
                    info.create_system != 3
                    or stat.S_IFMT(unix_mode) != stat.S_IFREG
                    or stat.S_IMODE(unix_mode) != 0o644
                    or info.compress_type != ZIP_STORED
                    or info.date_time != (1980, 1, 1, 0, 0, 0)
                    or info.comment
                    or info.extra
                ):
                    _fail("YAML audit archive entry metadata is unexpected")
                if (
                    info.file_size < 0
                    or info.compress_size < 0
                    or info.file_size > MAX_ARCHIVE_ENTRY_BYTES
                ):
                    _fail("YAML audit archive entry exceeds its size limit")
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    _fail("YAML audit archive exceeds its uncompressed size limit")
                if (
                    info.file_size
                    and info.file_size / max(1, info.compress_size)
                    > MAX_ARCHIVE_DECOMPRESSION_RATIO
                ):
                    _fail("YAML audit archive entry exceeds its compression ratio limit")
                metadata = files[info.filename]
                if info.file_size != metadata["size"]:
                    _fail("YAML audit archive entry size differs from its manifest")
                try:
                    payload = bundle.read(info)
                except (BadZipFile, OSError, RuntimeError):
                    _fail("YAML audit archive is corrupt")
                if _sha256_bytes(payload) != metadata["sha256"]:
                    _fail("YAML audit archive entry differs from its manifest")
                if info.filename.startswith("_pba_yaml/"):
                    module_sources[info.filename] = payload
            if bundle.testzip() is not None:
                _fail("YAML audit archive is corrupt")
    except (BadZipFile, OSError):
        _fail("YAML audit archive is malformed")

    if set(module_sources) != {
        f"_pba_yaml/{module_name}" for module_name in _MODULE_FILES
    }:
        _fail("YAML audit archive module set is incomplete")
    return _VerifiedArchive(
        archive_path=archive_path,
        artifact_sha=artifact_sha,
        manifest=manifest,
        module_sources=module_sources,
    )


def verify_isolated_interpreter() -> None:
    """Require the documented no-bytecode, no-environment, no-site runtime."""
    if not (
        sys.dont_write_bytecode
        and sys.flags.ignore_environment
        and sys.flags.no_site
    ):
        _fail("audit requires Python -B -E -S isolation")


def _module_origin_is_archive(module: ModuleType, archive_path: Path) -> bool:
    origin = getattr(module, "__file__", None)
    if not isinstance(origin, str):
        return False
    archive_prefix = os.path.normcase(str(archive_path.resolve())).replace("\\", "/")
    normalized_origin = os.path.normcase(os.path.abspath(origin)).replace("\\", "/")
    return normalized_origin.startswith(archive_prefix + "/")


def load_controlled_yaml_module(
    archive_path: Path = DEFAULT_ARCHIVE,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> ModuleType:
    """Validate first, then import the renamed parser and prove its origin."""
    archive_path = archive_path.resolve()
    verified = verify_controlled_archive(archive_path, manifest_path)
    key = verified.key

    preloaded = {
        name: module
        for name, module in sys.modules.items()
        if name == "_pba_yaml" or name.startswith("_pba_yaml.")
    }
    if preloaded and not all(
        isinstance(module, ModuleType)
        and _module_origin_is_archive(module, archive_path)
        for module in preloaded.values()
    ):
        _fail("private YAML parser was loaded before archive verification")
    preloaded_root = preloaded.get("_pba_yaml")
    if preloaded_root is not None and getattr(
        preloaded_root, "__pba_audit_archive_key__", None
    ) != key:
        _fail("private YAML parser does not match the verified archive digest")

    importer = _VerifiedArchiveImporter(verified)
    before_import = set(sys.modules)
    sys.meta_path.insert(0, importer)
    try:
        module = importlib.import_module("_pba_yaml")
    except (ImportError, OSError, RuntimeError):
        for name in tuple(sys.modules):
            if (
                name not in before_import
                and (name == "_pba_yaml" or name.startswith("_pba_yaml."))
            ):
                sys.modules.pop(name, None)
        _fail("controlled YAML parser could not be imported")
    finally:
        try:
            sys.meta_path.remove(importer)
        except ValueError:
            pass

    if (
        not _module_origin_is_archive(module, archive_path)
        or getattr(module, "__version__", None) != UPSTREAM_VERSION
        or getattr(module, "__with_libyaml__", None) is not False
    ):
        _fail("controlled YAML parser identity is unexpected")
    setattr(module, "__pba_audit_archive_key__", key)
    loader_module = sys.modules.get(getattr(module.SafeLoader, "__module__", ""))
    if not isinstance(loader_module, ModuleType) or not _module_origin_is_archive(
        loader_module, archive_path
    ):
        _fail("controlled YAML loader did not originate in the verified archive")
    return module


def _template_bytes(value: str | bytes) -> tuple[str, int]:
    if isinstance(value, bytes):
        if len(value) > MAX_TEMPLATE_BYTES:
            _fail("template exceeds the input byte limit")
        try:
            text = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            _fail("template is not valid UTF-8")
        return text, len(value)
    if not isinstance(value, str):
        _fail("template body is not text")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _fail("template is not valid UTF-8")
    if len(encoded) > MAX_TEMPLATE_BYTES:
        _fail("template exceeds the input byte limit")
    return value, len(encoded)


def _validate_json_like(value: Any, *, depth: int = 1) -> int:
    if depth > MAX_YAML_DEPTH:
        _fail("template exceeds the nesting depth limit")
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str):
            try:
                scalar_size = len(value.encode("utf-8"))
            except UnicodeEncodeError:
                _fail("template contains invalid Unicode text")
            if scalar_size > MAX_SCALAR_BYTES:
                _fail("template scalar exceeds its size limit")
        return 1
    if isinstance(value, int):
        # A Mapping-shaped AWS response is already decoded.  Bound conversion
        # without stringifying an attacker-sized bigint first.
        if value.bit_length() > MAX_NUMERIC_SCALAR_BYTES * 4:
            _fail("template numeric scalar exceeds its size limit")
        if len(str(abs(value))) > MAX_NUMERIC_SCALAR_BYTES:
            _fail("template numeric scalar exceeds its size limit")
        return 1
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("template contains a non-finite number")
        return 1
    if isinstance(value, list):
        if len(value) > MAX_COLLECTION_MEMBERS:
            _fail("template collection contains too many members")
        return 1 + sum(_validate_json_like(item, depth=depth + 1) for item in value)
    if isinstance(value, Mapping):
        if len(value) > MAX_COLLECTION_MEMBERS:
            _fail("template collection contains too many members")
        count = 1
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("template mapping key is not text")
            count += _validate_json_like(key, depth=depth + 1)
            count += _validate_json_like(item, depth=depth + 1)
        return count
    _fail("template contains a non-JSON value")


def _canonical_json_byte_size(value: Any) -> int:
    if isinstance(value, Mapping):
        total = 2 + max(0, len(value) - 1)
        for key, item in value.items():
            total += _canonical_json_byte_size(key) + 1
            if total > MAX_TEMPLATE_BYTES:
                _fail("template exceeds the canonical input byte limit")
            total += _canonical_json_byte_size(item)
            if total > MAX_TEMPLATE_BYTES:
                _fail("template exceeds the canonical input byte limit")
        return total
    if isinstance(value, list):
        total = 2 + max(0, len(value) - 1)
        for item in value:
            total += _canonical_json_byte_size(item)
            if total > MAX_TEMPLATE_BYTES:
                _fail("template exceeds the canonical input byte limit")
        return total
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
    except (OverflowError, TypeError, ValueError, UnicodeEncodeError):
        _fail("template contains a non-JSON value")


def _validate_mapping_template(value: Mapping[str, Any]) -> Mapping[str, Any]:
    nodes = _validate_json_like(value)
    if nodes > MAX_YAML_NODES:
        _fail("template exceeds the node limit")
    if _canonical_json_byte_size(value) > MAX_TEMPLATE_BYTES:
        _fail("template exceeds the canonical input byte limit")
    return value


def parse_cloudformation_yaml(
    value: str | bytes,
    *,
    archive_path: Path = DEFAULT_ARCHIVE,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> Any:
    """Parse one constrained CloudFormation YAML document fail closed."""
    text, _ = _template_bytes(value)
    yaml = load_controlled_yaml_module(archive_path, manifest_path)

    class StrictCloudFormationLoader(yaml.SafeLoader):
        def __init__(self, stream: str) -> None:
            super().__init__(stream)
            self._audit_node_count = 0
            self._audit_depth = 0

        def compose_node(self, parent: Any, index: Any) -> Any:
            event = self.peek_event()
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                raise yaml.YAMLError("YAML aliases and anchors are unsupported")
            self._audit_node_count += 1
            if self._audit_node_count > MAX_YAML_NODES:
                raise yaml.YAMLError("YAML node limit exceeded")
            self._audit_depth += 1
            if self._audit_depth > MAX_YAML_DEPTH:
                raise yaml.YAMLError("YAML nesting depth limit exceeded")
            try:
                return super().compose_node(parent, index)
            finally:
                self._audit_depth -= 1

        @staticmethod
        def _enforce_tag(node: Any) -> None:
            if node.tag not in _ALLOWED_CORE_TAGS and node.tag not in _SHORT_INTRINSICS:
                raise yaml.YAMLError("YAML tag is unsupported by CloudFormation")
            if node.tag in {
                "tag:yaml.org,2002:str",
                "tag:yaml.org,2002:int",
                "tag:yaml.org,2002:float",
                "tag:yaml.org,2002:bool",
                "tag:yaml.org,2002:null",
            } and not isinstance(node, yaml.ScalarNode):
                raise yaml.YAMLError("YAML core tag has the wrong node kind")
            if node.tag == "tag:yaml.org,2002:seq" and not isinstance(
                node, yaml.SequenceNode
            ):
                raise yaml.YAMLError("YAML sequence tag has the wrong node kind")
            if node.tag == "tag:yaml.org,2002:map" and not isinstance(
                node, yaml.MappingNode
            ):
                raise yaml.YAMLError("YAML mapping tag has the wrong node kind")

        def construct_object(self, node: Any, deep: bool = False) -> Any:
            self._enforce_tag(node)
            return super().construct_object(node, deep=deep)

        def construct_scalar(self, node: Any) -> Any:
            self._enforce_tag(node)
            if len(node.value.encode("utf-8")) > MAX_SCALAR_BYTES:
                raise yaml.YAMLError("YAML scalar size limit exceeded")
            return super().construct_scalar(node)

        def construct_sequence(self, node: Any, deep: bool = False) -> list[Any]:
            self._enforce_tag(node)
            if len(node.value) > MAX_COLLECTION_MEMBERS:
                raise yaml.YAMLError("YAML collection member limit exceeded")
            return [self.construct_object(child, deep=deep) for child in node.value]

        def construct_mapping(self, node: Any, deep: bool = False) -> dict[str, Any]:
            self._enforce_tag(node)
            if len(node.value) > MAX_COLLECTION_MEMBERS:
                raise yaml.YAMLError("YAML collection member limit exceeded")
            result: dict[str, Any] = {}
            for key_node, value_node in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    raise yaml.YAMLError("YAML merge keys are unsupported")
                key = self.construct_object(key_node, deep=True)
                if not isinstance(key, str):
                    raise yaml.YAMLError("YAML mapping keys must be strings")
                if key in result:
                    raise yaml.YAMLError("YAML duplicate mapping key")
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    def construct_intrinsic(loader: Any, node: Any) -> dict[str, Any]:
        loader._enforce_tag(node)
        if isinstance(node, yaml.ScalarNode):
            parsed_value = loader.construct_scalar(node)
            if node.tag == "!GetAtt":
                logical_id, separator, attribute = parsed_value.partition(".")
                if not separator or not logical_id or not attribute:
                    raise yaml.YAMLError(
                        "!GetAtt scalar must use Resource.Attribute form"
                    )
                parsed_value = [logical_id, attribute]
        elif isinstance(node, yaml.SequenceNode):
            parsed_value = loader.construct_sequence(node, deep=True)
        elif isinstance(node, yaml.MappingNode):
            parsed_value = loader.construct_mapping(node, deep=True)
        else:
            raise yaml.YAMLError("CloudFormation intrinsic node is malformed")
        return {_SHORT_INTRINSICS[node.tag]: parsed_value}

    safe_int_constructor = yaml.SafeLoader.yaml_constructors[
        "tag:yaml.org,2002:int"
    ]
    safe_float_constructor = yaml.SafeLoader.yaml_constructors[
        "tag:yaml.org,2002:float"
    ]

    def check_numeric_size(loader: Any, node: Any) -> None:
        text_value = loader.construct_scalar(node)
        if len(text_value.encode("utf-8")) > MAX_NUMERIC_SCALAR_BYTES:
            raise yaml.YAMLError("YAML numeric scalar exceeds its size limit")

    def construct_bounded_int(loader: Any, node: Any) -> int:
        check_numeric_size(loader, node)
        return safe_int_constructor(loader, node)

    def construct_bounded_float(loader: Any, node: Any) -> float:
        check_numeric_size(loader, node)
        return safe_float_constructor(loader, node)

    for tag in _SHORT_INTRINSICS:
        StrictCloudFormationLoader.add_constructor(tag, construct_intrinsic)
    StrictCloudFormationLoader.add_constructor(
        "tag:yaml.org,2002:int", construct_bounded_int
    )
    StrictCloudFormationLoader.add_constructor(
        "tag:yaml.org,2002:float", construct_bounded_float
    )

    try:
        parsed = yaml.load(text, Loader=StrictCloudFormationLoader)
    except yaml.YAMLError as exc:
        reason = str(exc)
        if "node limit" in reason:
            _fail("template exceeds the YAML node limit")
        if "nesting depth limit" in reason:
            _fail("template exceeds the YAML nesting depth limit")
        _fail("processed template is not valid constrained CloudFormation YAML")
    except (
        AttributeError,
        IndexError,
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        _fail("processed template is not valid constrained CloudFormation YAML")
    nodes = _validate_json_like(parsed)
    if nodes > MAX_YAML_NODES:
        _fail("template exceeds the node limit")
    return parsed


def parse_cloudformation_template(
    value: Any,
    *,
    archive_path: Path = DEFAULT_ARCHIVE,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> Any:
    """Parse strict JSON first, then constrained YAML using the pinned parser."""
    if isinstance(value, Mapping):
        return _validate_mapping_template(value)
    text, _ = _template_bytes(value)
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_strict_json_object,
            parse_int=_bounded_json_int,
            parse_float=_bounded_json_float,
            parse_constant=lambda _: _fail("template contains a non-finite number"),
        )
    except json.JSONDecodeError:
        return parse_cloudformation_yaml(
            text, archive_path=archive_path, manifest_path=manifest_path
        )
    except YamlAuditError:
        raise
    except (OverflowError, RecursionError, UnicodeError, ValueError):
        _fail("processed template is not valid constrained JSON")
    if isinstance(parsed, Mapping):
        return _validate_mapping_template(parsed)
    nodes = _validate_json_like(parsed)
    if nodes > MAX_YAML_NODES:
        _fail("template exceeds the node limit")
    return parsed
