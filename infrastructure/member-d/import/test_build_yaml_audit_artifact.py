import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest


_IMPORT_DIR = Path(__file__).resolve().parent
_BUILDER_PATH = _IMPORT_DIR / "build_yaml_audit_artifact.py"
_PURE_MODULES = (
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
)


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "member_d_build_yaml_audit_artifact", _BUILDER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tar_bytes(entries):
    raw_tar = io.BytesIO()
    with tarfile.open(fileobj=raw_tar, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, content, kind in entries:
            info = tarfile.TarInfo(name)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            if kind == "file":
                payload = bytes(content)
                info.type = tarfile.REGTYPE
                info.mode = 0o644
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.size = 0
                archive.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = str(content)
                info.mode = 0o777
                info.size = 0
                archive.addfile(info)
            else:  # pragma: no cover - test helper misuse
                raise AssertionError(kind)
    return gzip.compress(raw_tar.getvalue(), compresslevel=9, mtime=0)


def _valid_sdist_entries(*, license_bytes=None):
    license_bytes = license_bytes or (
        b"Copyright (c) 2017-2021 Ingy dot Net\n\n"
        b"Permission is hereby granted, free of charge, to any person obtaining "
        b"a copy of this software and associated documentation files.\n"
    )
    entries = [
        ("pyyaml-6.0.3", b"", "dir"),
        ("pyyaml-6.0.3/lib", b"", "dir"),
        ("pyyaml-6.0.3/lib/yaml", b"", "dir"),
        ("pyyaml-6.0.3/LICENSE", license_bytes, "file"),
        ("pyyaml-6.0.3/README.md", b"upstream metadata\n", "file"),
    ]
    for module_name in _PURE_MODULES:
        content = (
            b"__version__ = '6.0.3'\n"
            if module_name == "__init__.py"
            else f"# upstream {module_name}\n".encode("utf-8")
        )
        entries.append(
            (f"pyyaml-6.0.3/lib/yaml/{module_name}", content, "file")
        )
    entries.append(
        (
            "pyyaml-6.0.3/lib/yaml/cyaml.py",
            b"from yaml._yaml import CParser\n",
            "file",
        )
    )
    return entries, license_bytes


def _write_sdist(path, entries):
    path.write_bytes(_tar_bytes(entries))
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size


def _configure_test_source(monkeypatch, builder, path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(builder, "PYPI_SDIST_SHA256", digest)
    monkeypatch.setattr(builder, "PYPI_SDIST_SIZE", path.stat().st_size)


def test_official_pyyaml_source_is_pinned():
    builder = _load_builder()

    assert builder.PYYAML_VERSION == "6.0.3"
    assert builder.PYPI_SDIST_FILENAME == "pyyaml-6.0.3.tar.gz"
    assert builder.PYPI_SDIST_SIZE == 130_960
    assert builder.PYPI_SDIST_SHA256 == (
        "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
    )
    assert builder.PYPI_SDIST_URL == (
        "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/"
        "pyyaml-6.0.3.tar.gz"
    )


def test_two_offline_builds_are_byte_identical_and_manifest_every_entry(
    tmp_path, monkeypatch
):
    builder = _load_builder()
    entries, license_bytes = _valid_sdist_entries()
    sdist = tmp_path / builder.PYPI_SDIST_FILENAME
    source_sha, source_size = _write_sdist(sdist, entries)
    _configure_test_source(monkeypatch, builder, sdist)

    results = []
    for directory_name in ("first", "second"):
        output_dir = tmp_path / directory_name
        output_dir.mkdir()
        archive_path = output_dir / "member-d-yaml-audit.pyz"
        manifest_path = output_dir / "member-d-yaml-audit.lock.json"
        builder.build_artifact(sdist, archive_path, manifest_path)
        results.append((archive_path.read_bytes(), manifest_path.read_bytes()))

    assert results[0] == results[1]
    archive_bytes, manifest_bytes = results[0]
    manifest = json.loads(manifest_bytes)
    assert manifest_bytes.endswith(b"\n")
    assert manifest["schema_version"] == 1
    assert manifest["artifact"] == {
        "name": "member-d-yaml-audit.pyz",
        "sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "size": len(archive_bytes),
    }
    assert manifest["upstream"] == {
        "license": "MIT",
        "name": "PyYAML",
        "sdist_filename": "pyyaml-6.0.3.tar.gz",
        "sdist_sha256": source_sha,
        "sdist_size": source_size,
        "sdist_url": builder.PYPI_SDIST_URL,
        "version": "6.0.3",
    }
    assert manifest["zip"] == {
        "comment": "",
        "compression": "ZIP_STORED",
        "create_system": 3,
        "entry_count": 18,
        "file_mode": "0100644",
        "max_decompression_ratio": 100,
        "max_entries": 64,
        "max_entry_uncompressed_bytes": 524_288,
        "max_total_uncompressed_bytes": 2_097_152,
        "timestamp": [1980, 1, 1, 0, 0, 0],
    }

    expected_names = {
        *(f"_pba_yaml/{module_name}" for module_name in _PURE_MODULES),
        "THIRD_PARTY_LICENSES/PyYAML-6.0.3-LICENSE.txt",
        "THIRD_PARTY_NOTICES.md",
    }
    assert set(manifest["files"]) == expected_names
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert archive.namelist() == sorted(expected_names)
        assert archive.comment == b""
        assert archive.read(
            "THIRD_PARTY_LICENSES/PyYAML-6.0.3-LICENSE.txt"
        ) == license_bytes
        notice = archive.read("THIRD_PARTY_NOTICES.md").decode("utf-8")
        assert "PyYAML 6.0.3" in notice
        assert "MIT License" in notice
        assert "renamed from `yaml` to `_pba_yaml`" in notice
        assert "`cyaml.py`" in notice
        assert "__main__.py" not in archive.namelist()
        assert "_pba_yaml/cyaml.py" not in archive.namelist()
        for info in archive.infolist():
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 3
            assert info.external_attr >> 16 == 0o100644
            assert info.extra == b""
            assert info.comment == b""
            recorded = manifest["files"][info.filename]
            payload = archive.read(info.filename)
            assert recorded["size"] == len(payload)
            assert recorded["sha256"] == hashlib.sha256(payload).hexdigest()
        assert (
            manifest["files"]["THIRD_PARTY_NOTICES.md"]["source_member"]
            is None
        )


def test_source_hash_is_verified_before_malformed_tar_is_opened(tmp_path):
    builder = _load_builder()
    sdist = tmp_path / builder.PYPI_SDIST_FILENAME
    sdist.write_bytes(b"x" * builder.PYPI_SDIST_SIZE)

    with pytest.raises(builder.ArtifactBuildError, match="sdist SHA-256 mismatch"):
        builder.build_artifact(
            sdist,
            tmp_path / "member-d-yaml-audit.pyz",
            tmp_path / "member-d-yaml-audit.lock.json",
        )


def test_sdist_size_is_rejected_before_file_contents_are_read(
    tmp_path, monkeypatch
):
    builder = _load_builder()
    sdist = tmp_path / builder.PYPI_SDIST_FILENAME
    sdist.write_bytes(b"wrong-size")

    def forbidden_read_bytes(_path):
        raise AssertionError("sdist contents were read before the size gate")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)

    with pytest.raises(builder.ArtifactBuildError, match="sdist size mismatch"):
        builder.build_artifact(
            sdist,
            tmp_path / "member-d-yaml-audit.pyz",
            tmp_path / "member-d-yaml-audit.lock.json",
        )


@pytest.mark.parametrize(
    ("extra_entry", "message"),
    [
        (("../outside", b"escape", "file"), "unsafe sdist path"),
        (
            ("pyyaml-6.0.3/lib/yaml/linked.py", "../../outside", "symlink"),
            "unsupported sdist member type",
        ),
        (
            ("/absolute/path", b"absolute", "file"),
            "unsafe sdist path",
        ),
    ],
)
def test_unsafe_sdist_members_are_rejected(
    tmp_path, monkeypatch, extra_entry, message
):
    builder = _load_builder()
    entries, _ = _valid_sdist_entries()
    entries.append(extra_entry)
    sdist = tmp_path / builder.PYPI_SDIST_FILENAME
    _write_sdist(sdist, entries)
    _configure_test_source(monkeypatch, builder, sdist)

    with pytest.raises(builder.ArtifactBuildError, match=message):
        builder.build_artifact(
            sdist,
            tmp_path / "member-d-yaml-audit.pyz",
            tmp_path / "member-d-yaml-audit.lock.json",
        )


def test_duplicate_sdist_member_is_rejected(tmp_path, monkeypatch):
    builder = _load_builder()
    entries, _ = _valid_sdist_entries()
    duplicate = "pyyaml-6.0.3/lib/yaml/loader.py"
    entries.append((duplicate, b"replacement\n", "file"))
    sdist = tmp_path / builder.PYPI_SDIST_FILENAME
    _write_sdist(sdist, entries)
    _configure_test_source(monkeypatch, builder, sdist)

    with pytest.raises(builder.ArtifactBuildError, match="duplicate sdist member"):
        builder.build_artifact(
            sdist,
            tmp_path / "member-d-yaml-audit.pyz",
            tmp_path / "member-d-yaml-audit.lock.json",
        )


def test_missing_required_source_file_is_rejected(tmp_path, monkeypatch):
    builder = _load_builder()
    entries, _ = _valid_sdist_entries()
    entries = [
        entry
        for entry in entries
        if entry[0] != "pyyaml-6.0.3/lib/yaml/scanner.py"
    ]
    sdist = tmp_path / builder.PYPI_SDIST_FILENAME
    _write_sdist(sdist, entries)
    _configure_test_source(monkeypatch, builder, sdist)

    with pytest.raises(builder.ArtifactBuildError, match="required sdist member missing"):
        builder.build_artifact(
            sdist,
            tmp_path / "member-d-yaml-audit.pyz",
            tmp_path / "member-d-yaml-audit.lock.json",
        )


def test_builder_refuses_to_overwrite_source_or_use_non_pyz_output(
    tmp_path, monkeypatch
):
    builder = _load_builder()
    entries, _ = _valid_sdist_entries()
    sdist = tmp_path / builder.PYPI_SDIST_FILENAME
    _write_sdist(sdist, entries)
    _configure_test_source(monkeypatch, builder, sdist)

    with pytest.raises(builder.ArtifactBuildError, match="archive must end in .pyz"):
        builder.build_artifact(
            sdist,
            tmp_path / "artifact.zip",
            tmp_path / "artifact.lock.json",
        )
    with pytest.raises(builder.ArtifactBuildError, match="output paths must be distinct"):
        builder.build_artifact(sdist, sdist, tmp_path / "artifact.lock.json")
