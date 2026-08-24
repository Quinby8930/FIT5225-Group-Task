"""Authoritative species-name mapping: scientific -> team tag name.

The fine-tuned SpeciesNet model outputs 46 classes in ``Genus_species`` form
(e.g. ``Vombatus_ursinus``, ``Gymnorhina_tibicen``). The database stores tags
using the **team's short species name**, which is the last word of the
``labels.txt`` common name (e.g. ``common wombat`` -> ``wombat``). This module
is the single source of truth for that conversion, shared by Member C (ML) and
Member D (database), so the two can never disagree on a tag string.

Short-name rule (agreed with the team, see INTEGRATION.md §2):

- ``vombatus_ursinus``   -> ``"wombat"``   (from "common wombat")
- ``gymnorhina_tibicen`` -> ``"magpie"``   (from "australian magpie")
- ``canis_familiaris`` AND ``canis_dingo`` both -> ``"dingo"``
- Single-word names (``cattle``, ``human``, ``dingo``) are unchanged.
- Matching is case-insensitive (labels contain ``Homo_sapiens`` etc.).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def _load_mapping(labels_path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in labels_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split(";")
        if len(parts) < 7:
            continue
        # Format: uuid;class;order;family;genus;species;common_name
        genus = parts[4].strip().lower()
        species = parts[5].strip().lower()
        common = parts[6].strip()
        key = f"{genus}_{species}" if species else genus
        full = common or genus  # fall back to genus when common is empty
        mapping[key] = full.split()[-1]  # team short name = last word
    return mapping


class SpeciesMapper:
    def __init__(self, labels_path: str | Path) -> None:
        self._mapping = _load_mapping(Path(labels_path))

    def common_name(self, scientific: str) -> str:
        """Map a ``Genus_species`` (or ``Genus``) class label to its team tag name.

        Case-insensitive. Returns the input unchanged if unknown (so an unknown
        class never crashes the pipeline, but this should not happen for the 46
        supported classes).
        """
        key = scientific.strip().lower().replace(" ", "_")
        return self._mapping.get(key, scientific)

    @lru_cache(maxsize=1)
    def tag_of(self, scientific: str) -> str:  # noqa: D401 (alias kept cached)
        return self.common_name(scientific)


# Convenience default pointing at the bundled labels.txt.
DEFAULT_LABELS = Path(__file__).resolve().parent.parent / "data" / "labels.txt"

_mapper: SpeciesMapper | None = None


def get_mapper() -> SpeciesMapper:
    global _mapper
    if _mapper is None:
        _mapper = SpeciesMapper(DEFAULT_LABELS)
    return _mapper
