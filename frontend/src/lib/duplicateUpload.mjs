import { isDuplicateFileError } from "../api/apiClient.js";

function validTags(tags) {
  if (!tags || typeof tags !== "object" || Array.isArray(tags)) return null;
  const entries = Object.entries(tags);
  if (entries.length > 64) return null;
  for (const [species, count] of entries) {
    if (!species.trim() || species !== species.trim()) return null;
    if (!Number.isSafeInteger(count) || count <= 0) return null;
  }
  return Object.fromEntries(entries.sort(([left], [right]) => left.localeCompare(right)));
}

export function duplicateFailureForError(error) {
  if (!isDuplicateFileError(error)) {
    return { duplicate: null, message: error?.message || "Upload failed." };
  }
  const fileId = error.payload?.existing_file_id;
  const tags = validTags(error.payload?.tags);
  if (
    typeof fileId !== "string"
    || !fileId
    || fileId !== fileId.trim()
    || tags === null
  ) {
    return { duplicate: null, message: "Duplicate file details are unavailable." };
  }
  return { duplicate: { fileId, tags }, message: null };
}

export function duplicateCardModel(duplicate, fileType = "") {
  const tagRows = Object.entries(duplicate.tags).map(([species, count]) => ({
    species,
    count,
    label: `${species} × ${count}`,
  }));
  return {
    heading: fileType.startsWith("image/")
      ? "This image already exists"
      : "This file already exists",
    guidance: "No new file was uploaded.",
    fileId: duplicate.fileId,
    tagRows,
    exploreActions: tagRows.map(({ species }) => species),
    emptyTags: tagRows.length === 0,
  };
}

export async function copyDuplicateFileId(fileId, clipboard) {
  if (typeof clipboard?.writeText !== "function") {
    throw new Error("Clipboard access is unavailable.");
  }
  await clipboard.writeText(fileId);
}

export function createExploreSpeciesRequest(currentId, sessionKey, species) {
  return {
    id: currentId + 1,
    sessionKey,
    species: species.trim().toLowerCase(),
  };
}

export function consumeExploreSpeciesRequest(request, {
  sessionKey,
  lastRequestId,
  onSpecies,
  onQuery,
  onConsumed,
}) {
  if (
    !request
    || request.sessionKey !== sessionKey
    || !Number.isInteger(request.id)
    || request.id === lastRequestId
    || typeof request.species !== "string"
    || !request.species
  ) return lastRequestId;
  onSpecies(request.species);
  onConsumed(request.id);
  onQuery(request.species);
  return request.id;
}
