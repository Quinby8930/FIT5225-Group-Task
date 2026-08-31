"""Fail a container build when the course model weights are unavailable."""

from __future__ import annotations

from pathlib import Path
import sys


REQUIRED_WEIGHTS = ("model.pt", "mdv5a.pt")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: validate_model_weights.py MODEL_DIRECTORY", file=sys.stderr)
        return 2

    model_dir = Path(args[0])
    invalid = [
        name
        for name in REQUIRED_WEIGHTS
        if not (model_dir / name).is_file() or (model_dir / name).stat().st_size == 0
    ]
    if invalid:
        print(
            "Required model weight(s) missing or empty: " + ", ".join(invalid),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
