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
            {"should_process": False, "state": "completed"},
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
    assert should_process == {"should_process": False, "state": "completed"}
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
    assert opener.calls[0][1] == 70


def test_metadata_timeout_default_remains_ten_seconds(monkeypatch):
    opener = RecordingUrlOpen([{"should_process": True}])
    monkeypatch.setattr(http_clients_module, "urlopen", opener)

    MetadataClient("https://metadata.example").begin_processing(
        "file-1", {"user_id": "user-1"}
    )

    assert opener.calls[0][1] == 10


@pytest.mark.parametrize(
    "response",
    [
        {
            "should_process": True,
            "state": "acquired",
            "lease_token": "a" * 43,
        },
        {"should_process": False, "state": "lease_active"},
        {"should_process": True},
    ],
)
def test_metadata_client_preserves_the_processing_state_contract(monkeypatch, response):
    monkeypatch.setattr(
        http_clients_module, "urlopen", RecordingUrlOpen([response])
    )

    result = MetadataClient("https://metadata.example").begin_processing(
        "file-1", {"user_id": "user-1"}
    )

    assert result == response


@pytest.mark.parametrize(
    "response",
    [
        {"should_process": True, "state": "completed"},
        {"should_process": False, "state": "acquired"},
        {"should_process": False, "state": "unknown"},
    ],
)
def test_metadata_client_rejects_invalid_processing_state_combinations(
    monkeypatch, response
):
    monkeypatch.setattr(
        http_clients_module, "urlopen", RecordingUrlOpen([response])
    )

    with pytest.raises(MediaPipelineError) as caught:
        MetadataClient("https://metadata.example").begin_processing(
            "file-1", {"user_id": "user-1"}
        )

    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    assert caught.value.retryable is True


@pytest.mark.parametrize(
    "response",
    [
        {"should_process": True, "state": "acquired"},
        {"should_process": True, "state": "acquired", "lease_token": "short"},
        {"should_process": True, "state": "acquired", "lease_token": "x" * 257},
        {"should_process": True, "state": "acquired", "lease_token": 123},
    ],
)
def test_metadata_client_rejects_acquired_lease_without_a_valid_token(
    monkeypatch, response
):
    monkeypatch.setattr(
        http_clients_module, "urlopen", RecordingUrlOpen([response])
    )

    with pytest.raises(MediaPipelineError) as caught:
        MetadataClient("https://metadata.example").begin_processing(
            "file-1", {"user_id": "user-1"}
        )

    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    assert caught.value.retryable is True


@pytest.mark.parametrize(
    "detections",
    [
        [{"species": "wombat", "confidence": 0.5}] * 1001,
        [{}],
        [{"species": "", "confidence": 0.5}],
        [{"species": " ", "confidence": 0.5}],
        [{"species": "w" * 129, "confidence": 0.5}],
        [{"species": "wombat", "confidence": float("nan")}],
        [{"species": "wombat", "confidence": float("inf")}],
        [{"species": "wombat", "confidence": -0.01}],
        [{"species": "wombat", "confidence": 1.01}],
        [{"species": "wombat", "confidence": True}],
    ],
)
def test_inference_client_rejects_invalid_detection_contracts(
    monkeypatch, detections
):
    monkeypatch.setattr(
        http_clients_module,
        "urlopen",
        RecordingUrlOpen(
            [{"tags": {}, "detections": detections, "model_version": "v1"}]
        ),
    )

    with pytest.raises(MediaPipelineError) as caught:
        InferenceClient("https://inference.example").infer(
            {"file_id": "file-1", "media_type": "image", "image_urls": []}
        )

    assert caught.value.code == "INFERENCE_FAILED"
    assert caught.value.retryable is False


def test_inference_client_accepts_exact_detection_boundaries(monkeypatch):
    detections = [
        {"species": "w" * 128, "confidence": 0.0},
        *[{"species": "wombat", "confidence": 0.5}] * 998,
        {"species": "dingo", "confidence": 1.0},
    ]
    response = {"tags": {}, "detections": detections, "model_version": "v1"}
    monkeypatch.setattr(
        http_clients_module, "urlopen", RecordingUrlOpen([response])
    )

    assert InferenceClient("https://inference.example").infer({}) == response


@pytest.mark.parametrize(
    "tags",
    [
        {"wombat": True},
        {"wombat": -1},
        {"wombat": 1.5},
        {"wombat": "1"},
        {"wombat": 1001},
        {"": 1},
        {"   ": 1},
        {"w" * 129: 1},
        {"wombat": 501, "dingo": 500},
        {f"species-{index}": 0 for index in range(1001)},
    ],
)
def test_inference_client_rejects_invalid_tag_contracts(monkeypatch, tags):
    monkeypatch.setattr(
        http_clients_module,
        "urlopen",
        RecordingUrlOpen(
            [{"tags": tags, "detections": [], "model_version": "v1"}]
        ),
    )

    with pytest.raises(MediaPipelineError) as caught:
        InferenceClient("https://inference.example").infer({})

    assert caught.value.code == "INFERENCE_FAILED"
    assert caught.value.retryable is False


