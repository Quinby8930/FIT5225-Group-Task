from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

from app.backends.base import Prediction
from app.backends.mock import MockInferenceBackend
from app.config import Settings
from app.inference import InferenceService
from app.main import InferenceHandler
from app.schemas import RequestValidationError, parse_inference_request


def png_bytes(color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    image = Image.new("RGB", (4, 4), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def settings(tmp_path: Path, api_key: str | None = "test-secret") -> Settings:
    return Settings(
        model_version="test-v1",
        model_path=tmp_path / "model.pt",
        detector_model_path=tmp_path / "mdv5a.pt",
        labels_path=tmp_path / "labels.txt",
        internal_api_key=api_key,
        max_request_bytes=1024 * 1024,
        max_source_urls=3,
        max_image_bytes=1024 * 1024,
        request_timeout_seconds=5,
        confidence_threshold=0.05,
        allow_remote_urls=True,
        remote_url_timeout_seconds=5,
    )


def test_image_request_requires_exactly_one_image_url() -> None:
    with pytest.raises(RequestValidationError):
        parse_inference_request(
            {"file_id": "x", "media_type": "image"}, max_source_urls=3
        )

    with pytest.raises(RequestValidationError):
        parse_inference_request(
            {
                "file_id": "x",
                "media_type": "image",
                "image_urls": [
                    "https://example.com/a.jpg",
                    "https://example.com/b.jpg",
                ],
            },
            max_source_urls=3,
        )


def test_b_video_request_is_bounded() -> None:
    with pytest.raises(RequestValidationError):
        parse_inference_request(
            {
                "file_id": "x",
                "media_type": "video",
                "image_urls": [
                    "https://example.com/1.jpg",
                    "https://example.com/2.jpg",
                    "https://example.com/3.jpg",
                    "https://example.com/4.jpg",
                ],
            },
            max_source_urls=3,
        )


def test_inference_matches_b_response_contract() -> None:
    class Backend:
        model_version = "test-v2"

        def predict_image(self, image):
            del image
            return [
                Prediction("Casuarius_casuarius", 0.9),
                Prediction("Perameles_nasuta", 0.8),
            ]

    frames = [png_bytes(), png_bytes((40, 50, 60))]
    request = parse_inference_request(
        {
            "file_id": "video-1",
            "media_type": "video",
            "image_urls": ["https://example.com/1", "https://example.com/2"],
        },
        max_source_urls=3,
    )
    result = InferenceService(Backend(), lambda url: frames[int(url.rsplit("/", 1)[1]) - 1]).infer(request)
    assert result.model_version == "test-v2"
    assert result.tags == {"Casuarius_casuarius": 2, "Perameles_nasuta": 2}
    assert result.as_dict() == {
        "tags": {"Casuarius_casuarius": 2, "Perameles_nasuta": 2},
        "detections": [
            {"species": "Casuarius_casuarius", "confidence": 0.9},
            {"species": "Perameles_nasuta", "confidence": 0.8},
            {"species": "Casuarius_casuarius", "confidence": 0.9},
            {"species": "Perameles_nasuta", "confidence": 0.8},
        ],
        "model_version": "test-v2",
    }


def running_server(tmp_path: Path):
    config = settings(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), InferenceHandler)
    server.settings = config
    server.inference_service = InferenceService(
        MockInferenceBackend(), lambda _: png_bytes()
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def post(server, payload, headers=None):
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/infer",
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_http_authentication_and_inference(tmp_path: Path) -> None:
    server, thread = running_server(tmp_path)
    try:
        status, body = post(
            server,
            {
                "file_id": "image-1",
                "media_type": "image",
                "image_urls": ["https://example.com/image.jpg"],
            },
        )
        assert status == 401
        assert body["error"] == "unauthorized"

        status, body = post(
            server,
            {
                "file_id": "image-1",
                "media_type": "image",
                "image_urls": ["https://example.com/image.jpg"],
            },
            {"X-Internal-Api-Key": "test-secret"},
        )
        assert status == 200
        assert set(body) == {"tags", "detections", "model_version"}
        assert body["model_version"] == "mock-v1"
        assert body["detections"] == [{"species": "unknown", "confidence": 0.0}]
    finally:
        server.shutdown()
        thread.join(timeout=2)
