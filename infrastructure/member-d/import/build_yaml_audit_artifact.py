#!/usr/bin/env python3
"""Build the pinned, pure-Python YAML parser archive used by Member D audits.

This builder is intentionally offline.  It accepts an already-downloaded official
PyYAML source distribution, verifies its PyPI size and SHA-256 before opening it,
and copies only a fixed allowlist of pure-Python modules into a private package.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Mapping


PYYAML_VERSION = "6.0.3"
PYPI_SDIST_FILENAME = "pyyaml-6.0.3.tar.gz"
PYPI_SDIST_SIZE = 130_960
PYPI_SDIST_SHA256 = (
    "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
)
PYPI_SDIST_URL = (
    "https://files.pythonhosted.org/packages/05/8e/"
    "961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/"
    "pyyaml-6.0.3.tar.gz"
)

_SDIST_ROOT = f"pyyaml-{PYYAML_VERSION}"
_UPSTREAM_PACKAGE = f"{_SDIST_ROOT}/lib/yaml"
_UPSTREAM_LICENSE = f"{_SDIST_ROOT}/LICENSE"
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
_FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_FIXED_FILE_MODE = 0o100644

# These are both build-time limits and part of the lock manifest consumed by the
# runtime verifier.  The archive produced here is much smaller than every limit.
MAX_ARCHIVE_ENTRIES = 64
MAX_ARCHIVE_ENTRY_BYTES = 512 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_DECOMPRESSION_RATIO = 100

# The fixed production hash already authenticates the official sdist.  These
# structural limits additionally keep failure behavior bounded and fail closed.
_MAX_SDIST_MEMBERS = 4096
_MAX_SDIST_MEMBER_BYTES = 4 * 1024 * 1024
_MAX_SDIST_TOTAL_FILE_BYTES = 32 * 1024 * 1024

_NOTICE = b"""# Third-Party Notices

## PyYAML 6.0.3

This audit-only archive contains selected pure-Python source files from PyYAML
6.0.3, distributed under the MIT License. The complete, unmodified upstream
license and copyright notice is included at
`THIRD_PARTY_LICENSES/PyYAML-6.0.3-LICENSE.txt`.

Modification and renaming notice: the selected package was renamed from `yaml` to `_pba_yaml`
so the audit cannot accidentally resolve an untrusted site or
`PYTHONPATH` package. The optional `cyaml.py` module, compiled extension, build
metadata, tests, and executable `__main__.py` entry are excluded. The selected
pure-Python module contents and the upstream license are otherwise unmodified.
"""


class ArtifactBuildError(RuntimeError):
    """The pinned audit artifact could not be built safely."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _validate_output_paths(
    sdist_path: Path, archive_path: Path, manifest_path: Path
) -> None:
    resolved = {
        sdist_path.resolve(strict=False),
        archive_path.resolve(strict=False),
        manifest_path.resolve(strict=False),
    }
    if len(resolved) != 3:
        raise ArtifactBuildError("source and output paths must be distinct")
    if archive_path.suffix.lower() != ".pyz":
        raise ArtifactBuildError("archive must end in .pyz")
    if manifest_path.suffix.lower() != ".json":
        raise ArtifactBuildError("manifest must end in .json")
    for output_path in (archive_path, manifest_path):
        if output_path.exists() and output_path.is_symlink():
            raise ArtifactBuildError("output path must not be a symlink")


