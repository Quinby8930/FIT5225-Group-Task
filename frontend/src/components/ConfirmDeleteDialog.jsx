import { useEffect, useRef } from "react";

export default function ConfirmDeleteDialog({ open, pending, count, onCancel, onConfirm, returnFocusRef, fallbackFocusRef }) {
  const dialog = useRef(null);
  const cancel = useRef(null);

  useEffect(() => {
    if (!open) return;
    const node = dialog.current;
    if (!node.open) node.showModal();
    cancel.current?.focus();
    return () => {
      if (node.open) node.close();
      const returnTarget = returnFocusRef?.current;
      const fallbackTarget = fallbackFocusRef?.current;
      (returnTarget?.isConnected ? returnTarget : fallbackTarget)?.focus();
    };
  }, [open, returnFocusRef, fallbackFocusRef]);

  return (
    <dialog
      ref={dialog}
      aria-labelledby="delete-title"
      aria-describedby="delete-description"
      onCancel={(event) => {
        event.preventDefault();
        if (!pending) onCancel();
      }}
    >
      <h2 id="delete-title">Delete {count} selected file(s)?</h2>
      <p id="delete-description">
        This permanently deletes the original media, its generated thumbnail if present, and its
        archive record. This cannot be undone.
      </p>
      <div className="btn-row dialog-actions">
        <button ref={cancel} type="button" className="btn btn-secondary" disabled={pending} onClick={onCancel}>Cancel</button>
        <button type="button" className="btn btn-danger" disabled={pending} onClick={onConfirm}>
          {pending ? "Deleting…" : "Delete permanently"}
        </button>
      </div>
    </dialog>
  );
}
