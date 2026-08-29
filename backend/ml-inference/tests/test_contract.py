from __future__ import annotations

import http.client
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


def test_source_host_config_defaults_to_aws_s3_and_accepts_an_override(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ML_SERVICE_ROOT", str(tmp_path))
    monkeypatch.delenv("ALLOWED_SOURCE_HOSTS", raising=False)

    assert Settings.from_env().allowed_source_hosts == (
        "s3.amazonaws.com",
        "s3.ap-southeast-2.amazonaws.com",
    )

    monkeypatch.setenv(
        "ALLOWED_SOURCE_HOSTS",
        "assets.example.com, images.example.net ,assets.example.com",
    )
    assert Settings.from_env().allowed_source_hosts == (
        "assets.example.com",
        "images.example.net",
    )


def test_image_pixel_limit_defaults_to_40_million_and_is_configurable(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ML_SERVICE_ROOT", str(tmp_path))
    monkeypatch.delenv("MAX_IMAGE_PIXELS", raising=False)

    assert Settings.from_env().max_image_pixels == 40_000_000

    monkeypatch.setenv("MAX_IMAGE_PIXELS", "123456")
    assert Settings.from_env().max_image_pixels == 123_456

    manifest = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "s.yaml").read_text(encoding="utf-8")
    )
    environment = manifest["resources"]["ml_inference"]["props"][
        "environmentVariables"
    ]
    assert environment["MAX_IMAGE_PIXELS"] == "40000000"


def test_production_manifest_explicitly_disables_unauthenticated_inference() -> None:
    manifest = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "s.yaml").read_text(encoding="utf-8")
    )
    environment = manifest["resources"]["ml_inference"]["props"][
        "environmentVariables"
    ]
    assert environment["ALLOW_UNAUTHENTICATED_INFERENCE"] == "false"


def test_production_manifest_takes_the_image_from_deployment_environment() -> None:
    manifest = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "s.yaml").read_text(encoding="utf-8")
    )
    environment = manifest["resources"]["ml_inference"]["props"][
        "environmentVariables"
    ]

    assert manifest["vars"]["image"] == "${env(ML_IMAGE)}"
    assert environment["ALLOWED_SOURCE_HOSTS"] == (
        "s3.amazonaws.com,s3.ap-southeast-2.amazonaws.com"
    )


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


def test_megadetector_without_an_animal_crop_returns_no_predictions() -> None:
    from app.backends.speciesnet import SpeciesNetBackend

    class NoAnimalDetector:
        def generate_detections_one_image(self, image, **kwargs):
            del image, kwargs
            return {"detections": []}

    backend = SpeciesNetBackend.__new__(SpeciesNetBackend)
    backend._inference_lock = threading.RLock()
    backend.detector = NoAnimalDetector()
    backend.confidence_threshold = 0.05
    backend._classify = lambda image: pytest.fail(
        "full-image classification must not run when MegaDetector finds no animal"
    )

    with Image.new("RGB", (4, 4)) as image:
        assert backend.predict_image(image) == []


def test_explicit_full_image_mode_classifies_the_whole_image() -> None:
    from app.backends.speciesnet import SpeciesNetBackend

    expected = [Prediction("Vombatus_ursinus", 0.91)]
    classified_sizes = []
    backend = SpeciesNetBackend.__new__(SpeciesNetBackend)
    backend._inference_lock = threading.RLock()
    backend.detector = None

    def classify(image):
        classified_sizes.append(image.size)
        return expected

    backend._classify = classify

    with Image.new("RGB", (7, 5)) as image:
        assert backend.predict_image(image) == expected

    assert classified_sizes == [(7, 5)]


def test_megadetector_failure_propagates_without_full_image_fallback() -> None:
    from app.backends.speciesnet import SpeciesNetBackend

    class FailingDetector:
        def generate_detections_one_image(self, image, **kwargs):
            del image, kwargs
            return {"failure": "detector unavailable"}

    backend = SpeciesNetBackend.__new__(SpeciesNetBackend)
    backend._inference_lock = threading.RLock()
    backend.detector = FailingDetector()
    backend.confidence_threshold = 0.05
    backend._classify = lambda image: pytest.fail(
        "detector failure must not fall back to full-image classification"
    )

    with Image.new("RGB", (4, 4)) as image:
        with pytest.raises(RuntimeError, match="detector unavailable"):
            backend.predict_image(image)


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


def test_inference_request_rejects_plain_http_source_urls() -> None:
    with pytest.raises(RequestValidationError, match="HTTPS URL"):
        parse_inference_request(
            {
                "file_id": "image-http",
                "media_type": "image",
                "image_urls": ["http://bucket.s3.amazonaws.com/image.jpg"],
            },
            max_source_urls=3,
        )


