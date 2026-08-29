export function setStatusForView(statuses = {}, view, status) {
  return { ...statuses, [view]: status || null };
}

export function statusForView(statuses = {}, view) {
  return statuses[view] || null;
}

export function resetSessionViewState() {
  return { activeView: "home", statuses: {} };
}

export function projectSessionViewState(ownerSession, activeSession, state) {
  if (ownerSession === activeSession) return state;
  return {
    activeView: "home",
    statuses: {},
    query: { items: [], structuredItems: [], legacyItems: [], count: 0 },
    queryState: "idle",
    descriptors: { lastSuccessfulDescriptor: null, pendingDescriptor: null },
    selectedFileIds: [],
  };
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

export function advanceSessionIdentity(previous = {}, subject = null) {
  const priorGeneration = Number.isInteger(previous?.generation) && previous.generation >= 0
    ? previous.generation
    : 0;
  const normalizedSubject = typeof subject === "string" && subject.trim() ? subject : null;
  const generation = priorGeneration + 1;
  return {
    generation,
    subject: normalizedSubject,
    key: normalizedSubject ? `session-${generation}` : null,
  };
}
