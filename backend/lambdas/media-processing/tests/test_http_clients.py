import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from media_pipeline.errors import MediaPipelineError
from media_pipeline.http_clients import InferenceClient, MetadataClient


@contextmanager
def recording_server(responses):
    requests = []
    queued = list(responses)

    class Handler(BaseHTTPRequestHandler):
        def _respond(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": dict(self.headers),
                    "json": json.loads(body) if body else None,
                }
            )
            status, payload, content_type = queued.pop(0)
            encoded = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        do_POST = _respond
        do_PUT = _respond

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_metadata_client_serializes_all_contract_methods_and_real_false_lease():
    responses = [
        (200, {"should_process": False}, "application/json"),
        (200, {"ok": True}, "application/json"),
        (200, {"ok": True}, "application/json"),
    ]
    with recording_server(responses) as (base_url, requests):
        client = MetadataClient(base_url, internal_api_key="internal-test-key")
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
        assert request["headers"]["Content-Type"] == "application/json"
        assert request["headers"]["X-Internal-Api-Key"] == "internal-test-key"


@pytest.mark.parametrize(
    "response",
    [
        (503, {"error": "unavailable"}, "application/json"),
        (200, b"not-json", "application/json"),
        (200, {"should_process": "yes"}, "application/json"),
    ],
)
def test_metadata_client_maps_http_json_and_schema_failures_to_retryable_dependency_error(
    response,
):
    with recording_server([response]) as (base_url, _):
        client = MetadataClient(base_url)
        with pytest.raises(MediaPipelineError) as caught:
            client.begin_processing("file-1", {"user_id": "user-1"})

    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    assert caught.value.retryable is True


def test_inference_client_posts_exact_contract_and_validates_response():
    response = {
        "tags": {"wombat": 2},
        "detections": [{"species": "wombat", "confidence": 0.94}],
        "model_version": "speciesnet-v1",
    }
    with recording_server([(200, response, "application/json")]) as (
        base_url,
        requests,
    ):
        result = InferenceClient(base_url, internal_api_key="internal-test-key").infer(
            {
                "file_id": "file-1",
                "media_type": "image",
                "image_urls": ["https://signed.example/original"],
            }
        )

    assert result == response
    assert requests == [
        {
            "method": "POST",
            "path": "/infer",
            "headers": requests[0]["headers"],
            "json": {
                "file_id": "file-1",
                "media_type": "image",
                "image_urls": ["https://signed.example/original"],
            },
        }
    ]
    assert requests[0]["headers"]["X-Internal-Api-Key"] == "internal-test-key"


@pytest.mark.parametrize(
    "response",
    [
        (500, {"error": "failed"}, "application/json"),
        (200, b"not-json", "application/json"),
        (200, {"tags": [], "detections": [], "model_version": "v1"}, "application/json"),
        (200, {"tags": {}, "detections": {}, "model_version": "v1"}, "application/json"),
        (200, {"tags": {}, "detections": [], "model_version": ""}, "application/json"),
    ],
)
def test_inference_client_maps_http_json_and_schema_failures(response):
    with recording_server([response]) as (base_url, _):
        with pytest.raises(MediaPipelineError) as caught:
            InferenceClient(base_url).infer(
                {"file_id": "file-1", "media_type": "image", "image_urls": []}
            )

    assert caught.value.code == "INFERENCE_FAILED"


@pytest.mark.parametrize(
    ("client_factory", "invoke", "expected_code"),
    [
        (
            lambda url: MetadataClient(url, timeout=0.2),
            lambda client: client.begin_processing("file-1", {"user_id": "user-1"}),
            "DEPENDENCY_UNAVAILABLE",
        ),
        (
            lambda url: InferenceClient(url, timeout=0.2),
            lambda client: client.infer(
                {"file_id": "file-1", "media_type": "image", "image_urls": []}
            ),
            "INFERENCE_FAILED",
        ),
    ],
)
def test_http_clients_map_network_failures(client_factory, invoke, expected_code):
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{unavailable.getsockname()[1]}"

    with pytest.raises(MediaPipelineError) as caught:
        invoke(client_factory(url))

    assert caught.value.code == expected_code
