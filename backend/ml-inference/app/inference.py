from __future__ import annotations

import io
import logging
import time
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from PIL import Image, UnidentifiedImageError

from .backends.base import InferenceBackend, Prediction
from .schemas import InferenceRequest
from .species import SpeciesMapper

LOGGER = logging.getLogger("pacific_bioarchive_ml.inference")


class InferenceInputError(ValueError):
    """Raised when a source cannot be decoded as a supported image."""


class SourceUnavailableError(ConnectionError):
    """Raised when a remote source cannot currently be downloaded."""


class SourceTimeoutError(TimeoutError):
    """Raised when downloading a remote source exceeds its time budget."""


class InferenceTimeoutError(TimeoutError):
    """Raised when a request exceeds the configured inference budget."""


class InferenceResultLimitError(ValueError):
    """Raised when a response would exceed the configured detection limit."""


@dataclass(frozen=True)
class InferenceResult:
    tags: dict[str, int]
    detections: list[dict[str, float | str]]
    model_version: str

    def as_dict(self) -> dict[str, object]:
        return {
            "tags": self.tags,
            "detections": self.detections,
            "model_version": self.model_version,
        }


def _open_image(raw: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            return image.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise InferenceInputError("source is not a valid supported image") from exc


class InferenceService:
    def __init__(
        self,
        backend: InferenceBackend,
        fetch_url: Callable[..., bytes],
        species_mapper: SpeciesMapper | None = None,
        *,
        max_detections: int = 1000,
    ) -> None:
        self.backend = backend
        self.fetch_url = fetch_url
        self.species_mapper = species_mapper or SpeciesMapper({})
        self.max_detections = max_detections

    @staticmethod
    def _check_deadline(deadline: float | None) -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise InferenceTimeoutError("inference exceeded its deadline")

    @classmethod
    def _within_deadline(cls, operation, deadline: float | None):
        cls._check_deadline(deadline)
        result = operation()
        cls._check_deadline(deadline)
        return result

    def infer(
        self, request: InferenceRequest, *, deadline: float | None = None
    ) -> InferenceResult:
        tags: Counter[str] = Counter()
        detections: list[dict[str, float | str]] = []
        for index, url in enumerate(request.image_urls):
            raw = self._within_deadline(
                lambda: self.fetch_url(url, deadline=deadline), deadline
            )
            image = self._within_deadline(lambda: _open_image(raw), deadline)
            try:
                predictions = self._within_deadline(
                    lambda: self.backend.predict_image(image), deadline
                )
            finally:
                image.close()
            if not predictions:
                LOGGER.info("no_prediction file_id=%s image_index=%d", request.file_id, index)
                continue
            for prediction in predictions:
                if len(detections) >= self.max_detections:
                    raise InferenceResultLimitError(
                        f"inference cannot return more than {self.max_detections} detections"
                    )
                species = self.species_mapper.normalize(prediction.label)
                tags[species] += 1
                detections.append(
                    {
                        "species": species,
                        "confidence": round(prediction.confidence, 6),
                    }
                )

        return InferenceResult(
            model_version=self.backend.model_version,
            tags=dict(sorted(tags.items())),
            detections=detections,
        )
