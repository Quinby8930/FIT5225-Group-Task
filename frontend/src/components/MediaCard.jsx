function previewMessage(state) {
  const messages = {
    loading: "Loading preview…", signing_failed: "Preview signing failed; retrying…",
    forbidden: "Preview unavailable", not_found: "Media not found", not_completed: "Processing is not complete",
    expired: "Refreshing preview…", unavailable: "Preview temporarily unavailable",
  };
  return messages[state?.status] || "Preview unavailable";
}

export default function MediaCard({ item, assetStates, selected, onToggle, onOpenOriginal }) {
  const previewKey = item.file_type === "image" ? item.display_key : item.original_key;
  const state = assetStates[previewKey];
  const url = state?.status === "ready" ? state.url : null;
  const shortId = item.file_id.slice(0, 8);

  return <article className="media-card">
    <div className="media-preview">
      {url && item.file_type === "image" && <img src={url} alt={`Archive image ${shortId}`} loading="lazy" />}
      {url && item.file_type === "video" && <video controls preload="none" src={url}><track kind="captions" /></video>}
      {!url && <span>{previewMessage(state)}</span>}
    </div>
    <div className="media-card-body">
      <div className="media-card-heading"><strong>{item.file_type}</strong><span>{item.can_manage ? "Owned" : "Read-only"}</span></div>
      <p className="file-id">ID {shortId}</p>
      {item.can_manage && <label className="check-row"><input type="checkbox" checked={selected} onChange={() => onToggle(item.file_id)} /><span>Select for management</span></label>}
      <div className="inline-actions">
        {url && canOpenPreview(item) && <a href={url} target="_blank" rel="noreferrer">Open</a>}
        {canOpenFullImage(item) && <button type="button" className="link-button" onClick={() => onOpenOriginal(item)}>Open full image</button>}
      </div>
      <details><summary>Technical details</summary><code>{item.display_key}</code></details>
    </div>
  </article>;
}
import { canOpenFullImage, canOpenPreview } from "../lib/mediaActions.mjs";