def test_inference_client_normalizes_tag_whitespace_without_casefolding(monkeypatch):
    boundary_species = "x" * 128
    response = {
        "tags": {
            " wombat ": 600,
            "wombat": 400,
            "Wombat": 0,
            f" {boundary_species} ": 0,
        },
        "detections": [],
        "model_version": "v1",
    }
    monkeypatch.setattr(
        http_clients_module, "urlopen", RecordingUrlOpen([response])
    )

    result = InferenceClient("https://inference.example").infer({})

    assert list(result["tags"]) == ["Wombat", "wombat", boundary_species]
    assert result["tags"] == {
        "Wombat": 0,
        "wombat": 1000,
        boundary_species: 0,
    }


def test_inference_client_accepts_exact_tag_key_and_sum_boundaries(monkeypatch):
    tags = {f"species-{index:04}": 1 for index in range(1000)}
    response = {"tags": tags, "detections": [], "model_version": "v1"}
    monkeypatch.setattr(
        http_clients_module, "urlopen", RecordingUrlOpen([response])
    )

    result = InferenceClient("https://inference.example").infer({})

    assert result["tags"] == tags
    assert len(result["tags"]) == 1000
    assert sum(result["tags"].values()) == 1000


@pytest.mark.parametrize(
    "response",
    [
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
    ("status", "expected_code", "expected_retryable"),
    [
        (401, "INFERENCE_AUTH_FAILED", False),
        (400, "INFERENCE_REJECTED", False),
        (422, "INFERENCE_REJECTED", False),
        (500, "INFERENCE_UNAVAILABLE", True),
        (503, "INFERENCE_UNAVAILABLE", True),
        (504, "INFERENCE_UNAVAILABLE", True),
    ],
)
def test_inference_client_maps_http_status_taxonomy_without_remote_body(
    monkeypatch, status, expected_code, expected_retryable
):
    error = _http_error(status)
    error.fp = FakeResponse(b"secret remote diagnostic")
    monkeypatch.setattr(
        http_clients_module, "urlopen", RecordingUrlOpen([error])
    )

    with pytest.raises(MediaPipelineError) as caught:
        InferenceClient("https://inference.example").infer({})

    assert caught.value.code == expected_code
    assert caught.value.retryable is expected_retryable
    assert "secret remote diagnostic" not in str(caught.value)


@pytest.mark.parametrize(
    "failure",
    [
        URLError("connection unavailable"),
        TimeoutError("request timed out"),
    ],
)
def test_inference_client_maps_transport_failure_to_retryable_unavailable(
    monkeypatch, failure
):
    monkeypatch.setattr(
        http_clients_module, "urlopen", RecordingUrlOpen([failure])
    )

    with pytest.raises(MediaPipelineError) as caught:
        InferenceClient("https://inference.example").infer({})

    assert caught.value.code == "INFERENCE_UNAVAILABLE"
    assert caught.value.retryable is True


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
            "INFERENCE_UNAVAILABLE",
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


@pytest.mark.parametrize(
    "base_url",
    [
        "https://inference.example/infer",
        "https://inference.example/infer/",
        "https://inference.example/api/infer",
        "https://inference.example/api/infer/",
        "https://inference.example/%69nfer",
        "https://inference.example/%69nfer/",
        "https://inference.example/InFeR",
        "https://inference.example/api/%49nF%65r/",
    ],
)
def test_only_inference_client_rejects_a_decoded_infer_path_segment(base_url):
    assert MetadataClient(base_url).base_url == base_url.rstrip("/")

    with pytest.raises(ValueError, match="infer"):
        InferenceClient(base_url, internal_api_key="must-not-be-sent")


@pytest.mark.parametrize(
    ("client", "invoke", "expected_code"),
    [
        (
            MetadataClient("https://metadata.example", internal_api_key="secret-key"),
            lambda value: value.begin_processing("file-1", {"user_id": "user-1"}),
            "DEPENDENCY_UNAVAILABLE",
        ),
        (
            InferenceClient("https://inference.example", internal_api_key="secret-key"),
            lambda value: value.infer({"file_id": "file-1"}),
            "INFERENCE_UNAVAILABLE",
        ),
    ],
)
def test_authenticated_clients_reject_redirect_without_forwarding_key(
    monkeypatch, client, invoke, expected_code
):
    handler_type = getattr(http_clients_module, "_NoRedirectHandler", None)
    assert handler_type is not None
    assert http_clients_module.urlopen is http_clients_module._open_without_redirect
    original = client.base_url + "/original"
    destination = "https://attacker.example/collect"
    request = http_clients_module.Request(
        original, headers={"X-Internal-Api-Key": "secret-key"}
    )
    assert handler_type().redirect_request(
        request, None, 302, "redirect", {}, destination
    ) is None

    redirect = HTTPError(original, 302, "redirect", {"Location": destination}, None)
    transport = RecordingUrlOpen([redirect, {"should_process": True}])
    monkeypatch.setattr(http_clients_module, "urlopen", transport)

    with pytest.raises(MediaPipelineError) as caught:
        invoke(client)

    assert caught.value.code == expected_code
    assert len(transport.calls) == 1
    sent = _recorded_request(transport.calls[0][0])
    assert sent["headers"]["x-internal-api-key"] == "secret-key"
    assert transport.calls[0][0].full_url.startswith(client.base_url)
    assert all(call[0].full_url != destination for call in transport.calls)
