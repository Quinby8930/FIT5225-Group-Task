from __future__ import annotations

import importlib
import os
import threading
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .base import Prediction


DEFAULT_CLASSES = (
    "Alectura_lathami",
    "Antechinus_agilis",
    "Bos_taurus",
    "Burhinus_grallarius",
    "Canis_familiaris",
    "Chalcophaps_longirostris",
    "Colluricincla_harmonica",
    "Corcorax_melanorhamphos",
    "Dacelo_novaeguineae",
    "Dama_dama",
    "Eopsaltria_australis",
    "Felis_catus",
    "Geopelia_humeralis",
    "Gymnorhina_tibicen",
    "Homo_sapiens",
    "Isoodon_macrourus",
    "Lepus_europaeus",
    "Macropus_giganteus",
    "Menura_novaehollandiae",
    "Mus_musculus",
    "Oryctolagus_cuniculus",
    "Perameles_nasuta",
    "Pitta_versicolor",
    "Rattus",
    "Rattus_fuscipes",
    "Rattus_rattus",
    "Strepera_graculina",
    "Sus_scrofa",
    "Tachyglossus_aculeatus",
    "Thylogale_stigmatica",
    "Trichosurus_caninus",
    "Trichosurus_cunninghami",
    "Trichosurus_vulpecula",
    "Varanus_varius",
    "Vombatus_ursinus",
    "Vulpes_vulpes",
    "Wallabia_bicolor",
    "Canis_dingo",
    "Capra_hircus",
    "Casuarius_casuarius",
    "Heteromyias_cinereifrons",
    "Hypsiprymnodon_moschatus",
    "Megapodius_reinwardt",
    "Notamacropus_rufogriseus",
    "Orthonyx_spaldingii",
    "Uromys_caudimaculatus",
)


def _load_classes(path: Path) -> tuple[str, ...]:
    if not path.exists():
        return DEFAULT_CLASSES
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = [item.strip() for item in line.split(";")]
        if len(parts) >= 5 and parts[4]:
            genus = parts[4].capitalize()
            species = f"_{parts[5]}" if len(parts) >= 6 and parts[5] else ""
            names.append(f"{genus}{species}")
    return tuple(names) or DEFAULT_CLASSES


def _import_torch() -> Any:
    try:
        return importlib.import_module("torch")
    except ImportError as exc:
        raise RuntimeError(
            "Torch is required for the SpeciesNet backend. "
            "Install the production requirements or set INFERENCE_BACKEND=mock."
        ) from exc


class SpeciesNetBackend:
    """Adapter for the supplied fine-tuned PyTorch classifier.

    The model is loaded from a configurable path, so changing MODEL_PATH or
    MODEL_VERSION is enough to roll out a new model without changing callers.
    """

    def __init__(
        self,
        model_path: Path,
        detector_model_path: Path,
        labels_path: Path,
        model_version: str,
        confidence_threshold: float,
    ) -> None:
        self._torch = _import_torch()
        self.model_version = model_version
        self.confidence_threshold = confidence_threshold
        self.classes = _load_classes(labels_path)
        self.detector_mode = os.getenv("ANIMAL_DETECTOR", "megadetector").lower()
        self.detector = None
        self._inference_lock = threading.RLock()

        device_name = os.getenv("TORCH_DEVICE", "")
        if device_name:
            self.device = device_name
        elif self._torch.cuda.is_available():
            self.device = "cuda"
        elif getattr(self._torch.backends, "mps", None) and self._torch.backends.mps.is_available():
            self.device = "mps"
        else:
            self.device = "cpu"

        if not model_path.exists():
            raise FileNotFoundError(f"model file does not exist: {model_path}")
        self.model = self._torch.load(
            model_path, map_location=self.device, weights_only=False
        )
        self.model.eval()
        self.model.to(self.device)

        if self.detector_mode == "megadetector":
            if not detector_model_path.exists():
                raise FileNotFoundError(
                    f"MegaDetector model does not exist: {detector_model_path}"
                )
            try:
                detector_module = importlib.import_module(
                    "megadetector.detection.run_detector"
                )
            except ImportError as exc:
                raise RuntimeError(
                    "megadetector is required when ANIMAL_DETECTOR=megadetector"
                ) from exc
            self.detector = detector_module.load_detector(
                str(detector_model_path),
                detector_options={"force_cpu": self.device == "cpu"},
                verbose=False,
            )
        elif self.detector_mode != "full_image":
            raise ValueError(
                "ANIMAL_DETECTOR must be 'megadetector' or 'full_image'"
            )

        transforms = importlib.import_module("torchvision.transforms")
        self.transform = transforms.Compose(
            [
                transforms.Resize((480, 480)),
                transforms.ToTensor(),
            ]
        )

    def predict_image(self, image: Image.Image) -> list[Prediction]:
        with self._inference_lock:
            if self.detector is not None:
                crops = self._detect_animal_crops(image)
                if crops:
                    predictions: list[Prediction] = []
                    for crop in crops:
                        ranked = self._classify(crop)
                        if ranked:
                            predictions.append(ranked[0])
                    return predictions

            ranked = self._classify(image)
            return ranked[:1]

    def _classify(self, image: Image.Image) -> list[Prediction]:
        with self._torch.no_grad():
            tensor = self.transform(image.convert("RGB")).unsqueeze(0)
            tensor = tensor.permute(0, 2, 3, 1).to(self.device)
            logits = self.model(tensor)
            probabilities = self._torch.softmax(logits, dim=1)[0].detach().cpu().numpy()

        ranked = np.argsort(probabilities)[::-1]
        return [
            Prediction(
                label=self.classes[int(index)]
                if int(index) < len(self.classes)
                else f"class_{int(index)}",
                confidence=float(probabilities[int(index)]),
            )
            for index in ranked
            if float(probabilities[int(index)]) >= self.confidence_threshold
        ]

    def _detect_animal_crops(self, image: Image.Image) -> list[Image.Image]:
        result = self.detector.generate_detections_one_image(
            image.convert("RGB"),
            image_id="in-memory",
            detection_threshold=self.confidence_threshold,
        )
        if result.get("failure"):
            raise RuntimeError(result["failure"])
        detections = result.get("detections", [])
        if not detections:
            return []
        width, height = image.size
        crops: list[Image.Image] = []
        for detection in detections:
            if detection.get("category") != "1":
                continue
            confidence = float(detection.get("conf", 0.0))
            if confidence < self.confidence_threshold:
                continue
            x, y, w, h = detection["bbox"]
            left = max(0, int(x * width))
            top = max(0, int(y * height))
            right = min(width, int((x + w) * width))
            bottom = min(height, int((y + h) * height))
            if right > left and bottom > top:
                crops.append(image.crop((left, top, right, bottom)))
        return crops
