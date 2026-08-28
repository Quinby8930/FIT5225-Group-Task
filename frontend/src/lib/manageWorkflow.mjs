import { parseSpeciesList } from "./forms.js";

export function canSubmitTags(text) { return parseSpeciesList(text).length > 0; }
export function beginDeleteConfirmation(state = {}) { return { ...state, open: true, pending: false }; }
export function confirmDeleteOnce(state = {}) { return state.pending ? state : { ...state, pending: true }; }
export function mutationCount(response, selectedCount, field) {
  return Number.isInteger(response?.[field]) && response[field] >= 0 ? response[field] : selectedCount;
}
export function removeManagedItems(items, fileIds) {
  const removed = new Set(fileIds || []);
  return (items || []).filter((item) => !removed.has(item.file_id));
}