def _read_and_verify_sdist(sdist_path: Path) -> bytes:
    if sdist_path.name != PYPI_SDIST_FILENAME:
        raise ArtifactBuildError(
            f"sdist filename must be {PYPI_SDIST_FILENAME!r}"
        )
    try:
        source_stat = sdist_path.stat()
    except OSError as exc:
        raise ArtifactBuildError("unable to stat the PyYAML sdist") from exc
    if not stat.S_ISREG(source_stat.st_mode):
        raise ArtifactBuildError("sdist must be a regular file")
    if source_stat.st_size != PYPI_SDIST_SIZE:
        raise ArtifactBuildError(
            "sdist size mismatch: expected the pinned PyPI source archive"
        )
    try:
        payload = sdist_path.read_bytes()
    except OSError as exc:
        raise ArtifactBuildError("unable to read the PyYAML sdist") from exc
    if len(payload) != PYPI_SDIST_SIZE:
        raise ArtifactBuildError(
            "sdist size changed while reading: expected the pinned PyPI source archive"
        )
    # Authentication happens before any tar parsing, so a corrupt or malicious
    # archive cannot influence parser error behavior.
    actual_sha256 = _sha256(payload)
    if actual_sha256 != PYPI_SDIST_SHA256:
        raise ArtifactBuildError(
            "sdist SHA-256 mismatch: expected the pinned PyPI source archive"
        )
    return payload


