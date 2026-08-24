"""Pluggable tag detection for the "find by uploaded file" query.

Member C owns the real ML pipeline (MegaDetector + SpeciesNet). To let the
database/query work proceed in parallel, we define the interface here and ship a
stub. When C's module is ready, replace `StubTagDetector` with a thin adapter
that calls C's function — the query endpoint code does not change.

The stub can be driven by a JSON mapping (filename -> {species: count}) so the
demo shows realistic, deterministic results for the test images.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path


class TagDetector(ABC):
    @abstractmethod
    def detect(self, file_name: str, content: bytes) -> dict[str, int]:
        """Return ``{species_common_name: count}`` for one uploaded file."""


class StubTagDetector(TagDetector):
    def __init__(
        self,
        mapping: dict[str, dict[str, int]] | None = None,
        default: dict[str, int] | None = None,
    ) -> None:
        # `mapping` keys are matched against the *basename* of the uploaded file.
        self._mapping = mapping or {}
        self._default = default or {"dingo": 1}

    @classmethod
    def from_json(cls, path: str | Path) -> "StubTagDetector":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(mapping=data.get("mapping"), default=data.get("default"))

    def detect(self, file_name: str, content: bytes) -> dict[str, int]:
        basename = Path(file_name).name
        if basename in self._mapping:
            return dict(self._mapping[basename])
        return dict(self._default)
