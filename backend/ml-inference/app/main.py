from __future__ import annotations

import hmac
import http.client
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from PIL import UnidentifiedImageError

from .backends.mock import MockInferenceBackend
from .backends.speciesnet import SpeciesNetBackend
from .config import Settings
from .inference import (
    InferenceInputError,
    InferenceResultLimitError,
    InferenceService,
    InferenceTimeoutError,
    SourceTimeoutError,
    SourceUnavailableError,
)
from .schemas import RequestValidationError, parse_inference_request
from .species import SpeciesMapper

LOGGER = logging.getLogger("pacific_bioarchive_ml")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def build_backend(settings: Settings):
    backend_name = os.getenv("INFERENCE_BACKEND", "speciesnet").lower()
    if backend_name == "mock":
        return MockInferenceBackend()
    if backend_name == "speciesnet":
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/pacific-bioarchive-mpl")
        os.environ.setdefault("YOLOV5_CONFIG_DIR", "/tmp/pacific-bioarchive-yolo")
        os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)
        os.makedirs(os.environ["YOLOV5_CONFIG_DIR"], exist_ok=True)
        return SpeciesNetBackend(
            model_path=settings.model_path,
            detector_model_path=settings.detector_model_path,
            labels_path=settings.labels_path,
            model_version=settings.model_version,
            confidence_threshold=settings.confidence_threshold,
        )
    raise ValueError("INFERENCE_BACKEND must be either 'speciesnet' or 'mock'")


