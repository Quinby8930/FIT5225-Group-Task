"""Executable model-weight checks used by the Docker image build."""

from pathlib import Path
import subprocess
import sys

import pytest


VALIDATOR = Path(__file__).parents[1] / "scripts" / "validate_model_weights.py"
DOCKERFILE = Path(__file__).parents[1] / "Dockerfile"


def run_validator(model_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(model_dir)],
        capture_output=True,
        check=False,
        text=True,
    )


@pytest.mark.parametrize("invalid_name", ["model.pt", "mdv5a.pt"])
def test_model_weight_validator_rejects_a_missing_or_empty_weight(
    tmp_path: Path,
    invalid_name: str,
) -> None:
    for name in ("model.pt", "mdv5a.pt"):
        (tmp_path / name).write_bytes(b"" if name == invalid_name else b"weights")

    result = run_validator(tmp_path)

    assert result.returncode == 1
    assert invalid_name in result.stderr
    assert "missing or empty" in result.stderr


def test_model_weight_validator_accepts_two_non_empty_weights(tmp_path: Path) -> None:
    (tmp_path / "model.pt").write_bytes(b"classifier")
    (tmp_path / "mdv5a.pt").write_bytes(b"detector")

    result = run_validator(tmp_path)

    assert result.returncode == 0
    assert result.stderr == ""


def test_docker_build_invokes_the_weight_validator_after_copying_models() -> None:
    instructions = [
        line.strip()
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    models_copy = instructions.index("COPY models ./models")
    validator_copy = instructions.index(
        "COPY scripts/validate_model_weights.py /tmp/validate_model_weights.py"
    )
    validator_run = instructions.index(
        "RUN python /tmp/validate_model_weights.py /app/models && rm /tmp/validate_model_weights.py"
    )

    assert models_copy < validator_copy < validator_run
