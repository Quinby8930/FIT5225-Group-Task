import { useEffect, useRef } from "react";

export default function ConfirmDeleteDialog({ open, pending, count, onCancel, onConfirm, returnFocusRef }) {
  const dialog = useRef(null); const confirm = useRef(null);
  useEffect(() => { if (!open) return; const node = dialog.current; if (!node.open) node.showModal(); confirm.current?.focus(); return () => { if (node.open) node.close(); returnFocusRef?.current?.focus(); }; }, [open, returnFocusRef]);
  return <dialog ref={dialog} aria-labelledby="delete-title" aria-describedby="delete-description" onCancel={(event) => { event.preventDefault(); if (!pending) onCancel(); }}><h2 id="delete-title">Delete {count} selected file(s)?</h2><p id="delete-description">This permanently deletes originals, derived files, and metadata. This cannot be undone.</p><div className="inline-actions"><button type="button" className="secondary-button" disabled={pending} onClick={onCancel}>Cancel</button><button ref={confirm} type="button" className="danger-button" disabled={pending} onClick={onConfirm}>{pending ? "Deleting…" : "Delete permanently"}</button></div></dialog>;
}
