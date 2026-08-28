import { useMemo, useState } from "react";
import { deleteFiles, editTags } from "../../api/mediaApi";
import Field from "../../components/Field";
import { parseSpeciesList } from "../../lib/forms";
import { selectedMutationKeys } from "../../lib/manageSelection.mjs";

export default function ManagePanel({ selectedFileIds, currentItems, onStatus }) {
  const [tagsText, setTagsText] = useState("");
  const [operation, setOperation] = useState(1);
  const keys = useMemo(() => selectedMutationKeys(selectedFileIds, currentItems), [selectedFileIds, currentItems]);
  async function mutate(action, message) {
    if (!keys.length) return;
    onStatus({ type: "info", message: "Updating selected media…" });
    try { await action(keys); onStatus({ type: "success", message }); } catch (error) { onStatus({ type: "error", message: error.message }); }
  }
  return <section className="panel narrow-panel"><div className="panel-title"><div><p className="eyebrow">Selected records</p><h2>Manage media</h2></div><span>{keys.length} manageable item(s)</span></div><p className="empty-state">Only selected archive records with server-provided management permission can be changed.</p><div className="stack"><Field label="Tags"><input value={tagsText} onChange={(event) => setTagsText(event.target.value)} placeholder="wombat, dingo" /></Field><div className="segmented"><button type="button" className={operation === 1 ? "active" : ""} onClick={() => setOperation(1)}>Add</button><button type="button" className={operation === 0 ? "active" : ""} onClick={() => setOperation(0)}>Remove</button></div><button type="button" disabled={!keys.length || !tagsText.trim()} onClick={() => mutate((selected) => editTags(selected, parseSpeciesList(tagsText), operation), "Tags updated.")}>Apply tags</button><button type="button" className="danger-button" disabled={!keys.length} onClick={() => mutate(deleteFiles, "Files deleted.")}>Delete files</button></div></section>;
}
