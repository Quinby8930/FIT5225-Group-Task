import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .errors import MediaPipelineError


MAX_JSON_RESPONSE_BYTES = 1024 * 1024


class _NoRedirectHandler(HTTPRedirectHandler):
    """Reject redirects so an internal key is never forwarded."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


def _open_without_redirect(request, *, timeout):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


# Stable injection point retained for the existing static client tests.
urlopen = _open_without_redirect


def _validated_https_base_url(base_url):
    value = str(base_url)
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (
            parsed.scheme.lower() == "https"
            and bool(parsed.hostname)
            and (port is None or port > 0)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and not any(character.isspace() for character in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("base_url must be a valid HTTPS URL")
    return value.rstrip("/")


class _JsonHttpClient:
    def __init__(self, base_url, *, internal_api_key=None, timeout=10):
        self.base_url = _validated_https_base_url(base_url)
        self.internal_api_key = internal_api_key
        self.timeout = timeout

    def _request(
        self,
        method,
        path,
        payload,
        *,
        error_code,
        retryable=False,
        http_error_mapper=None,
        transport_error_code=None,
        transport_retryable=None,
    ):
        headers = {"Content-Type": "application/json"}
        if self.internal_api_key:
            headers["X-Internal-Api-Key"] = self.internal_api_key
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read(MAX_JSON_RESPONSE_BYTES + 1)
            if len(body) > MAX_JSON_RESPONSE_BYTES:
                raise MediaPipelineError(
                    error_code,
                    "HTTP dependency response exceeded the size limit",
                    retryable=retryable,
                )
            return json.loads(body.decode("utf-8"))
        except HTTPError as error:
            if http_error_mapper is None:
                mapped_code, mapped_retryable = error_code, retryable
            else:
                mapped_code, mapped_retryable = http_error_mapper(error.code)
            raise MediaPipelineError(
                mapped_code,
                "HTTP dependency request failed",
                retryable=mapped_retryable,
            ) from error
        except (
            URLError,
            TimeoutError,
            OSError,
        ) as error:
            raise MediaPipelineError(
                transport_error_code or error_code,
                "HTTP dependency request failed",
                retryable=(
                    retryable
                    if transport_retryable is None
                    else transport_retryable
                ),
            ) from error
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            raise MediaPipelineError(
                error_code,
                "HTTP dependency request failed",
                retryable=retryable,
            ) from error


class MetadataClient(_JsonHttpClient):
    def _metadata_request(self, method, file_id, action, payload):
        path = f"/internal/files/{quote(file_id, safe='')}/{action}"
        result = self._request(
            method,
            path,
            payload,
            error_code="DEPENDENCY_UNAVAILABLE",
            retryable=True,
        )
        if not isinstance(result, dict):
            raise MediaPipelineError(
                "DEPENDENCY_UNAVAILABLE",
                "Metadata response did not match its contract",
                retryable=True,
            )
        return result

    def begin_processing(self, file_id, payload):
        result = self._metadata_request("POST", file_id, "processing", payload)
        if type(result.get("should_process")) is not bool:
            raise MediaPipelineError(
                "DEPENDENCY_UNAVAILABLE",
                "Metadata response did not match its contract",
                retryable=True,
            )
        if "state" in result and (
            (
                result["state"] == "acquired"
                and result["should_process"] is True
                and isinstance(result.get("lease_token"), str)
                and 32 <= len(result["lease_token"]) <= 256
            )
            or (
                result["state"] in {"completed", "lease_active"}
                and result["should_process"] is False
            )
        ):
            return result
        if "state" not in result:
            return result
        raise MediaPipelineError(
            "DEPENDENCY_UNAVAILABLE",
            "Metadata response did not match its contract",
            retryable=True,
        )

    def complete(self, file_id, payload):
        self._metadata_request("PUT", file_id, "complete", payload)

    def fail(self, file_id, payload):
        self._metadata_request("PUT", file_id, "failed", payload)


class InferenceClient(_JsonHttpClient):
    def __init__(self, base_url, *, internal_api_key=None, timeout=70):
        super().__init__(
            base_url, internal_api_key=internal_api_key, timeout=timeout
        )
        decoded_path = unquote(urlsplit(self.base_url).path)
        if any(segment.lower() == "infer" for segment in decoded_path.split("/")):
            raise ValueError("inference base_url must not contain an infer path segment")

    @staticmethod
    def _map_http_error(status):
        if status == 401:
            return "INFERENCE_AUTH_FAILED", False
        if 400 <= status < 500:
            return "INFERENCE_REJECTED", False
        return "INFERENCE_UNAVAILABLE", True

    def infer(self, payload):
        result = self._request(
            "POST",
            "/infer",
            payload,
            error_code="INFERENCE_FAILED",
            http_error_mapper=self._map_http_error,
            transport_error_code="INFERENCE_UNAVAILABLE",
            transport_retryable=True,
        )
        normalized_tags = (
            self._normalize_tags(result.get("tags"))
            if isinstance(result, dict)
            else None
        )
        if not (
            isinstance(result, dict)
            and normalized_tags is not None
            and isinstance(result.get("detections"), list)
            and len(result["detections"]) <= 1000
            and isinstance(result.get("model_version"), str)
            and result["model_version"].strip()
            and all(self._valid_detection(item) for item in result["detections"])
        ):
            raise MediaPipelineError(
                "INFERENCE_FAILED", "Inference response did not match its contract"
            )
        return {**result, "tags": normalized_tags}

    @staticmethod
    def _normalize_tags(tags):
        if not isinstance(tags, dict) or len(tags) > 1000:
            return None
        normalized = {}
        response_total = 0
        for species, count in tags.items():
            if not isinstance(species, str):
                return None
            species = species.strip()
            if not species or len(species) > 128:
                return None
            if type(count) is not int or not 0 <= count <= 1000:
                return None
            response_total += count
            if response_total > 1000:
                return None
            normalized[species] = normalized.get(species, 0) + count
            if normalized[species] > 1000:
                return None
        if sum(normalized.values()) > 1000:
            return None
        return dict(sorted(normalized.items()))

    @staticmethod
    def _valid_detection(value):
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
