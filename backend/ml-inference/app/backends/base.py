from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PIL import Image


@dataclass(frozen=True)
class Prediction:
    label: str
    confidence: float


class InferenceBackend(Protocol):
    model_version: str

    def predict_image(self, image: Image.Image) -> list[Prediction]:
        """Return ranked predictions for one image."""

