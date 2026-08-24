from __future__ import annotations

import io
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from PIL import Image, UnidentifiedImageError

from .backends.base import InferenceBackend, Prediction
from .schemas import InferenceRequest

LOGGER = logging.getLogger("pacific_bioarchive_ml.inference")


class InferenceInputError(ValueError):
    """Raised when a source cannot be decoded as a supported image."""


class InferenceTimeoutError(TimeoutError):
    """Raised when a request exceeds the configured inference budget."""


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
        image = Image.open(io.BytesIO(raw))
        image.load()
        return image.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise InferenceInputError("source is not a valid supported image") from exc


class InferenceService:
    def __init__(
        self,
        backend: InferenceBackend,
        fetch_url: Callable[[str], bytes],
    ) -> None:
        self.backend = backend
        self.fetch_url = fetch_url

    def infer(self, request: InferenceRequest) -> InferenceResult:
        sources = [self.fetch_url(url) for url in request.image_urls]

        tags: Counter[str] = Counter()
        detections: list[dict[str, float | str]] = []
        for index, raw in enumerate(sources):
            image = _open_image(raw)
            predictions = self.backend.predict_image(image)
            if not predictions:
                LOGGER.info("no_prediction file_id=%s image_index=%d", request.file_id, index)
                continue
            for prediction in predictions:
                tags[prediction.label] += 1
                detections.append(
                    {
                        "species": prediction.label,
                        "confidence": round(prediction.confidence, 6),
                    }
                )

        return InferenceResult(
            model_version=self.backend.model_version,
            tags=dict(sorted(tags.items())),
            detections=detections,
        )
