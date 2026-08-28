function asSelection(selected) {
  return selected instanceof Set ? [...selected] : (Array.isArray(selected) ? selected : []);
}

function structuredFileIds(items) {
  return new Set((Array.isArray(items) ? items : [])
    .filter((item) => item && item.legacy !== true && typeof item.file_id === "string" && item.file_id)
    .map((item) => item.file_id));
}

export function beginQuerySelection() {
  return [];
}

export function toggleFileSelection(selected, fileId) {
  if (typeof fileId !== "string" || !fileId) return asSelection(selected);
  const current = asSelection(selected);
  return current.includes(fileId)
    ? current.filter((value) => value !== fileId)
    : [...current, fileId];
}

export function reconcileSelection(selected, currentItems) {
  const allowed = structuredFileIds(currentItems);
  return [...new Set(asSelection(selected))].filter((fileId) => allowed.has(fileId));
}

export function selectedMutationKeys(selected, currentItems) {
  const selectedIds = new Set(asSelection(selected));
  return [...new Set((Array.isArray(currentItems) ? currentItems : [])
    .filter((item) => (
      item
      && item.legacy !== true
      && item.can_manage === true
      && selectedIds.has(item.file_id)
      && typeof item.original_key === "string"
      && item.original_key
    ))
    .map((item) => item.original_key))];
}
