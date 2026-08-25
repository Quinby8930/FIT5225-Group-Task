from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from PIL import Image

from app.backends.base import Prediction
from app.backends.mock import MockInferenceBackend
from app.config import Settings
from app.inference import InferenceService
from app.main import InferenceHandler, InferenceServer
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
        max_detections=1000,
        request_timeout_seconds=5,
        confidence_threshold=0.05,
        allow_remote_urls=True,
        remote_url_timeout_seconds=5,
    )


def test_auth_config_defaults_closed_and_requires_explicit_local_switch(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ML_SERVICE_ROOT", str(tmp_path))
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    monkeypatch.delenv("ALLOW_UNAUTHENTICATED_INFERENCE", raising=False)

    closed = Settings.from_env()
    assert closed.internal_api_key is None
    assert closed.allow_unauthenticated_inference is False

    monkeypatch.setenv("INTERNAL_API_KEY", "")
    assert Settings.from_env().internal_api_key is None

    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_INFERENCE", "true")
    local_only = Settings.from_env()
    assert local_only.allow_unauthenticated_inference is True


def test_production_manifest_explicitly_disables_unauthenticated_inference() -> None:
    manifest = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "s.yaml").read_text(encoding="utf-8")
    )
    environment = manifest["resources"]["ml_inference"]["props"][
        "environmentVariables"
    ]
    assert environment["ALLOW_UNAUTHENTICATED_INFERENCE"] == "false"


def test_species_mapper_uses_scientific_columns_and_team_short_name() -> None:
    from app.species import SpeciesMapper

    mapper = SpeciesMapper.from_file(
        Path(__file__).resolve().parents[1] / "config" / "labels.txt"
    )

    assert mapper.normalize("Canis_familiaris") == "dingo"
    assert mapper.normalize("Canis_dingo") == "dingo"
    assert mapper.normalize("Vombatus_ursinus") == "wombat"
    assert mapper.normalize("Casuarius_casuarius") == "cassowary"
    assert mapper.normalize("cAnIs_FaMiLiArIs") == "dingo"
    assert mapper.normalize("Unlisted_species") == "Unlisted_species"


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
    from app.species import SpeciesMapper

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
    result = InferenceService(
        Backend(),
        lambda url: frames[int(url.rsplit("/", 1)[1]) - 1],
        SpeciesMapper.from_file(
            Path(__file__).resolve().parents[1] / "config" / "labels.txt"
        ),
        max_detections=1000,
    ).infer(request, deadline=time.monotonic() + 5)
    assert result.model_version == "test-v2"
    assert result.tags == {"bandicoot": 2, "cassowary": 2}
    assert result.as_dict() == {
        "tags": {"bandicoot": 2, "cassowary": 2},
        "detections": [
            {"species": "cassowary", "confidence": 0.9},
            {"species": "bandicoot", "confidence": 0.8},
            {"species": "cassowary", "confidence": 0.9},
            {"species": "bandicoot", "confidence": 0.8},
        ],
        "model_version": "test-v2",
    }


def test_inference_fetches_predicts_and_closes_one_source_at_a_time() -> None:
    from app.species import SpeciesMapper

    outstanding = 0
    images = []

    def fetch_url(url):
        nonlocal outstanding
        assert outstanding == 0
        outstanding = 1
        return png_bytes((int(url.rsplit("/", 1)[1]), 20, 30))

    class Backend:
        model_version = "test-v1"

        def predict_image(self, image):
            nonlocal outstanding
            assert outstanding == 1
            outstanding = 0
            images.append(image)
            return [Prediction("Canis_dingo", 0.75)]

    request = parse_inference_request(
        {
            "file_id": "video-streaming",
            "media_type": "video",
            "image_urls": ["https://example.com/1", "https://example.com/2"],
        },
        max_source_urls=3,
    )

    result = InferenceService(
        Backend(), fetch_url, SpeciesMapper({}), max_detections=1000
    ).infer(request, deadline=time.monotonic() + 5)

    assert result.tags == {"Canis_dingo": 2}
    assert outstanding == 0
    assert len(images) == 2
    for image in images:
        with pytest.raises(ValueError):
            image.getpixel((0, 0))


