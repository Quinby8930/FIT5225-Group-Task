import { parseSpeciesList } from "./forms.js";

export function canSubmitTags(text) { return parseSpeciesList(text).length > 0; }
export function beginDeleteConfirmation(state = {}) { return { ...state, open: true, pending: false }; }
export function confirmDeleteOnce(state = {}) { return state.pending ? state : { ...state, pending: true }; }
export function beginMutation(state = {}) { return state.pending ? state : { ...state, pending: true }; }
export function finishMutation(state = {}) { return { ...state, pending: false }; }
export function canStartMutation(state = {}) { return !state.pending; }
export function mutationCount(response, selectedCount, field) {
  return Number.isInteger(response?.[field]) && response[field] >= 0 ? response[field] : selectedCount;
}
export function canCommitManageEffect(activeSession, sourceSession) {
  return typeof sourceSession === "string"
    && sourceSession.trim().length > 0
    && activeSession === sourceSession;
}
export function removeManagedItems(items, fileIds) {
  const removed = new Set(fileIds || []);
  return (items || []).filter((item) => !removed.has(item.file_id));
}
export function removeManagedQueryItems(query = {}, fileIds) {
  const items = removeManagedItems(query.items, fileIds);
  const structuredItems = items.filter((item) => !item.legacy);
  const legacyItems = items.filter((item) => item.legacy);
  return { items, structuredItems, legacyItems, count: items.length };
}
export function removeManagedQueryItemsForSession(query, fileIds, activeSession, sourceSession) {
  return canCommitManageEffect(activeSession, sourceSession)
    ? removeManagedQueryItems(query, fileIds)
    : query;
}
export function removeManagedSelectionForSession(selection, fileIds, activeSession, sourceSession) {
  if (!canCommitManageEffect(activeSession, sourceSession)) return selection;
  const removed = new Set(fileIds || []);
  return (selection || []).filter((fileId) => !removed.has(fileId));
}
export function finishMutationForSession(state, activeSession, sourceSession) {
  return canCommitManageEffect(activeSession, sourceSession) ? finishMutation(state) : state;
}
