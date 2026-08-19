import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .errors import MediaPipelineError


class _JsonHttpClient:
    def __init__(self, base_url, *, internal_api_key=None, timeout=10):
        self.base_url = base_url.rstrip("/")
        self.internal_api_key = internal_api_key
        self.timeout = timeout

    def _request(self, method, path, payload, *, error_code, retryable=False):
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
                body = response.read()
            return json.loads(body)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
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
        return result["should_process"]

    def complete(self, file_id, payload):
        self._metadata_request("PUT", file_id, "complete", payload)

    def fail(self, file_id, payload):
        self._metadata_request("PUT", file_id, "failed", payload)


class InferenceClient(_JsonHttpClient):
    def infer(self, payload):
        result = self._request(
            "POST",
            "/infer",
            payload,
            error_code="INFERENCE_FAILED",
        )
        if not (
            isinstance(result, dict)
            and isinstance(result.get("tags"), dict)
            and isinstance(result.get("detections"), list)
            and isinstance(result.get("model_version"), str)
            and result["model_version"]
        ):
            raise MediaPipelineError(
                "INFERENCE_FAILED", "Inference response did not match its contract"
            )
        return result
