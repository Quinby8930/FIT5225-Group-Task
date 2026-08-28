const MEDIA_TYPES = new Set(["image", "video"]);

function nonEmptyString(value) {
  return typeof value === "string" && value.trim() ? value : null;
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
    legacy: false,
  };
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
  const displayKeys = new Set(structuredItems.map((item) => item.display_key));
  const legacyItems = (Array.isArray(source.results) ? source.results : [])
    .flatMap((key, index) => {
      if (!nonEmptyString(key) || displayKeys.has(key)) return [];
      return [{
        identity: `legacy:${index}:${key}`,
        file_id: null,
        file_type: null,
        display_key: key,
        original_key: null,
        thumbnail_key: null,
        can_preview: false,
        can_manage: false,
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