@pytest.mark.parametrize(
    "source_url",
    [
        "https://example.com/image.jpg",
        "https://attacker:secret@bucket.s3.ap-southeast-2.amazonaws.com/image.jpg",
        "https://bucket.s3.ap-southeast-2.amazonaws.com:8443/image.jpg",
    ],
)
def test_remote_fetcher_rejects_unsafe_sources_before_network_access(
    monkeypatch, tmp_path: Path, source_url: str
) -> None:
    from app.inference import InferenceInputError
    from app.main import build_fetcher

    def unexpected_network_access(*args, **kwargs):
        del args, kwargs
        pytest.fail("unsafe source reached the network client")

    class RejectingOpener:
        open = unexpected_network_access

    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *handlers: RejectingOpener()
    )

    with pytest.raises(InferenceInputError, match="source URL is not permitted"):
        build_fetcher(settings(tmp_path))(source_url)


@pytest.mark.parametrize(
    ("network_error", "expected_error_name"),
    [
        (urllib.error.URLError("DNS unavailable"), "SourceUnavailableError"),
        (
            urllib.error.HTTPError(
                "https://bucket.s3.ap-southeast-2.amazonaws.com/image.jpg",
                403,
                "expired signature",
                {},
                None,
            ),
            "SourceUnavailableError",
        ),
        (
            urllib.error.HTTPError(
                "https://bucket.s3.ap-southeast-2.amazonaws.com/image.jpg",
                429,
                "rate limited",
                {},
                None,
            ),
            "SourceUnavailableError",
        ),
        (
            urllib.error.HTTPError(
                "https://bucket.s3.ap-southeast-2.amazonaws.com/image.jpg",
                503,
                "upstream unavailable",
                {},
                None,
            ),
            "SourceUnavailableError",
        ),
        (TimeoutError("source timed out"), "SourceTimeoutError"),
        (ConnectionResetError("connection reset"), "SourceUnavailableError"),
        (http.client.IncompleteRead(b"partial", 100), "SourceUnavailableError"),
    ],
)
def test_remote_fetcher_maps_transient_download_failures_to_retryable_errors(
    monkeypatch,
    tmp_path: Path,
    network_error: Exception,
    expected_error_name: str,
) -> None:
    from app import inference as inference_module
    from app.main import build_fetcher

    class FailingOpener:
        def open(self, request, timeout):
            del request, timeout
            raise network_error

    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *handlers: FailingOpener()
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("safe opener was bypassed"),
    )

    expected_error = getattr(inference_module, expected_error_name)
    with pytest.raises(expected_error):
        build_fetcher(settings(tmp_path))(
            "https://bucket.s3.ap-southeast-2.amazonaws.com/image.jpg"
        )


def test_remote_fetcher_maps_response_read_disconnect_to_retryable_error(
    monkeypatch, tmp_path: Path
) -> None:
    from app.inference import SourceUnavailableError
    from app.main import build_fetcher

    class DisconnectingResponse:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            del args

        def read(self, size):
            del size
            raise ConnectionResetError("connection reset while reading")

    class Opener:
        def open(self, request, timeout):
            del request, timeout
            return DisconnectingResponse()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())

    with pytest.raises(SourceUnavailableError):
        build_fetcher(settings(tmp_path))(
            "https://bucket.s3.ap-southeast-2.amazonaws.com/image.jpg"
        )


def test_remote_fetcher_caps_socket_timeout_to_remaining_request_budget(
    monkeypatch, tmp_path: Path
) -> None:
    from app import main as main_module
    from app.main import build_fetcher

    observed_timeouts = []
    image_bytes = png_bytes()

    class Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            del args

        def read(self, size):
            assert size == settings(tmp_path).max_image_bytes + 1
            return image_bytes

    class Opener:
        def open(self, request, timeout):
            del request
            observed_timeouts.append(timeout)
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())
    monkeypatch.setattr(main_module.time, "monotonic", lambda: 100.0)

    result = build_fetcher(settings(tmp_path))(
        "https://bucket.s3.ap-southeast-2.amazonaws.com/image.jpg",
        deadline=101.25,
    )

    assert result == image_bytes
    assert observed_timeouts == [1.25]


def test_remote_fetcher_installs_a_no_redirect_policy(
    monkeypatch, tmp_path: Path
) -> None:
    from app.inference import SourceUnavailableError
    from app.main import build_fetcher

    configured_handlers = []

    class RedirectingOpener:
        def open(self, request, timeout):
            del timeout
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "redirect",
                {"Location": "https://example.com/redirected.jpg"},
                None,
            )

    def build_opener(*handlers):
        configured_handlers.extend(handlers)
        return RedirectingOpener()

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("safe opener was bypassed"),
    )

    with pytest.raises(SourceUnavailableError):
        build_fetcher(settings(tmp_path))(
            "https://bucket.s3.ap-southeast-2.amazonaws.com/image.jpg"
        )

    assert configured_handlers
    assert configured_handlers[0].redirect_request(
        None, None, 302, "redirect", {}, "https://example.com/redirected.jpg"
    ) is None


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
        lambda url, *, deadline=None: frames[int(url.rsplit("/", 1)[1]) - 1],
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

    def fetch_url(url, *, deadline=None):
        del deadline
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