def build_fetcher(settings: Settings):
    opener = urllib.request.build_opener(_NoRedirectHandler())

    def validate_source_url(url: str) -> str:
        parsed = urlparse(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise InferenceInputError("source URL is not permitted") from exc
        hostname = (parsed.hostname or "").lower().rstrip(".")
        allowed_host = any(
            hostname == allowed or hostname.endswith(f".{allowed}")
            for allowed in settings.allowed_source_hosts
        )
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or not allowed_host
        ):
            raise InferenceInputError("source URL is not permitted")
        return url

    def fetch_url(url: str, *, deadline: float | None = None) -> bytes:
        if not settings.allow_remote_urls:
            raise InferenceInputError("remote URL input is disabled")
        url = validate_source_url(url)
        network_timeout = float(settings.remote_url_timeout_seconds)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceTimeoutError("source URL download timed out")
            network_timeout = min(network_timeout, remaining)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "PacificBioArchive-ML/1.0"},
            method="GET",
        )
        try:
            with opener.open(
                request, timeout=network_timeout
            ) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > settings.max_image_bytes:
                    raise InferenceInputError("remote image exceeds the size limit")
                data = response.read(settings.max_image_bytes + 1)
        except urllib.error.HTTPError as exc:
            if (
                300 <= exc.code < 400
                or exc.code in {403, 408, 425, 429}
                or exc.code >= 500
            ):
                raise SourceUnavailableError(
                    "source URL is temporarily unavailable"
                ) from exc
            raise InferenceInputError("source URL could not be downloaded") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise SourceTimeoutError("source URL download timed out") from exc
            raise SourceUnavailableError(
                "source URL is temporarily unavailable"
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise SourceTimeoutError("source URL download timed out") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise SourceUnavailableError(
                "source URL is temporarily unavailable"
            ) from exc
        except ValueError as exc:
            raise InferenceInputError("source URL returned invalid metadata") from exc
        if len(data) > settings.max_image_bytes:
            raise InferenceInputError("remote image exceeds the size limit")
        return data

    return fetch_url


def build_service(settings: Settings) -> InferenceService:
    return InferenceService(
        build_backend(settings),
        build_fetcher(settings),
        SpeciesMapper.from_file(settings.labels_path),
        max_detections=settings.max_detections,
    )


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


class InferenceHandler(BaseHTTPRequestHandler):
    server_version = "PacificBioArchiveML/1.0"
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(int(os.getenv("SERVER_SOCKET_TIMEOUT_SECONDS", "900")))

    @property
    def settings(self) -> Settings:
        return self.server.settings  # type: ignore[attr-defined]

    @property
    def inference_service(self) -> InferenceService:
        return self.server.inference_service  # type: ignore[attr-defined]

    def _send_json(self, status: int, payload: Any) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorization_error(self) -> HTTPStatus | None:
        expected = self.settings.internal_api_key
        if expected is None:
            if self.settings.allow_unauthenticated_inference:
                return None
            return HTTPStatus.SERVICE_UNAVAILABLE
        provided = self.headers.get("X-Internal-Api-Key", "")
        if hmac.compare_digest(provided, expected):
            return None
        return HTTPStatus.UNAUTHORIZED

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": "pacific-bioarchive-ml",
                    "model_version": self.inference_service.backend.model_version,
                },
            )
            return
        if self.path == "/ready":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ready",
                    "model_version": self.inference_service.backend.model_version,
                },
            )
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/infer":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        auth_error = self._authorization_error()
        if auth_error is not None:
            error_code = (
                "internal_auth_not_configured"
                if auth_error == HTTPStatus.SERVICE_UNAVAILABLE
                else "unauthorized"
            )
            self._send_json(auth_error, {"error": error_code})
            return

        content_length = self.headers.get("Content-Length")
        try:
            length = int(content_length or "0")
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
            return
        if length <= 0:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "empty_request"})
            return
        if length > self.settings.max_request_bytes:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
            return

        started = time.perf_counter()
        try:
            payload = json.loads(self.rfile.read(length))
            request = parse_inference_request(payload, self.settings.max_source_urls)
            deadline = time.monotonic() + self.settings.request_timeout_seconds
            result = self.inference_service.infer(
                request,
                deadline=deadline,
            )
            response = result.as_dict()
            status = HTTPStatus.OK
        except json.JSONDecodeError:
            response = {"error": "invalid_json"}
            status = HTTPStatus.BAD_REQUEST
        except RequestValidationError as exc:
            response = {"error": "validation_error", "detail": str(exc)}
            status = HTTPStatus.UNPROCESSABLE_ENTITY
        except (InferenceInputError, UnidentifiedImageError) as exc:
            response = {"error": "invalid_source", "detail": str(exc)}
            status = HTTPStatus.UNPROCESSABLE_ENTITY
        except SourceUnavailableError as exc:
            response = {"error": "source_unavailable", "detail": str(exc)}
            status = HTTPStatus.SERVICE_UNAVAILABLE
        except SourceTimeoutError as exc:
            response = {"error": "source_timeout", "detail": str(exc)}
            status = HTTPStatus.GATEWAY_TIMEOUT
        except InferenceResultLimitError as exc:
            response = {"error": "detection_limit_exceeded", "detail": str(exc)}
            status = HTTPStatus.UNPROCESSABLE_ENTITY
        except InferenceTimeoutError as exc:
            response = {"error": "inference_timeout", "detail": str(exc)}
            status = HTTPStatus.GATEWAY_TIMEOUT
        except Exception:
            LOGGER.exception("inference_failed path=%s", self.path)
            response = {"error": "inference_failed"}
            status = HTTPStatus.BAD_GATEWAY
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        LOGGER.info(
            "request path=%s status=%d elapsed_ms=%s",
            self.path,
            status,
            elapsed_ms,
        )
        self._send_json(status, response)

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("http " + format, *args)


class InferenceServer(ThreadingHTTPServer):
    daemon_threads = False


def create_server(
    settings: Settings | None = None,
    host: str | None = None,
    port: int | None = None,
) -> ThreadingHTTPServer:
    configure_logging()
    settings = settings or Settings.from_env()
    service = build_service(settings)
    host = host or os.getenv("HOST", "0.0.0.0")
    port = port if port is not None else int(os.getenv("PORT", "9000"))
    server = InferenceServer((host, port), InferenceHandler)
    server.settings = settings  # type: ignore[attr-defined]
    server.inference_service = service  # type: ignore[attr-defined]
    LOGGER.info(
        "service_started host=%s port=%s backend=%s model_version=%s",
        host,
        port,
        type(service.backend).__name__,
        service.backend.model_version,
    )
    return server


def main() -> None:
    server = create_server()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("service_stopping")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
