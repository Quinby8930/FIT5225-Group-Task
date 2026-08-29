import { useEffect, useMemo, useRef, useState } from "react";
import { deleteFiles, editTags } from "../../api/mediaApi";
import ConfirmDeleteDialog from "../../components/ConfirmDeleteDialog";
import Field from "../../components/Field";
import { parseSpeciesList } from "../../lib/forms";
import { selectedMutationKeys } from "../../lib/manageSelection.mjs";
import { beginDeleteConfirmation, beginMutation, canCommitManageEffect, canStartMutation, canSubmitTags, confirmDeleteOnce, finishMutationForSession, mutationCount } from "../../lib/manageWorkflow.mjs";

export default function ManagePanel({ selectedFileIds, currentItems, getActiveSession, sessionKey, onStatus, onNavigate, onDeleted }) {
  const [tagsText, setTagsText] = useState("");
  const [operation, setOperation] = useState(1);
  const [deleteState, setDeleteState] = useState({ open: false, pending: false });
  const [mutationState, setMutationState] = useState({ pending: false });
  const [focusEmptyAfterDelete, setFocusEmptyAfterDelete] = useState(false);
  const deleteTrigger = useRef(null);
  const emptyExplore = useRef(null);
  const mutationInFlight = useRef(false);
  const keys = useMemo(() => selectedMutationKeys(selectedFileIds, currentItems), [selectedFileIds, currentItems]);
  const selectedItems = currentItems.filter((item) => selectedFileIds.includes(item.file_id) && item.can_manage);

  useEffect(() => {
    if (focusEmptyAfterDelete && keys.length === 0) {
      emptyExplore.current?.focus();
      setFocusEmptyAfterDelete(false);
    }
  }, [focusEmptyAfterDelete, keys.length]);

  async function updateTags() {
    if (mutationInFlight.current || !canStartMutation(mutationState)) return;
    const sourceSession = getActiveSession?.();
    if (!canCommitManageEffect(sourceSession, sessionKey)) return;
    mutationInFlight.current = true;
    setMutationState((current) => beginMutation(current));
    try {
      const result = await editTags(keys, parseSpeciesList(tagsText), operation);
      if (!canCommitManageEffect(getActiveSession?.(), sourceSession)) return;
      onStatus(sourceSession, { type: "success", message: `Updated ${mutationCount(result, keys.length, "updated")} item(s).` });
    } catch (error) {
      if (!canCommitManageEffect(getActiveSession?.(), sourceSession)) return;
      onStatus(sourceSession, { type: "error", message: error.message });
    } finally {
      if (!canCommitManageEffect(getActiveSession?.(), sourceSession)) return;
      mutationInFlight.current = false;
      setMutationState((current) => finishMutationForSession(
        current,
        getActiveSession?.(),
        sourceSession,
      ));
    }
  }

  async function confirmDelete() {
    if (mutationInFlight.current || !canStartMutation(mutationState)) return;
    const sourceSession = getActiveSession?.();
    if (!canCommitManageEffect(sourceSession, sessionKey)) return;
    mutationInFlight.current = true;
    setMutationState((current) => beginMutation(current));
    setDeleteState((current) => confirmDeleteOnce(current));
    try {
      const result = await deleteFiles(keys);
      if (!canCommitManageEffect(getActiveSession?.(), sourceSession)) return;
      onStatus(sourceSession, {
        type: "success",
        message: `Deleted ${mutationCount(result, keys.length, "deleted_db_records")} database record(s) and ${mutationCount(result, keys.length, "storage_objects_removed")} storage object(s).`,
      });
      setFocusEmptyAfterDelete((current) => (
        canCommitManageEffect(getActiveSession?.(), sourceSession) ? true : current
      ));
      onDeleted(sourceSession, selectedItems.map((item) => item.file_id));
      setDeleteState((current) => (
        canCommitManageEffect(getActiveSession?.(), sourceSession)
          ? { open: false, pending: false }
          : current
      ));
    } catch (error) {
      if (!canCommitManageEffect(getActiveSession?.(), sourceSession)) return;
      setDeleteState((current) => (
        canCommitManageEffect(getActiveSession?.(), sourceSession)
          ? { open: false, pending: false }
          : current
      ));
      onStatus(sourceSession, { type: "error", message: error.message });
    } finally {
      if (!canCommitManageEffect(getActiveSession?.(), sourceSession)) return;
      mutationInFlight.current = false;
      setMutationState((current) => finishMutationForSession(
        current,
        getActiveSession?.(),
        sourceSession,
      ));
    }
  }

  return (
    <section className="panel narrow-panel manage-panel">
      <div className="panel-title">
        <div>
          <p className="eyebrow">Selected records</p>
          <h1>Manage</h1>
        </div>
        <span>{keys.length} manageable item(s)</span>
      </div>
      <p className="panel-note">Only selected archive records with server-provided management permission can be changed.</p>

      {keys.length === 0 ? (
        <div className="manage-empty">
          <p className="eyebrow">Curation route</p>
          <h2>Choose records before making changes</h2>
          <ol className="empty-workflow">
            <li><span>01</span><div><strong>Search</strong><small>Run a query in Explore.</small></div></li>
            <li><span>02</span><div><strong>Select</strong><small>Choose records marked Owned.</small></div></li>
            <li><span>03</span><div><strong>Curate</strong><small>Add or remove tags, or permanently delete selected media.</small></div></li>
          </ol>
          <p>Only your own uploads can be managed; read-only archive results remain protected.</p>
          <button ref={emptyExplore} type="button" className="btn btn-secondary" onClick={() => onNavigate("explore")}>Go to Explore</button>
        </div>
      ) : (
        <>
          <p className="sel-count">{keys.length} record(s) selected</p>
          <ul className="selected-id-list">
            {selectedItems.map((item) => <li key={item.file_id}>ID {item.file_id.slice(0, 8)}</li>)}
          </ul>
          <div className="stack" aria-busy={mutationState.pending}>
            <Field label="Tags">
              <input value={tagsText} onChange={(event) => setTagsText(event.target.value)} placeholder="wombat, dingo" />
            </Field>
            <div className="segmented" role="group" aria-label="Tag operation">
              <button type="button" disabled={mutationState.pending} aria-pressed={operation === 1} className={operation === 1 ? "active" : ""} onClick={() => setOperation(1)}>Add tags</button>
              <button type="button" disabled={mutationState.pending} aria-pressed={operation === 0} className={operation === 0 ? "active" : ""} onClick={() => setOperation(0)}>Remove tags</button>
            </div>
            <div className="btn-row">
              <button type="button" className="btn btn-primary" disabled={mutationState.pending || !canSubmitTags(tagsText)} onClick={updateTags}>Apply tags</button>
              <button ref={deleteTrigger} type="button" className="btn btn-danger-outline" disabled={mutationState.pending} onClick={() => setDeleteState((current) => beginDeleteConfirmation(current))}>Delete files</button>
            </div>
          </div>
        </>
      )}

      <ConfirmDeleteDialog
        open={deleteState.open}
        pending={deleteState.pending}
        count={keys.length}
        onCancel={() => setDeleteState({ open: false, pending: false })}
        onConfirm={confirmDelete}
        returnFocusRef={deleteTrigger}
        fallbackFocusRef={emptyExplore}
      />
    </section>
  );
}
