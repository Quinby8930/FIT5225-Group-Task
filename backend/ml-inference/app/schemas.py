from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


_MEDIA_TYPES = {"image", "video"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class RequestValidationError(ValueError):
    """Raised when an /infer request does not satisfy the service contract."""


@dataclass(frozen=True)
class InferenceRequest:
    file_id: str
    media_type: str
    image_urls: tuple[str, ...]

    @property
    def source_count(self) -> int:
        return len(self.image_urls)


def _require_string(value: Any, field: str, max_length: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RequestValidationError(f"{field} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise RequestValidationError(f"{field} is too long")
    return value


def _validate_url(value: Any, field: str) -> str:
    value = _require_string(value, field, max_length=4096)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RequestValidationError(f"{field} must be an HTTPS URL")
    return value


def parse_inference_request(payload: Any, max_source_urls: int) -> InferenceRequest:
    if not isinstance(payload, dict):
        raise RequestValidationError("request body must be a JSON object")

    file_id = _require_string(payload.get("file_id"), "file_id")
    if not _SAFE_ID.fullmatch(file_id):
        raise RequestValidationError(
            "file_id must contain only letters, digits, ., _, :, or -"
        )

    media_type = _require_string(payload.get("media_type"), "media_type").lower()
    if media_type not in _MEDIA_TYPES:
        raise RequestValidationError(
            f"media_type must be one of {sorted(_MEDIA_TYPES)}"
        )

    raw_image_urls = payload.get("image_urls")
    if not isinstance(raw_image_urls, list):
        raise RequestValidationError("image_urls must be a JSON array")
    if len(raw_image_urls) > max_source_urls:
        raise RequestValidationError(
            f"image_urls cannot contain more than {max_source_urls} URLs"
        )
    image_urls = tuple(_validate_url(item, "image_urls[]") for item in raw_image_urls)
    if not image_urls:
        raise RequestValidationError("image_urls must contain at least one URL")
    if media_type == "image" and len(image_urls) != 1:
        raise RequestValidationError(
            "image requests must contain exactly one image URL"
        )

    return InferenceRequest(
        file_id=file_id,
        media_type=media_type,
        image_urls=image_urls,
    )
