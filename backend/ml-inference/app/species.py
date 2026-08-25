from __future__ import annotations

from pathlib import Path


class SpeciesMapper:
    """Normalize model scientific labels to the team's short wire tags."""

    def __init__(self, scientific_to_team: dict[str, str]) -> None:
        self._scientific_to_team = {
            scientific.casefold(): team for scientific, team in scientific_to_team.items()
        }

    @classmethod
    def from_file(cls, labels_path: Path) -> "SpeciesMapper":
        scientific_to_team: dict[str, str] = {}
        for line in labels_path.read_text(encoding="utf-8").splitlines():
            columns = line.split(";")
            if len(columns) < 7:
                continue
            genus = columns[4].strip()
            species = columns[5].strip()
            common_name = columns[6].strip()
            if not genus or not species or not common_name:
                continue
            scientific_to_team[f"{genus}_{species}"] = common_name.split()[-1].casefold()
        return cls(scientific_to_team)

    def normalize(self, label: str) -> str:
        return self._scientific_to_team.get(label.casefold(), label)
