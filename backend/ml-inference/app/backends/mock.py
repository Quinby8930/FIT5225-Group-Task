from __future__ import annotations

from PIL import Image

from .base import Prediction


class MockInferenceBackend:
    """Deterministic backend for API tests and local contract development."""

    model_version = "mock-v1"

    def predict_image(self, image: Image.Image) -> list[Prediction]:
        del image
        return [Prediction(label="unknown", confidence=0.0)]