def test_inference_passes_its_absolute_deadline_to_the_source_fetcher() -> None:
    from app.species import SpeciesMapper

    observed_deadlines = []

    def fetch_url(url, *, deadline):
        del url
        observed_deadlines.append(deadline)
        return png_bytes()

    request = parse_inference_request(
        {
            "file_id": "image-deadline",
            "media_type": "image",
            "image_urls": ["https://example.com/image"],
        },
        max_source_urls=3,
    )

    deadline = time.monotonic() + 5
    InferenceService(
        MockInferenceBackend(), fetch_url, SpeciesMapper({}), max_detections=1000
    ).infer(request, deadline=deadline)

    assert observed_deadlines == [deadline]


def test_configured_pixel_limit_rejects_an_image_before_load_or_convert(
    monkeypatch, tmp_path: Path
) -> None:
    from app import inference as inference_module
    from app import main as main_module
    from app.inference import InferenceInputError
    from app.main import build_service

    class OversizedHeader:
        size = (10_000, 4_001)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            del args

        def load(self):
            pytest.fail("oversized image pixels must not be decoded")

        def convert(self, mode):
            del mode
            pytest.fail("oversized image pixels must not be converted")

    class Backend:
        model_version = "test-v1"

        def predict_image(self, image):
            del image
            pytest.fail("oversized image must not reach the model")

    monkeypatch.setattr(
        inference_module.Image, "open", lambda source: OversizedHeader()
    )
    monkeypatch.setattr(main_module, "build_backend", lambda config: Backend())
    monkeypatch.setattr(
        main_module,
        "build_fetcher",
        lambda config: (lambda url, *, deadline=None: b"image header"),
    )
    original_pillow_limit = Image.MAX_IMAGE_PIXELS
    request = parse_inference_request(
        {
            "file_id": "oversized-image",
            "media_type": "image",
            "image_urls": ["https://example.com/oversized.jpg"],
        },
        max_source_urls=3,
    )
    config = replace(settings(tmp_path), max_image_pixels=40_000_000)
    config.labels_path.write_text("", encoding="utf-8")

    with pytest.raises(InferenceInputError, match="pixel limit"):
        build_service(config).infer(request)

    assert Image.MAX_IMAGE_PIXELS == original_pillow_limit


def test_pillow_decompression_bomb_error_maps_to_inference_input_error(
    monkeypatch,
) -> None:
    from app import inference as inference_module
    from app.inference import InferenceInputError
    from app.species import SpeciesMapper

    def raise_bomb(source):
        del source
        raise Image.DecompressionBombError("unsafe image dimensions")

    monkeypatch.setattr(inference_module.Image, "open", raise_bomb)
    request = parse_inference_request(
        {
            "file_id": "decompression-bomb",
            "media_type": "image",
            "image_urls": ["https://example.com/bomb.jpg"],
        },
        max_source_urls=3,
    )

    with pytest.raises(InferenceInputError, match="valid supported image"):
        InferenceService(
            MockInferenceBackend(),
            lambda url, *, deadline=None: b"unsafe header",
            SpeciesMapper({}),
        ).infer(request)


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
            lambda _, *, deadline=None: png_bytes(),
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
            lambda _, *, deadline=None: png_bytes(),
            SpeciesMapper({}),
            max_detections=1000,
        ).infer(request, deadline=time.monotonic() + 5)


def running_server(tmp_path: Path, service=None, config=None):
    config = config or settings(tmp_path)
    server = InferenceServer(("127.0.0.1", 0), InferenceHandler)
    server.settings = config
    server.inference_service = service or InferenceService(
        MockInferenceBackend(), lambda _, *, deadline=None: png_bytes()
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
        ("SourceUnavailableError", 503, "source_unavailable"),
        ("SourceTimeoutError", 504, "source_timeout"),
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


@pytest.mark.parametrize(
    ("failure_mode", "expected_detail"),
    [
        ("pixel_limit", "source image exceeds the pixel limit"),
        ("decompression_bomb", "source is not a valid supported image"),
    ],
)
def test_http_maps_pixel_limit_failures_to_invalid_source(
    monkeypatch,
    tmp_path: Path,
    failure_mode: str,
    expected_detail: str,
) -> None:
    from app import inference as inference_module

    class OversizedHeader:
        size = (10_000, 4_001)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            del args

        def load(self):
            pytest.fail("oversized image pixels must not be decoded")

        def convert(self, mode):
            del mode
            pytest.fail("oversized image pixels must not be converted")

    def open_source(source):
        del source
        if failure_mode == "decompression_bomb":
            raise Image.DecompressionBombError("unsafe image dimensions")
        return OversizedHeader()

    monkeypatch.setattr(inference_module.Image, "open", open_source)
    service = InferenceService(
        MockInferenceBackend(),
        lambda url, *, deadline=None: b"image header",
        max_image_pixels=40_000_000,
    )
    server, thread = running_server(tmp_path, service)
    try:
        status, body = post(
            server,
            {
                "file_id": "pixel-limit",
                "media_type": "image",
                "image_urls": ["https://example.com/image.jpg"],
            },
            {"X-Internal-Api-Key": "test-secret"},
        )
        assert status == 422
        assert body == {"error": "invalid_source", "detail": expected_detail}
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
