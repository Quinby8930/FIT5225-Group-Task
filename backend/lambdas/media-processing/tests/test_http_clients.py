import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import pytest

import media_pipeline.http_clients as http_clients_module
from media_pipeline.errors import MediaPipelineError
from media_pipeline.http_clients import InferenceClient, MetadataClient


def _encoded(payload):
    return payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")


class FakeResponse:
    def __init__(self, payload):
        self.body = _encoded(payload)
        self.read_limits = []

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        return False

    def read(self, limit=-1):
        self.read_limits.append(limit)
        return self.body if limit < 0 else self.body[:limit]


class RecordingUrlOpen:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response if isinstance(response, FakeResponse) else FakeResponse(response)


def _recorded_request(request):
    return {
        "method": request.get_method(),
        "path": urlsplit(request.full_url).path,
        "headers": {name.lower(): value for name, value in request.header_items()},
        "json": json.loads(request.data) if request.data else None,
    }


def _http_error(status):
    return HTTPError(
        "https://dependency.example/failure",
        status,
        "dependency failure",
        {},
        None,
    )


def test_metadata_client_serializes_all_contract_methods_and_real_false_lease(
    monkeypatch,
):
    opener = RecordingUrlOpen(
        [
            {"should_process": False},
            {"ok": True},
            {"ok": True},
        ]
    )
    monkeypatch.setattr(http_clients_module, "urlopen", opener)
    client = MetadataClient(
        "https://metadata.example", internal_api_key="internal-test-key"
    )

    should_process = client.begin_processing(
        "file-1",
        {
            "user_id": "user-1",
            "object_key": "originals/user-1/file-1/wombat.jpg",
            "sequencer": "abc123",
        },
    )
    client.complete("file-1", {"status": "completed", "thumbnail_key": None})
    client.fail(
        "file-1",
        {
            "user_id": "user-1",
            "error_code": "INVALID_MEDIA",
            "message": "bad image",
            "status": "failed",
        },
    )

    requests = [_recorded_request(request) for request, _ in opener.calls]
    assert should_process is False
    assert [(request["method"], request["path"]) for request in requests] == [
        ("POST", "/internal/files/file-1/processing"),
        ("PUT", "/internal/files/file-1/complete"),
        ("PUT", "/internal/files/file-1/failed"),
    ]
    assert requests[0]["json"] == {
        "user_id": "user-1",
        "object_key": "originals/user-1/file-1/wombat.jpg",
        "sequencer": "abc123",
    }
    assert requests[1]["json"] == {"status": "completed", "thumbnail_key": None}
    assert requests[2]["json"]["error_code"] == "INVALID_MEDIA"
    for request in requests:
        assert request["headers"]["content-type"] == "application/json"
        assert request["headers"]["x-internal-api-key"] == "internal-test-key"


@pytest.mark.parametrize(
    "response",
    [
        _http_error(503),
        b"not-json",
        {"should_process": "yes"},
    ],
)
def test_metadata_client_maps_http_json_and_schema_failures_to_retryable_dependency_error(
    monkeypatch,
    response,
):
    monkeypatch.setattr(
        http_clients_module,
        "urlopen",
        RecordingUrlOpen([response]),
    )
    client = MetadataClient("https://metadata.example")

    with pytest.raises(MediaPipelineError) as caught:
        client.begin_processing("file-1", {"user_id": "user-1"})

    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    assert caught.value.retryable is True


def test_inference_client_posts_exact_contract_and_validates_response(monkeypatch):
    response = {
        "tags": {"wombat": 2},
        "detections": [{"species": "wombat", "confidence": 0.94}],
        "model_version": "speciesnet-v1",
    }
    opener = RecordingUrlOpen([response])
    monkeypatch.setattr(http_clients_module, "urlopen", opener)

    result = InferenceClient(
        "https://inference.example", internal_api_key="internal-test-key"
    ).infer(
        {
            "file_id": "file-1",
            "media_type": "image",
            "image_urls": ["https://signed.example/original"],
        }
    )

    request = _recorded_request(opener.calls[0][0])
    assert result == response
    assert request == {
        "method": "POST",
        "path": "/infer",
        "headers": request["headers"],
        "json": {
            "file_id": "file-1",
            "media_type": "image",
            "image_urls": ["https://signed.example/original"],
        },
    }
    assert request["headers"]["x-internal-api-key"] == "internal-test-key"


