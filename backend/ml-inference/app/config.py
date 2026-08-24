from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class Settings:
    model_version: str
    model_path: Path
    detector_model_path: Path
    labels_path: Path
    internal_api_key: str | None
    max_request_bytes: int
    max_source_urls: int
    max_image_bytes: int
    request_timeout_seconds: int
    confidence_threshold: float
    allow_remote_urls: bool
    remote_url_timeout_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.getenv("ML_SERVICE_ROOT", Path(__file__).resolve().parents[1]))
        model_dir = Path(os.getenv("MODEL_DIR", root / "models"))
        confidence = float(os.getenv("CONFIDENCE_THRESHOLD", "0.05"))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("CONFIDENCE_THRESHOLD must be between 0 and 1")

        key = os.getenv("INTERNAL_API_KEY")
        if key == "":
            key = None

        return cls(
            model_version=os.getenv("MODEL_VERSION", "v1"),
            model_path=Path(os.getenv("MODEL_PATH", model_dir / "model.pt")),
            detector_model_path=Path(
                os.getenv("DETECTOR_MODEL_PATH", model_dir / "mdv5a.pt")
            ),
            labels_path=Path(os.getenv("LABELS_PATH", root / "config" / "labels.txt")),
            internal_api_key=key,
            max_request_bytes=_positive_int("MAX_REQUEST_BYTES", 25 * 1024 * 1024),
            max_source_urls=_positive_int("MAX_SOURCE_URLS", 900),
            max_image_bytes=_positive_int("MAX_IMAGE_BYTES", 12 * 1024 * 1024),
            request_timeout_seconds=_positive_int("INFER_TIMEOUT_SECONDS", 45),
            confidence_threshold=confidence,
            allow_remote_urls=os.getenv("ALLOW_REMOTE_URLS", "true").lower()
            in {"1", "true", "yes"},
            remote_url_timeout_seconds=_positive_int("REMOTE_URL_TIMEOUT_SECONDS", 20),
        )
