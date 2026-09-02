const MEDIA_TYPES = new Set(["image", "video"]);
const MAX_PUBLIC_TAGS = 64;
const MAX_PUBLIC_DETECTIONS = 1000;
const MAX_PUBLIC_TEXT_LENGTH = 128;
const AI_NOTICE = "AI-generated result; it may be incorrect. Archive tags can be corrected by the owner.";

function nonEmptyString(value) {
  return typeof value === "string" && value.trim() ? value : null;
}

function safePublicText(value) {
  const text = nonEmptyString(value)?.trim();
  if (
    !text
    || text.length > MAX_PUBLIC_TEXT_LENGTH
    || [...text].some((character) => {
      const code = character.codePointAt(0);
      return code < 0x20 || code === 0x7f;
    })
  ) return null;
  return text;
}

function safeTags(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const entries = Object.entries(value);
  if (entries.length > MAX_PUBLIC_TAGS) return {};
  return Object.fromEntries(entries
    .filter(([species, count]) => (
      safePublicText(species) === species
      && Number.isSafeInteger(count)
      && count > 0
    ))
    .sort(([left], [right]) => left.localeCompare(right)));
}

function safeDetections(value) {
  if (!Array.isArray(value) || value.length > MAX_PUBLIC_DETECTIONS) return [];
  return value.flatMap((detection) => {
    if (!detection || typeof detection !== "object" || Array.isArray(detection)) return [];
    const species = safePublicText(detection.species);
    const confidence = detection.confidence;
    if (
      !species
      || typeof confidence !== "number"
      || !Number.isFinite(confidence)
      || confidence < 0
      || confidence > 1
    ) return [];
    return [{ species, confidence }];
  });
}

function structuredItem(value) {
  if (!value || typeof value !== "object" || !MEDIA_TYPES.has(value.file_type)) return null;

  const fileId = nonEmptyString(value.file_id);
  const displayKey = nonEmptyString(value.display_key);
  const originalKey = nonEmptyString(value.original_key);
  if (!fileId || !displayKey || !originalKey) return null;

  return {
    identity: fileId,
    file_id: fileId,
    file_type: value.file_type,
    display_key: displayKey,
    original_key: originalKey,
    thumbnail_key: nonEmptyString(value.thumbnail_key),
    can_preview: value.can_preview === true,
    can_manage: value.can_manage === true,
    tags: safeTags(value.tags),
    detections: safeDetections(value.detections),
    model_version: safePublicText(value.model_version) || "",
    legacy: false,
  };
}

export function canRenderRawMediaKey(item) {
  return item?.can_manage === true;
}

export function mediaTechnicalDetails(item) {
  const tagRows = Object.entries(safeTags(item?.tags)).map(([species, count]) => ({
    species,
    count,
    label: `${species} × ${count}`,
  }));
  const detectionRows = safeDetections(item?.detections).map(({ species, confidence }) => ({
    species,
    confidence,
    label: `${species} — model score ${(confidence * 100).toFixed(2)}%`,
  }));
  const modelVersion = safePublicText(item?.model_version);
  const hasAiDetails = detectionRows.length > 0 || modelVersion !== null;
  return {
    tagRows,
    detectionRows,
    modelVersion,
    notice: hasAiDetails ? AI_NOTICE : null,
    hasDetails: tagRows.length > 0 || hasAiDetails,
    hasAiDetails,
  };
}

export function legacyReferenceLabel(index) {
  return `Legacy reference ${index + 1}`;
}

export function normalizeQueryResponse(data) {
  const source = data && typeof data === "object" ? data : {};
  const knownFileIds = new Set();
  const structuredItems = (Array.isArray(source.items) ? source.items : [])
    .map(structuredItem)
    .filter((item) => {
      if (!item || knownFileIds.has(item.file_id)) return false;
      knownFileIds.add(item.file_id);
      return true;
    });
  const coveredKeys = new Set(structuredItems.flatMap((item) => [item.display_key, item.original_key, item.thumbnail_key]).filter(Boolean));
  const legacyItems = (Array.isArray(source.results) ? source.results : [])
    .flatMap((key, index) => {
      if (!nonEmptyString(key) || coveredKeys.has(key)) return [];
      return [{
        identity: `legacy:${index}:${key}`,
        file_id: null,
        file_type: null,
        display_key: key,
        original_key: null,
        thumbnail_key: null,
        can_preview: false,
        can_manage: false,
        tags: {},
        detections: [],
        model_version: "",
        legacy: true,
      }];
    });

  return {
    items: [...structuredItems, ...legacyItems],
    structuredItems,
    legacyItems,
    count: structuredItems.length + legacyItems.length,
  };
}
