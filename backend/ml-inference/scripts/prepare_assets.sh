#!/usr/bin/env sh
set -eu

ARCHIVE="${1:-$HOME/Downloads/PacificBioArchive.zip}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

if [ ! -f "$ARCHIVE" ]; then
  echo "Archive not found: $ARCHIVE" >&2
  exit 1
fi

mkdir -p "$ROOT/models"
unzip -p "$ARCHIVE" model.pt > "$ROOT/models/model.pt"
unzip -p "$ARCHIVE" mdv5a.pt > "$ROOT/models/mdv5a.pt"
printf 'Copied model assets into %s/models\n' "$ROOT"

