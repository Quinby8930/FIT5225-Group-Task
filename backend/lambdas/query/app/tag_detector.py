"""Pluggable tag detection for the "find by uploaded file" query.

Member C owns the real ML pipeline (MegaDetector + SpeciesNet). To let the
database/query work proceed in parallel, we define the interface here and ship a
stub. When C's module is ready, replace `StubTagDetector` with a thin adapter
that calls C's function — the query endpoint code does not change.

The stub can be driven by a JSON mapping (filename -> {species: count}) so the
demo shows realistic, deterministic results for the test images.
"""

from __future__ import annotations

import json
import math
import re
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


MAX_RESPONSE_BYTES = 1024 * 1024
PRESIGN_LIFETIME_SECONDS = 120


class _NoRedirectHandler(HTTPRedirectHandler):
    """Reject redirects so the internal key never crosses origins."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


def _open_without_redirect(request, *, timeout):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


class TagDetectionError(RuntimeError):
    """The remote detector could not return a trusted result."""


class TagDetectionUnavailable(TagDetectionError):
    """The configured production detector cannot be constructed."""


def _validated_https_url(value: str, *, allow_query: bool = False) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (
            parsed.scheme.lower() == "https"
            and bool(parsed.hostname)
            and (port is None or port > 0)
            and parsed.username is None
            and parsed.password is None
            and (allow_query or not parsed.query)
            and not parsed.fragment
            and not any(character.isspace() for character in value)
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("endpoint must be a valid HTTPS URL")
    return value.rstrip("/")


def _safe_segment(value: str, *, fallback: str) -> str:
    basename = Path(value.replace("\\", "/")).name
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", basename).strip("._")
    return safe or fallback


class TagDetector(ABC):
    @abstractmethod
    def detect(
        self,
        *,
        user_id: str,
        file_name: str,
        content_type: str,
        content: bytes,
    ) -> dict[str, int]:
        """Return ``{species_common_name: count}`` for one uploaded file."""


class StubTagDetector(TagDetector):
    def __init__(
        self,
        mapping: dict[str, dict[str, int]] | None = None,
        default: dict[str, int] | None = None,
    ) -> None:
        # `mapping` keys are matched against the *basename* of the uploaded file.
        self._mapping = mapping or {}
        self._default = default or {"dingo": 1}

    @classmethod
    def from_json(cls, path: str | Path) -> "StubTagDetector":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(mapping=data.get("mapping"), default=data.get("default"))

    def detect(
        self,
        *,
        user_id: str,
        file_name: str,
        content_type: str,
        content: bytes,
    ) -> dict[str, int]:
        basename = Path(file_name).name
        if basename in self._mapping:
            return dict(self._mapping[basename])
        return dict(self._default)


class RemoteTagDetector(TagDetector):
    """Stage a private image briefly and call Member C over HTTPS."""

    def __init__(
        self,
        *,
        bucket_name: str,
        inference_api_url: str,
        internal_api_key: str,
        s3_client=None,
        http_open=None,
        timeout_seconds: int = 25,
        uuid_factory=uuid4,
    ) -> None:
        if not bucket_name or not bucket_name.strip():
            raise ValueError("query input bucket must not be empty")
        if not internal_api_key:
            raise ValueError("internal API key must not be empty")
        if s3_client is None:
            import boto3

            s3_client = boto3.client("s3")
        self._bucket = bucket_name
        self._inference_api_url = _validated_https_url(inference_api_url)
        decoded_path = unquote(urlsplit(self._inference_api_url).path)
        if any(segment.casefold() == "infer" for segment in decoded_path.split("/")):
            raise ValueError(
                "inference API base URL must not contain an infer path segment"
            )
        self._internal_api_key = internal_api_key
        self._s3 = s3_client
        self._http_open = http_open or _open_without_redirect
        self._timeout_seconds = timeout_seconds
        self._uuid_factory = uuid_factory

    def detect(
        self,
        *,
        user_id: str,
        file_name: str,
        content_type: str,
        content: bytes,
    ) -> dict[str, int]:
        request_id = str(self._uuid_factory())
        safe_user = _safe_segment(user_id, fallback="unknown-user")
        safe_filename = _safe_segment(file_name, fallback="upload")
        key = f"query-inputs/{safe_user}/{request_id}/{safe_filename}"
        put_attempted = False
        primary_error: TagDetectionError | None = None
        cleanup_error: TagDetectionError | None = None
        result: dict[str, int] | None = None
        try:
            put_attempted = True
            try:
                self._s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=content,
                    ContentType=content_type,
                )
            except Exception as exc:
                raise TagDetectionError("query image staging failed") from exc
            try:
                signed_url = self._s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self._bucket, "Key": key},
                    ExpiresIn=PRESIGN_LIFETIME_SECONDS,
                )
            except Exception as exc:
                raise TagDetectionError("query image presign failed") from exc
            try:
                _validated_https_url(signed_url, allow_query=True)
            except ValueError as exc:
                raise TagDetectionError("presigned image URL must use HTTPS") from exc

            request = Request(
                f"{self._inference_api_url}/infer",
                data=json.dumps(
                    {
                        "file_id": request_id,
                        "media_type": "image",
                        "image_urls": [signed_url],
                    }
                ).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Internal-Api-Key": self._internal_api_key,
                },
                method="POST",
            )
            try:
                with self._http_open(
                    request, timeout=self._timeout_seconds
                ) as response:
                    status = getattr(response, "status", None)
                    body = response.read(MAX_RESPONSE_BYTES + 1)
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                raise TagDetectionError("inference request failed") from exc
            if type(status) is not int or not 200 <= status < 300:
                raise TagDetectionError("inference returned a non-success status")
            if not isinstance(body, bytes):
                raise TagDetectionError("inference response was malformed")
            if len(body) > MAX_RESPONSE_BYTES:
                raise TagDetectionError("inference response exceeded the size limit")
            try:
                response_payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TagDetectionError("inference response was malformed") from exc
            result = self._validated_tags(response_payload)
        except TagDetectionError as exc:
            primary_error = exc
        except Exception as exc:
            primary_error = TagDetectionError("tag detection dependency failed")
            primary_error.__cause__ = exc
        finally:
            if put_attempted:
                try:
                    self._s3.delete_object(Bucket=self._bucket, Key=key)
                except Exception as exc:
                    cleanup_error = TagDetectionError("query image cleanup failed")
                    cleanup_error.__cause__ = exc

        if primary_error is not None:
            if cleanup_error is not None:
                primary_error.add_note("query image cleanup also failed")
            raise primary_error
        if cleanup_error is not None:
            raise cleanup_error
        if result is None:
            raise TagDetectionError("inference response was malformed")
        return result

    @classmethod
    def _validated_tags(cls, result) -> dict[str, int]:
        if not (
            isinstance(result, dict)
            and isinstance(result.get("tags"), dict)
            and isinstance(result.get("detections"), list)
            and len(result["detections"]) <= 1000
            and isinstance(result.get("model_version"), str)
            and result["model_version"].strip()
            and all(cls._valid_detection(item) for item in result["detections"])
        ):
            raise TagDetectionError("inference response was malformed")
        normalised: dict[str, int] = {}
        for species, count in result["tags"].items():
            if not (
                isinstance(species, str)
                and species.strip()
                and len(species.strip()) <= 128
                and type(count) is int
                and 0 <= count <= 1000
            ):
                raise TagDetectionError("inference response was malformed")
            name = species.strip().casefold()
            normalised[name] = normalised.get(name, 0) + count
        if sum(normalised.values()) > 1000:
            raise TagDetectionError("inference response was malformed")
        return normalised

    @staticmethod
    def _valid_detection(value) -> bool:
        if not isinstance(value, dict):
            return False
        species = value.get("species")
        confidence = value.get("confidence")
        return (
            isinstance(species, str)
            and bool(species.strip())
            and len(species) <= 128
            and type(confidence) in (int, float)
            and math.isfinite(confidence)
            and 0 <= confidence <= 1
        )


class UnavailableTagDetector(TagDetector):
    """Fail closed when remote detector deployment settings are incomplete."""

    def detect(
        self,
        *,
        user_id: str,
        file_name: str,
        content_type: str,
        content: bytes,
    ) -> dict[str, int]:
        raise TagDetectionUnavailable("remote tag detector is not configured")
