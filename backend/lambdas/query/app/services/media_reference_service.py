"""Normalize untrusted archive-media references without fetching them."""

from __future__ import annotations

import re
from urllib.parse import unquote_to_bytes, urlsplit


MAX_MEDIA_KEY_BYTES = 1024
_ARCHIVE_PREFIXES = ("originals/", "thumbnails/")
_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_VIRTUAL_HOST = re.compile(r"^(?P<bucket>[a-z0-9.-]+)\.s3(?:\.[a-z0-9-]+)?\.amazonaws\.com$")
_PATH_HOST = re.compile(r"^s3(?:\.[a-z0-9-]+)?\.amazonaws\.com$")


class MediaReferenceError(ValueError):
    """Raised when a user supplied value is not a safe archive media reference."""


def normalize_media_reference(reference: str, bucket: str) -> str:
    """Return a canonical S3 key for a direct key or this bucket's HTTPS URL.

    This deliberately parses only; it never fetches, signs, or logs a URL.
    """
    if not isinstance(reference, str) or not reference:
        raise MediaReferenceError("invalid media reference")

    try:
        parsed = urlsplit(reference)
    except ValueError as exc:
        raise MediaReferenceError("invalid media reference") from exc

    if parsed.scheme:
        if not bucket.strip():
            raise MediaReferenceError("invalid media reference")
        key = _key_from_url(parsed, bucket)
        return _validate_key(key, already_decoded=True)
    else:
        if parsed.netloc or parsed.query or parsed.fragment:
            raise MediaReferenceError("invalid media reference")
        key = reference
    return _validate_key(key, already_decoded=False)


def _key_from_url(parsed, bucket: str) -> str:
    try:
        has_invalid_authority = (
            not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
        )
    except ValueError as exc:
        raise MediaReferenceError("invalid media reference") from exc
    if parsed.scheme.lower() != "https" or has_invalid_authority or parsed.fragment:
        raise MediaReferenceError("invalid media reference")
    _validate_percent_encoding(parsed.path)
    _validate_percent_encoding(parsed.query)
    host = (parsed.hostname or "").lower()
    expected_bucket = bucket.strip().lower()
    virtual = _VIRTUAL_HOST.fullmatch(host)
    if virtual and virtual.group("bucket") == expected_bucket:
        return _decode_path(parsed.path)
    if _PATH_HOST.fullmatch(host):
        path = _decode_path(parsed.path)
        prefix = f"{bucket.strip()}/"
        if path.startswith(prefix):
            return path[len(prefix) :]
    raise MediaReferenceError("invalid media reference")


def _decode_path(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        raise MediaReferenceError("invalid media reference")
    try:
        return unquote_to_bytes(value[1:]).decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise MediaReferenceError("invalid media reference") from exc


def _validate_percent_encoding(value: str) -> None:
    if _PERCENT_ESCAPE.search(value):
        raise MediaReferenceError("invalid media reference")


def _validate_key(key: str, *, already_decoded: bool) -> str:
    if already_decoded:
        decoded = key
    else:
        _validate_percent_encoding(key)
        try:
            decoded = unquote_to_bytes(key).decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise MediaReferenceError("invalid media reference") from exc
    if (
        not decoded.startswith(_ARCHIVE_PREFIXES)
        or "\\" in decoded
        or any(segment in {"", ".", ".."} for segment in decoded.split("/"))
        or len(decoded.encode("utf-8")) > MAX_MEDIA_KEY_BYTES
    ):
        raise MediaReferenceError("invalid media reference")
    return decoded
