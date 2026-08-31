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


def _probability(
    name: str,
    default: float,
    *,
    fallback_name: str | None = None,
) -> float:
    raw = os.getenv(name)
    if raw is None and fallback_name is not None:
        raw = os.getenv(fallback_name)
    try:
        value = float(str(default) if raw is None else raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


def _source_hosts() -> tuple[str, ...]:
    raw = os.getenv(
        "ALLOWED_SOURCE_HOSTS",
        "s3.amazonaws.com,s3.ap-southeast-2.amazonaws.com",
    )
    hosts = tuple(
        dict.fromkeys(
            host.strip().lower().rstrip(".")
            for host in raw.split(",")
            if host.strip()
        )
    )
    if not hosts:
        raise ValueError("ALLOWED_SOURCE_HOSTS must contain at least one host")
    return hosts


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
    max_detections: int
    request_timeout_seconds: int
    detection_threshold: float
    species_confidence_threshold: float
    allow_remote_urls: bool
    remote_url_timeout_seconds: int
    allow_unauthenticated_inference: bool = False
    allowed_source_hosts: tuple[str, ...] = (
        "s3.amazonaws.com",
        "s3.ap-southeast-2.amazonaws.com",
    )
    max_image_pixels: int = 40_000_000

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.getenv("ML_SERVICE_ROOT", Path(__file__).resolve().parents[1]))
        model_dir = Path(os.getenv("MODEL_DIR", root / "models"))
        detection_threshold = _probability(
            "DETECTION_THRESHOLD",
            0.05,
            fallback_name="CONFIDENCE_THRESHOLD",
        )
        species_confidence_threshold = _probability(
            "SPECIES_CONFIDENCE_THRESHOLD",
            0.45,
        )

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
            max_image_pixels=_positive_int("MAX_IMAGE_PIXELS", 40_000_000),
            max_detections=_positive_int("MAX_DETECTIONS", 1000),
            request_timeout_seconds=_positive_int("INFER_TIMEOUT_SECONDS", 45),
            detection_threshold=detection_threshold,
            species_confidence_threshold=species_confidence_threshold,
            allow_remote_urls=os.getenv("ALLOW_REMOTE_URLS", "true").lower()
            in {"1", "true", "yes"},
            remote_url_timeout_seconds=_positive_int("REMOTE_URL_TIMEOUT_SECONDS", 20),
            allow_unauthenticated_inference=os.getenv(
                "ALLOW_UNAUTHENTICATED_INFERENCE", "false"
            ).lower()
            in {"1", "true", "yes"},
            allowed_source_hosts=_source_hosts(),
        )
