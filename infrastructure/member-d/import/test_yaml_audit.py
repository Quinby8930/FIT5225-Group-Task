import json
import os
import subprocess
import sys
import importlib.util
import hashlib
import shutil
import stat
import warnings
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

import pytest


_IMPORT_DIR = Path(__file__).resolve().parent
_YAML_AUDIT = _IMPORT_DIR / "yaml_audit.py"


def _load_yaml_audit():
    spec = importlib.util.spec_from_file_location("member_d_yaml_audit_tests", _YAML_AUDIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime_fixture(tmp_path):
    archive = tmp_path / "member-d-yaml-audit.pyz"
    manifest = tmp_path / "member-d-yaml-audit.lock.json"
    shutil.copy2(_IMPORT_DIR / archive.name, archive)
    shutil.copy2(_IMPORT_DIR / manifest.name, manifest)
    return archive, manifest


def _rewrite_manifest(manifest_path, transform):
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    transform(value)
    manifest_path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _refresh_artifact_digest(archive_path, manifest_path):
    payload = archive_path.read_bytes()

    def update(value):
        value["artifact"]["size"] = len(payload)
        value["artifact"]["sha256"] = hashlib.sha256(payload).hexdigest()

    _rewrite_manifest(manifest_path, update)


def _append_archive_entry(archive_path, name):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with ZipFile(archive_path, "a") as archive:
            archive.writestr(name, b"unexpected")


def test_yaml_template_body_parses_with_site_and_pythonpath_disabled(tmp_path):
    """Only the verified private archive may satisfy a clean audit process."""
    marker = tmp_path / "malicious-imported.txt"
    malicious = tmp_path / "malicious"
    malicious.mkdir()
    payload = f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n"
    (malicious / "yaml.py").write_text(payload, encoding="utf-8")
    (malicious / "adoption.py").write_text(payload, encoding="utf-8")
    private_package = malicious / "_pba_yaml"
    private_package.mkdir()
    (private_package / "__init__.py").write_text(payload, encoding="utf-8")

    template_body = """
AWSTemplateFormatVersion: '2010-09-09'
Resources:
  FilesTable:
    Type: AWS::DynamoDB::Table
Outputs:
  FilesTableReference:
    Value: !Ref FilesTable
"""
    probe = f"""
import importlib.util
import json
from pathlib import Path

module_path = Path({str(_YAML_AUDIT)!r})
spec = importlib.util.spec_from_file_location("member_d_yaml_audit", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
parsed = module.parse_cloudformation_yaml({template_body!r})
print(json.dumps(parsed, sort_keys=True, separators=(",", ":")))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(malicious)
    result = subprocess.run(
        [sys.executable, "-B", "-E", "-S", "-c", probe],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Resources": {"FilesTable": {"Type": "AWS::DynamoDB::Table"}},
        "Outputs": {
            "FilesTableReference": {"Value": {"Ref": "FilesTable"}}
        },
    }
    assert not marker.exists()


def test_loaded_parser_module_origin_is_the_verified_archive():
    yaml_audit = _load_yaml_audit()

    controlled = yaml_audit.load_controlled_yaml_module()

    archive = str(yaml_audit.DEFAULT_ARCHIVE.resolve()).replace("\\", "/")
    origin = str(controlled.__file__).replace("\\", "/")
    assert origin.startswith(archive + "/")
    assert controlled.__name__ == "_pba_yaml"
    assert controlled.__version__ == "6.0.3"
    assert controlled.__with_libyaml__ is False


def test_committed_archive_bytes_match_the_runtime_pin_and_lock():
    yaml_audit = _load_yaml_audit()
    archive = _IMPORT_DIR / "member-d-yaml-audit.pyz"
    manifest = json.loads(
        (_IMPORT_DIR / "member-d-yaml-audit.lock.json").read_text(encoding="utf-8")
    )
    payload = archive.read_bytes()

    assert len(payload) == yaml_audit.CONTROLLED_ARCHIVE_SIZE == 217_611
    assert hashlib.sha256(payload).hexdigest() == (
        yaml_audit.CONTROLLED_ARCHIVE_SHA256
    )
    assert manifest["artifact"] == {
        "name": archive.name,
        "sha256": yaml_audit.CONTROLLED_ARCHIVE_SHA256,
        "size": yaml_audit.CONTROLLED_ARCHIVE_SIZE,
    }


def test_import_executes_the_already_verified_archive_bytes_if_path_is_swapped(
    tmp_path,
):
    archive, manifest = _runtime_fixture(tmp_path)
    marker = tmp_path / "replacement-executed.txt"
    probe = f"""
import importlib.util
from pathlib import Path
from zipfile import ZipFile

module_path = Path({str(_YAML_AUDIT)!r})
archive_path = Path({str(archive)!r})
manifest_path = Path({str(manifest)!r})
marker_path = Path({str(marker)!r})
spec = importlib.util.spec_from_file_location("member_d_yaml_audit_swap_test", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
real_verify = module.verify_controlled_archive

def verify_then_replace(*args, **kwargs):
    verified = real_verify(*args, **kwargs)
    with ZipFile(archive_path, "w") as replacement:
        replacement.writestr(
            "_pba_yaml/__init__.py",
            "from pathlib import Path\\nPath(" + repr(str(marker_path)) + ").write_text('executed')\\n",
        )
    return verified

module.verify_controlled_archive = verify_then_replace
controlled = module.load_controlled_yaml_module(archive_path, manifest_path)
assert controlled.__version__ == "6.0.3"
assert str(controlled.__file__).replace("\\\\", "/").startswith(
    str(archive_path.resolve()).replace("\\\\", "/") + "/"
)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-E", "-S", "-c", probe],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_standard_condition_short_tag_is_preserved():
    yaml_audit = _load_yaml_audit()

    parsed = yaml_audit.parse_cloudformation_yaml(
        """
Conditions:
  IsProduction: !Equals [!Ref Environment, production]
  IsReady: !And
    - !Condition IsProduction
    - !Equals [!Ref Enabled, 'true']
"""
    )

    assert parsed["Conditions"]["IsReady"]["Fn::And"][0] == {
        "Condition": "IsProduction"
    }


@pytest.mark.parametrize(
    "yaml_text",
    [
        "Value: !!binary SGVsbG8=\n",
        "Value: !!omap [{one: 1}]\n",
        "Value: !!pairs [{one: 1}]\n",
        "Value: !!set {one: null}\n",
        "Value: !!timestamp 2026-09-01T00:00:00Z\n",
        "Value: &shared thing\nOther: *shared\n",
        "Defaults: &defaults {a: b}\nValue: {<<: *defaults}\n",
        "Value: {<<: {a: b}}\n",
        "Value: !UnknownTag thing\n",
        "Value: !!python/object:builtins.object {}\n",
        "First: document\n---\nSecond: document\n",
        "Resources: {}\nResources: {}\n",
        "? [complex, key]\n: value\n",
    ],
    ids=(
        "binary",
        "omap",
        "pairs",
        "set",
        "timestamp",
        "alias-anchor",
        "merge-with-alias",
        "merge-without-alias",
        "unknown-short-tag",
        "python-object",
        "multiple-documents",
        "duplicate-key",
        "complex-key",
    ),
)
def test_cloudformation_unsupported_yaml_features_fail_closed(yaml_text):
    yaml_audit = _load_yaml_audit()

    with pytest.raises(yaml_audit.YamlAuditError):
        yaml_audit.parse_cloudformation_yaml(yaml_text)


def test_yaml_input_byte_limit_is_enforced_before_parser_loading(monkeypatch):
    yaml_audit = _load_yaml_audit()
    monkeypatch.setattr(yaml_audit, "MAX_TEMPLATE_BYTES", 8)
    monkeypatch.setattr(
        yaml_audit,
        "load_controlled_yaml_module",
        lambda *_args, **_kwargs: pytest.fail(
            "parser was loaded before the input byte gate"
        ),
    )

    with pytest.raises(yaml_audit.YamlAuditError, match="byte limit"):
        yaml_audit.parse_cloudformation_yaml("Value: too-large")


def test_yaml_node_limit_is_enforced(monkeypatch):
    yaml_audit = _load_yaml_audit()
    monkeypatch.setattr(yaml_audit, "MAX_YAML_NODES", 4)

    with pytest.raises(yaml_audit.YamlAuditError, match="node"):
        yaml_audit.parse_cloudformation_yaml("Value: [one, two, three]\n")


def test_yaml_nesting_depth_limit_is_enforced(monkeypatch):
    yaml_audit = _load_yaml_audit()
    monkeypatch.setattr(yaml_audit, "MAX_YAML_DEPTH", 3)

    with pytest.raises(yaml_audit.YamlAuditError, match="depth"):
        yaml_audit.parse_cloudformation_yaml("Value: [[[too-deep]]]\n")


@pytest.mark.parametrize(
    "yaml_text",
    [
        "Value: !!bool nope\n",
        "Value: !!int nope\n",
        "Value: !!float nope\n",
        "Value: !!int {a: b}\n",
        "Value: !!bool [true]\n",
        "Value: !!str [a]\n",
        "Value: !!seq nope\n",
        "Value: !!map []\n",
        "Value: !!seq {}\n",
        "Value: !!int ''\n",
        "Value: !!int '+'\n",
        "Value: !!float ''\n",
    ],
)
def test_invalid_explicit_core_scalars_are_sanitized(yaml_text):
    yaml_audit = _load_yaml_audit()

    with pytest.raises(
        yaml_audit.YamlAuditError,
        match="processed template is not valid constrained CloudFormation YAML",
    ) as error:
        yaml_audit.parse_cloudformation_yaml(yaml_text)

    assert "nope" not in str(error.value)


def test_normal_size_yaml_1_1_integer_forms_remain_compatible():
    yaml_audit = _load_yaml_audit()

    parsed = yaml_audit.parse_cloudformation_yaml(
        "Sexagesimal: 1:2:3\nHex: 0x10\nBinary: 0b10\nOctal: 01\nText: 0o10\n"
    )

    assert parsed == {
        "Sexagesimal": 3723,
        "Hex": 16,
        "Binary": 2,
        "Octal": 1,
        "Text": "0o10",
    }


def test_numeric_scalar_limit_precedes_big_integer_construction(monkeypatch):
    yaml_audit = _load_yaml_audit()
    monkeypatch.setattr(yaml_audit, "MAX_NUMERIC_SCALAR_BYTES", 8)

    with pytest.raises(yaml_audit.YamlAuditError, match="constrained"):
        yaml_audit.parse_cloudformation_yaml("Value: 123456789\n")


def test_json_path_rejects_duplicate_keys_and_non_finite_numbers():
    yaml_audit = _load_yaml_audit()

    for value in ('{"Resources":{},"Resources":{}}', '{"Value":NaN}'):
        with pytest.raises(yaml_audit.YamlAuditError):
            yaml_audit.parse_cloudformation_template(value)


@pytest.mark.parametrize(
    "value",
    [
        "[" * 10_000 + "0" + "]" * 10_000,
        '{"Value":"\\ud800"}',
        '{"Value":' + "9" * 10_000 + "}",
    ],
    ids=("deep-json", "surrogate-json", "oversized-json-integer"),
)
def test_json_decoder_and_validation_failures_are_sanitized(value):
    yaml_audit = _load_yaml_audit()

    with pytest.raises(yaml_audit.YamlAuditError) as error:
        yaml_audit.parse_cloudformation_template(value)

    assert "9" * 128 not in str(error.value)
    assert "ud800" not in str(error.value)


def test_json_numeric_limit_does_not_depend_on_cpython_digit_guard(tmp_path):
    probe = f"""
import importlib.util
from pathlib import Path

module_path = Path({str(_YAML_AUDIT)!r})
spec = importlib.util.spec_from_file_location("member_d_json_digit_guard_test", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
try:
    module.parse_cloudformation_template('{{"Value":' + '9' * 10000 + '}}')
except module.YamlAuditError:
    raise SystemExit(0)
raise SystemExit(1)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "int_max_str_digits=0",
            "-B",
            "-E",
            "-S",
            "-c",
            probe,
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_mapping_response_shape_cannot_bypass_node_or_depth_limits(monkeypatch):
    yaml_audit = _load_yaml_audit()
    monkeypatch.setattr(yaml_audit, "MAX_YAML_DEPTH", 3)

    with pytest.raises(yaml_audit.YamlAuditError, match="depth"):
        yaml_audit.parse_cloudformation_template(
            {"Resources": {"Nested": {"Too": "deep"}}}
        )

    monkeypatch.setattr(yaml_audit, "MAX_YAML_DEPTH", 100)
    monkeypatch.setattr(yaml_audit, "MAX_YAML_NODES", 4)
    with pytest.raises(yaml_audit.YamlAuditError, match="node"):
        yaml_audit.parse_cloudformation_template(
            {"Resources": {"One": {}, "Two": {}}}
        )


def test_mapping_response_shape_has_a_canonical_input_byte_limit(monkeypatch):
    yaml_audit = _load_yaml_audit()
    monkeypatch.setattr(yaml_audit, "MAX_TEMPLATE_BYTES", 16)

    with pytest.raises(yaml_audit.YamlAuditError, match="byte limit"):
        yaml_audit.parse_cloudformation_template({"Value": "too-large"})


def test_runtime_rejects_archive_hash_mismatch(tmp_path):
    yaml_audit = _load_yaml_audit()
    archive, manifest = _runtime_fixture(tmp_path)
    archive.write_bytes(archive.read_bytes() + b"tampered")

    with pytest.raises(yaml_audit.YamlAuditError, match="manifest"):
        yaml_audit.verify_controlled_archive(archive, manifest)


def test_runtime_sanitizes_deep_manifest_decoder_failure(tmp_path):
    yaml_audit = _load_yaml_audit()
    archive, manifest = _runtime_fixture(tmp_path)
    manifest.write_text("[" * 10_000 + "0" + "]" * 10_000, encoding="utf-8")

    with pytest.raises(yaml_audit.YamlAuditError, match="manifest is malformed"):
        yaml_audit.verify_controlled_archive(archive, manifest)


def test_runtime_requires_exact_upstream_source_member_provenance(tmp_path):
    yaml_audit = _load_yaml_audit()
    archive, manifest = _runtime_fixture(tmp_path)

    def update(value):
        value["files"]["_pba_yaml/loader.py"]["source_member"] = (
            "pyyaml-6.0.3/lib/yaml/not-loader.py"
        )

    _rewrite_manifest(manifest, update)

    with pytest.raises(yaml_audit.YamlAuditError, match="source member"):
        yaml_audit.verify_controlled_archive(archive, manifest)


def test_full_upstream_license_is_preserved_inside_and_outside_archive():
    expected = (
        _IMPORT_DIR
        / "THIRD_PARTY_LICENSES"
        / "PyYAML-6.0.3-LICENSE.txt"
    ).read_bytes()
    assert hashlib.sha256(expected).hexdigest() == (
        "8d3928f9dc4490fd635707cb88eb26bd764102a7282954307d3e5167a577e8a4"
    )
    with ZipFile(_IMPORT_DIR / "member-d-yaml-audit.pyz") as archive:
        assert archive.read(
            "THIRD_PARTY_LICENSES/PyYAML-6.0.3-LICENSE.txt"
        ) == expected
    notice = (_IMPORT_DIR / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "renamed from" in notice
    assert "`yaml` to `_pba_yaml`" in notice
    assert "cyaml.py" in notice


@pytest.mark.parametrize(
    ("entry_name", "expected_error"),
    [
        ("_pba_yaml/loader.py", "duplicate"),
        ("/absolute.py", "unsafe"),
        ("../escape.py", "unsafe"),
        ("unknown.py", "unknown or missing"),
        ("__main__.py", "must not be executable"),
    ],
    ids=("duplicate", "absolute", "parent-traversal", "unknown", "executable"),
)
def test_runtime_rejects_unsafe_duplicate_or_unknown_archive_entry(
    tmp_path,
    monkeypatch,
    entry_name,
    expected_error,
):
    yaml_audit = _load_yaml_audit()
    archive, manifest = _runtime_fixture(tmp_path)
    _append_archive_entry(archive, entry_name)
    _refresh_artifact_digest(archive, manifest)
    monkeypatch.setattr(
        yaml_audit,
        "CONTROLLED_ARCHIVE_SHA256",
        hashlib.sha256(archive.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        yaml_audit, "CONTROLLED_ARCHIVE_SIZE", archive.stat().st_size
    )

    with pytest.raises(yaml_audit.YamlAuditError, match=expected_error):
        yaml_audit.verify_controlled_archive(archive, manifest)


def test_runtime_rejects_symlink_archive_entry(tmp_path, monkeypatch):
    yaml_audit = _load_yaml_audit()
    archive, manifest = _runtime_fixture(tmp_path)
    replacement = tmp_path / "replacement.pyz"
    target_name = "_pba_yaml/loader.py"
    with ZipFile(archive, "r") as source, ZipFile(replacement, "w") as target:
        for original in source.infolist():
            info = ZipInfo(original.filename, original.date_time)
            info.compress_type = ZIP_STORED
            info.create_system = 3
            info.create_version = 20
            info.extract_version = 10
            info.external_attr = original.external_attr
            info.internal_attr = 0
            info.flag_bits = 0
            info.extra = b""
            info.comment = b""
            if original.filename == target_name:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            target.writestr(info, source.read(original))
    replacement.replace(archive)
    _refresh_artifact_digest(archive, manifest)
    monkeypatch.setattr(
        yaml_audit,
        "CONTROLLED_ARCHIVE_SHA256",
        hashlib.sha256(archive.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        yaml_audit, "CONTROLLED_ARCHIVE_SIZE", archive.stat().st_size
    )

    with pytest.raises(yaml_audit.YamlAuditError, match="metadata"):
        yaml_audit.verify_controlled_archive(archive, manifest)


def test_runtime_enforces_archive_decompression_ratio_limit(tmp_path, monkeypatch):
    yaml_audit = _load_yaml_audit()
    archive, manifest = _runtime_fixture(tmp_path)
    monkeypatch.setattr(yaml_audit, "MAX_ARCHIVE_DECOMPRESSION_RATIO", 0.5)

    def update(value):
        value["zip"]["max_decompression_ratio"] = 0.5

    _rewrite_manifest(manifest, update)

    with pytest.raises(yaml_audit.YamlAuditError, match="compression ratio"):
        yaml_audit.verify_controlled_archive(archive, manifest)
