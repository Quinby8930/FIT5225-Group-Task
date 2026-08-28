export function selectUploadFile(state = {}, file = null) {
  return { ...state, file, stage: "", receipt: null, submitting: false };
}

export function canSubmitUpload(state = {}) {
  return Boolean(state.file) && !state.submitting && !state.receipt;
}

export function startUpload(state = {}) {
  return canSubmitUpload(state) ? { ...state, submitting: true, receipt: null } : state;
}

export function completeUpload(state = {}, receipt) {
  return { ...state, submitting: false, stage: "queued", receipt };
}

export function failUpload(state = {}) {
  return { ...state, submitting: false, stage: "" };
}

export function canCommitUploadEffect({
  mounted = false,
  activeSession,
  sourceSession,
  currentRun,
  sourceRun,
} = {}) {
  return Boolean(mounted)
    && typeof activeSession === "string"
    && activeSession === sourceSession
    && Number.isInteger(currentRun)
    && currentRun === sourceRun;
}

export function pauseInactivePreview(active, media) {
  if (!active && typeof media?.pause === "function") media.pause();
}