def test_inference_raises_when_monotonic_deadline_is_crossed(monkeypatch) -> None:
    from app import inference as inference_module
    from app.inference import InferenceTimeoutError
    from app.species import SpeciesMapper

    ticks = iter([10.0, 10.1, 11.1])
    monkeypatch.setattr(inference_module.time, "monotonic", lambda: next(ticks))
    request = parse_inference_request(
        {
            "file_id": "image-timeout",
            "media_type": "image",
            "image_urls": ["https://example.com/image"],
        },
        max_source_urls=3,
    )

    with pytest.raises(InferenceTimeoutError):
        InferenceService(
            MockInferenceBackend(),
            lambda _: png_bytes(),
            SpeciesMapper({}),
            max_detections=1000,
        ).infer(request, deadline=11.0)


def test_inference_rejects_detection_1001_without_a_partial_result() -> None:
    from app.inference import InferenceResultLimitError
    from app.species import SpeciesMapper

    class Backend:
        model_version = "test-v1"

        def predict_image(self, image):
            del image
            return [Prediction("Canis_dingo", 0.5)] * 1001

    request = parse_inference_request(
        {
            "file_id": "image-overflow",
            "media_type": "image",
            "image_urls": ["https://example.com/image"],
        },
        max_source_urls=3,
    )

    with pytest.raises(InferenceResultLimitError):
        InferenceService(
            Backend(),
            lambda _: png_bytes(),
            SpeciesMapper({}),
            max_detections=1000,
        ).infer(request, deadline=time.monotonic() + 5)


def running_server(tmp_path: Path, service=None, config=None):
    config = config or settings(tmp_path)
    server = InferenceServer(("127.0.0.1", 0), InferenceHandler)
    server.settings = config
    server.inference_service = service or InferenceService(
        MockInferenceBackend(), lambda _: png_bytes()
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_inference_server_waits_for_request_threads_to_finish() -> None:
    assert InferenceServer.daemon_threads is False


@pytest.mark.parametrize(
    ("error_name", "expected_status", "expected_code"),
    [
        ("InferenceResultLimitError", 422, "detection_limit_exceeded"),
        ("InferenceTimeoutError", 504, "inference_timeout"),
    ],
)
def test_http_maps_bounded_inference_failures(
    tmp_path: Path, error_name: str, expected_status: int, expected_code: str
) -> None:
    from app import inference as inference_module

    error_type = getattr(inference_module, error_name)

    class FailingService:
        def infer(self, request, *, deadline):
            del request, deadline
            raise error_type("bounded inference failure")

    server, thread = running_server(tmp_path, FailingService())
    try:
        status, body = post(
            server,
            {
                "file_id": "image-1",
                "media_type": "image",
                "image_urls": ["https://example.com/image.jpg"],
            },
            {"X-Internal-Api-Key": "test-secret"},
        )
        assert status == expected_status
        assert body == {"error": expected_code, "detail": "bounded inference failure"}
    finally:
        server.shutdown()
        thread.join(timeout=2)


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


def get(server, path):
    with urllib.request.urlopen(
        f"http://127.0.0.1:{server.server_port}{path}"
    ) as response:
        return response.status, json.loads(response.read())


def test_http_fails_closed_without_server_key_and_local_switch_is_explicit(
    tmp_path: Path,
) -> None:
    closed = settings(tmp_path, api_key=None)
    server, thread = running_server(tmp_path, config=closed)
    try:
        status, body = post(
            server,
            {
                "file_id": "image-closed",
                "media_type": "image",
                "image_urls": ["https://example.com/image.jpg"],
            },
        )
        assert status == 503
        assert body == {"error": "internal_auth_not_configured"}
        assert get(server, "/health")[0] == 200
        assert get(server, "/ready")[0] == 200
    finally:
        server.shutdown()
        thread.join(timeout=2)

    local_only = replace(closed, allow_unauthenticated_inference=True)
    server, thread = running_server(tmp_path, config=local_only)
    try:
        status, body = post(
            server,
            {
                "file_id": "image-local",
                "media_type": "image",
                "image_urls": ["https://example.com/image.jpg"],
            },
        )
        assert status == 200
        assert body["model_version"] == "mock-v1"
    finally:
        server.shutdown()
        thread.join(timeout=2)


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
            {"X-Internal-Api-Key": "wrong-secret"},
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
