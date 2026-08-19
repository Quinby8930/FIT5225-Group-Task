"""Member C — how to wrap MegaDetector + SpeciesNet as a `TagDetector`.

This is the ONLY integration point Member C needs to satisfy for Member D.
Copy this file, implement `detect`, and set it as the detector in `app/main.py`
(replace the `StubTagDetector`). The query endpoint code does NOT change.

The returned dict keys MUST be the team short species names from `labels.txt`
(via `SpeciesMapper`, i.e. the last word of the common name — `wombat`,
`magpie`, `dingo`) — do NOT return scientific names like `Vombatus_ursinus`.
"""

from __future__ import annotations

from app.species import get_mapper
from app.tag_detector import TagDetector


class SpeciesNetDetector(TagDetector):
    """Real detector: MegaDetector finds animals, SpeciesNet classifies them."""

    def __init__(self, md_weights: str = "./mdv5a.pt", species_weights: str = "./model.pt"):
        self._mapper = get_mapper()
        # TODO(C): load your MegaDetector and fine-tuned SpeciesNet models here
        # (see the starter package batch.py). Keep paths in config/env, so a
        # new model version is a config change, not a code change (spec 4.1).
        self._md_weights = md_weights
        self._species_weights = species_weights
        self._classes = self._load_classes()  # the 46 `Genus_species` labels

    def _load_classes(self) -> list[str]:
        # TODO(C): return the `classes` list from batch.py.
        raise NotImplementedError

    def detect(self, file_name: str, content: bytes) -> dict[str, int]:
        # 1. Write `content` to a temp file (or run inference on bytes directly).
        # 2. MegaDetector -> bounding boxes of animals (category "1").
        # 3. Crop each box, run SpeciesNet -> one class per animal.
        # 4. Count occurrences and map scientific -> common name.
        raw_counts: dict[str, int] = self._run_pipeline(content)  # {"Vombatus_ursinus": 2}
        return {self._mapper.common_name(k): v for k, v in raw_counts.items()}

    def _run_pipeline(self, content: bytes) -> dict[str, int]:
        # TODO(C): actual MegaDetector + SpeciesNet inference.
        raise NotImplementedError
