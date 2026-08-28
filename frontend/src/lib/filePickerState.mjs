export function selectFilePickerFile(state = {}, file = null) {
  return {
    file,
    inputRevision: (state.inputRevision || 0) + 1,
  };
}

export function clearFilePickerFile(state = {}) {
  return selectFilePickerFile(state, null);
}

export function filePickerClassName(variant) {
  return variant === "dropzone" ? "file-picker file-picker-dropzone" : "file-picker";
}
