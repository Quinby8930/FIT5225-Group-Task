import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


IMPORT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(IMPORT_DIR))

import prepare_import
from adoption import AdoptionError


def _scoped_arguments(command: str, tmp_path: Path) -> list[str]:
    if command == "audit":
        return [
            "audit",
            "--region",
            "ap-southeast-2",
            "--stack",
            "PacificBioArchive-Database",
            "--api",
            "2dd2aqb32j",
            "--authorizer",
            "7ir7fs",
            "--integration",
            "fbjojun",
            "--function",
            "PacificBioArchive-QueryLambda",
            "--expected-commit",
            "a" * 40,
            "--workdir",
            str(tmp_path / "audit"),
        ]
    return [
        "recovery-report",
        "--region",
        "ap-southeast-2",
        "--source-stack",
        "PacificBioArchive-Database",
        "--target-stack",
        "PacificBioArchive-QueryAdoption",
        "--expected-commit",
        "a" * 40,
        "--workdir",
        str(tmp_path / "recovery"),
    ]


@pytest.mark.parametrize("command", ["audit", "recovery-report"])
def test_invalid_yaml_runtime_is_rejected_before_aws_client(
    monkeypatch,
    tmp_path,
    command,
):
    """Removing either runtime-gate call must expose the forbidden AWS client."""
    calls = []

    monkeypatch.setattr(
        prepare_import,
        "verify_repository_identity",
        lambda *_args: calls.append("repository"),
    )

    def reject_archive():
        calls.append("archive")
        raise AdoptionError("controlled YAML audit archive is invalid")

    monkeypatch.setattr(
        prepare_import,
        "yaml_audit",
        SimpleNamespace(
            verify_isolated_interpreter=lambda: calls.append("interpreter"),
            verify_controlled_archive=reject_archive,
        ),
        raising=False,
    )

    class ForbiddenAwsCli:
        def __init__(self):
            calls.append("aws")
            raise AssertionError("AWS client constructed before archive validation")

    monkeypatch.setattr(prepare_import, "AwsCli", ForbiddenAwsCli)

    with pytest.raises(
        AdoptionError,
        match="controlled YAML audit archive is invalid",
    ):
        prepare_import.main(_scoped_arguments(command, tmp_path))

    assert calls == ["repository", "interpreter", "archive"]


def _run_git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _committed_tampered_runtime(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    import_dir = repository / "infrastructure" / "member-d" / "import"
    import_dir.mkdir(parents=True)
    for name in (
        "prepare_import.py",
        "adoption.py",
        "yaml_audit.py",
        "member-d-yaml-audit.pyz",
        "member-d-yaml-audit.lock.json",
    ):
        source = IMPORT_DIR / name
        assert source.is_file(), f"controlled runtime file is missing: {name}"
        shutil.copy2(source, import_dir / name)

    archive = import_dir / "member-d-yaml-audit.pyz"
    archive.write_bytes(archive.read_bytes() + b"tampered after controlled build")

    _run_git(repository, "init")
    _run_git(repository, "config", "user.name", "Member D Tests")
    _run_git(repository, "config", "user.email", "member-d@example.invalid")
    _run_git(repository, "add", ".")
    _run_git(repository, "commit", "-m", "tampered runtime fixture")
    return import_dir / "prepare_import.py", _run_git(repository, "rev-parse", "HEAD")


def test_clean_process_ignores_malicious_pythonpath_and_stops_before_aws(
    tmp_path,
):
    """A missing archive hash gate would reach the deliberately unavailable AWS CLI."""
    script, commit = _committed_tampered_runtime(tmp_path)
    marker = tmp_path / "malicious-imported.txt"
    malicious = tmp_path / "malicious"
    malicious.mkdir()
    payload = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('loaded', encoding='utf-8')\n"
    )
    (malicious / "yaml.py").write_text(payload, encoding="utf-8")
    (malicious / "adoption.py").write_text(payload, encoding="utf-8")
    private_package = malicious / "_pba_yaml"
    private_package.mkdir()
    (private_package / "__init__.py").write_text(payload, encoding="utf-8")

    git = shutil.which("git")
    assert git is not None
    env = os.environ.copy()
    env["PYTHONPATH"] = str(malicious)
    env["PATH"] = str(Path(git).resolve().parent)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-E",
            "-S",
            str(script),
            "audit",
            "--region",
            "ap-southeast-2",
            "--stack",
            "PacificBioArchive-Database",
            "--api",
            "2dd2aqb32j",
            "--authorizer",
            "7ir7fs",
            "--integration",
            "fbjojun",
            "--function",
            "PacificBioArchive-QueryLambda",
            "--expected-commit",
            commit,
            "--workdir",
            str(tmp_path / "work"),
        ],
        cwd=script.parents[3],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "archive" in result.stderr.lower()
    assert "AWS CLI query failed" not in result.stderr
    assert not marker.exists()