def _safe_tar_member_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        raise ArtifactBuildError("unsafe sdist path")
    if name.startswith("/"):
        raise ArtifactBuildError("unsafe sdist path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ArtifactBuildError("unsafe sdist path")
    if path.parts and path.parts[0].endswith(":"):
        raise ArtifactBuildError("unsafe sdist path")
    return path.as_posix()


def _read_selected_sources(
    sdist_payload: bytes,
) -> Mapping[str, tuple[bytes, str | None]]:
    try:
        source = tarfile.open(fileobj=io.BytesIO(sdist_payload), mode="r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise ArtifactBuildError("verified sdist is not a readable gzip tar") from exc

    with source:
        members = source.getmembers()
        if len(members) > _MAX_SDIST_MEMBERS:
            raise ArtifactBuildError("sdist has too many members")
        by_name = {}
        total_file_bytes = 0
        for member in members:
            safe_name = _safe_tar_member_name(member.name)
            if safe_name in by_name:
                raise ArtifactBuildError(f"duplicate sdist member: {safe_name}")
            if not (member.isfile() or member.isdir()):
                raise ArtifactBuildError(
                    f"unsupported sdist member type: {safe_name}"
                )
            if member.size < 0 or member.size > _MAX_SDIST_MEMBER_BYTES:
                raise ArtifactBuildError(f"sdist member is too large: {safe_name}")
            if member.isfile():
                total_file_bytes += member.size
                if total_file_bytes > _MAX_SDIST_TOTAL_FILE_BYTES:
                    raise ArtifactBuildError("sdist uncompressed content is too large")
            by_name[safe_name] = member

        required_sources = {
            f"{_UPSTREAM_PACKAGE}/{module_name}": (
                f"_pba_yaml/{module_name}"
            )
            for module_name in _PURE_MODULES
        }
        required_sources[_UPSTREAM_LICENSE] = (
            f"THIRD_PARTY_LICENSES/PyYAML-{PYYAML_VERSION}-LICENSE.txt"
        )
        selected = {}
        for source_name, output_name in required_sources.items():
            member = by_name.get(source_name)
            if member is None or not member.isfile():
                raise ArtifactBuildError(
                    f"required sdist member missing or not a file: {source_name}"
                )
            extracted = source.extractfile(member)
            if extracted is None:
                raise ArtifactBuildError(
                    f"required sdist member could not be read: {source_name}"
                )
            payload = extracted.read(member.size + 1)
            if len(payload) != member.size:
                raise ArtifactBuildError(
                    f"required sdist member size changed while reading: {source_name}"
                )
            selected[output_name] = (payload, source_name)
    selected["THIRD_PARTY_NOTICES.md"] = (_NOTICE, None)
    return selected


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=name, date_time=_FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 10
    info.external_attr = _FIXED_FILE_MODE << 16
    info.internal_attr = 0
    info.flag_bits = 0
    info.extra = b""
    info.comment = b""
    return info


def _build_archive(files: Mapping[str, tuple[bytes, str | None]]) -> bytes:
    if len(files) > MAX_ARCHIVE_ENTRIES:
        raise ArtifactBuildError("archive has too many entries")
    total_size = 0
    output = io.BytesIO()
    with zipfile.ZipFile(
        output, mode="w", compression=zipfile.ZIP_STORED, allowZip64=False
    ) as archive:
        archive.comment = b""
        for name in sorted(files):
            payload, _source_member = files[name]
            if len(payload) > MAX_ARCHIVE_ENTRY_BYTES:
                raise ArtifactBuildError(f"archive entry is too large: {name}")
            total_size += len(payload)
            if total_size > MAX_ARCHIVE_TOTAL_BYTES:
                raise ArtifactBuildError("archive uncompressed content is too large")
            archive.writestr(_zip_info(name), payload)
    return output.getvalue()


def _build_manifest(
    *,
    archive_name: str,
    archive_payload: bytes,
    files: Mapping[str, tuple[bytes, str | None]],
) -> bytes:
    file_records = {}
    for name in sorted(files):
        payload, source_member = files[name]
        file_records[name] = {
            "sha256": _sha256(payload),
            "size": len(payload),
            "source_member": source_member,
        }
    manifest = {
        "artifact": {
            "name": archive_name,
            "sha256": _sha256(archive_payload),
            "size": len(archive_payload),
        },
        "files": file_records,
        "schema_version": 1,
        "upstream": {
            "license": "MIT",
            "name": "PyYAML",
            "sdist_filename": PYPI_SDIST_FILENAME,
            "sdist_sha256": PYPI_SDIST_SHA256,
            "sdist_size": PYPI_SDIST_SIZE,
            "sdist_url": PYPI_SDIST_URL,
            "version": PYYAML_VERSION,
        },
        "zip": {
            "comment": "",
            "compression": "ZIP_STORED",
            "create_system": 3,
            "entry_count": len(files),
            "file_mode": "0100644",
            "max_decompression_ratio": MAX_ARCHIVE_DECOMPRESSION_RATIO,
            "max_entries": MAX_ARCHIVE_ENTRIES,
            "max_entry_uncompressed_bytes": MAX_ARCHIVE_ENTRY_BYTES,
            "max_total_uncompressed_bytes": MAX_ARCHIVE_TOTAL_BYTES,
            "timestamp": list(_FIXED_ZIP_TIMESTAMP),
        },
    }
    return _canonical_json(manifest)


def build_artifact(
    sdist_path: str | Path,
    archive_path: str | Path,
    manifest_path: str | Path,
) -> None:
    """Build a deterministic audit archive and its external lock manifest."""

    sdist_path = Path(sdist_path)
    archive_path = Path(archive_path)
    manifest_path = Path(manifest_path)
    _validate_output_paths(sdist_path, archive_path, manifest_path)
    sdist_payload = _read_and_verify_sdist(sdist_path)
    selected = _read_selected_sources(sdist_payload)

    archive_payload = _build_archive(selected)
    # Every invocation proves reproducibility in-process before writing either
    # output.  The test suite also repeats builds in independent directories.
    if archive_payload != _build_archive(selected):
        raise ArtifactBuildError("archive construction is not deterministic")
    manifest_payload = _build_manifest(
        archive_name=archive_path.name,
        archive_payload=archive_payload,
        files=selected,
    )

    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_bytes(archive_payload)
        manifest_path.write_bytes(manifest_payload)
    except OSError as exc:
        raise ArtifactBuildError("unable to write audit artifact outputs") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Offline builder for the pinned Member D PyYAML audit artifact"
        )
    )
    parser.add_argument(
        "--sdist",
        required=True,
        type=Path,
        help=f"already-downloaded official {PYPI_SDIST_FILENAME}",
    )
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        build_artifact(args.sdist, args.archive, args.manifest)
    except ArtifactBuildError as exc:
        raise SystemExit(f"artifact build failed: {exc}") from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
