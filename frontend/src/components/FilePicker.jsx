import { useRef, useState } from "react";
import { clearFilePickerFile, filePickerClassName, selectFilePickerFile } from "../lib/filePickerState.mjs";

export default function FilePicker({ accept, ariaLabel = "Choose file", disabled = false, id, onChange, variant }) {
  const inputRef = useRef(null);
  const [selection, setSelection] = useState({ file: null, inputRevision: 0 });
  const { file, inputRevision } = selection;

  function chooseFile() {
    if (!disabled) inputRef.current?.click();
  }

  function updateFile(nextFile) {
    setSelection((current) => selectFilePickerFile(current, nextFile));
    onChange(nextFile);
  }

  function clearSelection() {
    setSelection((current) => clearFilePickerFile(current));
    onChange(null);
  }

  return (
    <div className={filePickerClassName(variant)}>
      <input
        key={inputRevision}
        ref={inputRef}
        className="file-picker-native"
        type="file"
        accept={accept}
        disabled={disabled}
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => updateFile(event.target.files?.[0] || null)}
      />
      <div className="file-picker-actions">
        <button id={id} type="button" className="btn btn-secondary" aria-label={ariaLabel} onClick={chooseFile} disabled={disabled}>
          Choose file
        </button>
        {file && (
          <button type="button" className="btn btn-quiet" onClick={clearSelection} disabled={disabled}>
            Clear selection
          </button>
        )}
      </div>
      <p className="file-picker-status" aria-live="polite">
        {file ? `Selected file: ${file.name}` : "No file selected."}
      </p>
    </div>
  );
}