@pytest.mark.parametrize(
    "response",
    [
        _http_error(500),
        b"not-json",
        {"tags": [], "detections": [], "model_version": "v1"},
        {"tags": {}, "detections": {}, "model_version": "v1"},
        {"tags": {}, "detections": [], "model_version": ""},
    ],
)
def test_inference_client_maps_http_json_and_schema_failures(monkeypatch, response):
    monkeypatch.setattr(
        http_clients_module,
        "urlopen",
        RecordingUrlOpen([response]),
    )

    with pytest.raises(MediaPipelineError) as caught:
        InferenceClient("https://inference.example").infer(
            {"file_id": "file-1", "media_type": "image", "image_urls": []}
        )

    assert caught.value.code == "INFERENCE_FAILED"


@pytest.mark.parametrize(
    ("client_factory", "invoke", "expected_code"),
    [
        (
            lambda: MetadataClient("https://metadata.example", timeout=0.2),
            lambda client: client.begin_processing("file-1", {"user_id": "user-1"}),
            "DEPENDENCY_UNAVAILABLE",
        ),
        (
            lambda: InferenceClient("https://inference.example", timeout=0.2),
            lambda client: client.infer(
                {"file_id": "file-1", "media_type": "image", "image_urls": []}
            ),
            "INFERENCE_FAILED",
        ),
    ],
)
def test_http_clients_map_network_failures(
    monkeypatch,
    client_factory,
    invoke,
    expected_code,
):
    monkeypatch.setattr(
        http_clients_module,
        "urlopen",
        RecordingUrlOpen([URLError("connection unavailable")]),
    )

    with pytest.raises(MediaPipelineError) as caught:
        invoke(client_factory())

    assert caught.value.code == expected_code


@pytest.mark.parametrize(
    ("client_factory", "invoke", "expected_code", "expected_retryable"),
    [
        (
            lambda: MetadataClient("https://metadata.example"),
            lambda client: client.begin_processing("file-1", {"user_id": "user-1"}),
            "DEPENDENCY_UNAVAILABLE",
            True,
        ),
        (
            lambda: InferenceClient("https://inference.example"),
            lambda client: client.infer(
                {"file_id": "file-1", "media_type": "image", "image_urls": []}
            ),
            "INFERENCE_FAILED",
            False,
        ),
    ],
)
def test_http_clients_map_invalid_utf8_to_their_standard_dependency_error(
    monkeypatch,
    client_factory,
    invoke,
    expected_code,
    expected_retryable,
):
    monkeypatch.setattr(
        http_clients_module,
        "urlopen",
        RecordingUrlOpen([b"\xff"]),
    )

    with pytest.raises(MediaPipelineError) as caught:
        invoke(client_factory())

    assert caught.value.code == expected_code
    assert caught.value.retryable is expected_retryable


def test_inference_client_caps_json_response_reads_at_one_mib(monkeypatch):
    response = FakeResponse(b"x" * 1_048_577)
    monkeypatch.setattr(
        http_clients_module,
        "urlopen",
        RecordingUrlOpen([response]),
    )

    with pytest.raises(MediaPipelineError) as caught:
        InferenceClient("https://inference.example").infer(
            {"file_id": "file-1", "media_type": "image", "image_urls": []}
        )

    assert caught.value.code == "INFERENCE_FAILED"
    assert response.read_limits == [1_048_577]


@pytest.mark.parametrize(
    ("client_type", "base_url"),
    [
        (MetadataClient, "http://metadata.example"),
        (InferenceClient, "http://inference.example"),
        (MetadataClient, "ftp://metadata.example"),
        (InferenceClient, "not-a-url"),
        (MetadataClient, "https://metadata.example:99999"),
        (InferenceClient, "https://inference.example:invalid"),
    ],
)
def test_internal_http_clients_reject_non_https_base_urls(client_type, base_url):
    with pytest.raises(ValueError, match="HTTPS"):
        client_type(base_url, internal_api_key="must-not-be-sent")
