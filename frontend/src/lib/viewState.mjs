export function setStatusForView(statuses = {}, view, status) {
  return { ...statuses, [view]: status || null };
}

export function statusForView(statuses = {}, view) {
  return statuses[view] || null;
}

export function resetSessionViewState() {
  return { activeView: "home", statuses: {} };
}

export function navigateToView(view, { setActiveView, scrollTo = globalThis.scrollTo } = {}) {
  if (typeof setActiveView !== "function") return;
  setActiveView(view);
  if (typeof scrollTo === "function") {
    scrollTo({ top: 0, left: 0, behavior: "auto" });
  }
}

export function queryStatusAfterDeletion(count) {
  const remaining = Number.isInteger(count) && count > 0 ? count : 0;
  return {
    type: "info",
    message: remaining
      ? `${remaining} result(s) remain after deletion.`
      : "No media remain in this result set.",
  };
}
