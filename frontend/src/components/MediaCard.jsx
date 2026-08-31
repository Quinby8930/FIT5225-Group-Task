import { canOpenFullImage, canOpenPreview, canRenderInlinePreview, previewStatusSemantics } from "../lib/mediaActions.mjs";
import { usableAssetUrl } from "../lib/assetUrls.mjs";
import { canRenderRawMediaKey } from "../lib/queryResults.mjs";

function previewMessage(state) {
  const messages = {
    loading: "Loading preview…",
    signing_failed: "Preview signing failed; retrying…",
    forbidden: "Preview unavailable",
    not_found: "Media not found",
    not_completed: "Processing is not complete",
    expired: "Refreshing preview…",
    unavailable: "Preview temporarily unavailable",
    retry_exhausted: "Preview retries exhausted. Refresh previews to try again.",
  };
  return messages[state?.status] || "Preview unavailable";
}

export default function MediaCard({ item, assetStates, selected, onToggle, onOpenOriginal }) {
  const canRenderPreview = canRenderInlinePreview(item);
  const previewKey = canRenderPreview
    ? (item.file_type === "image" ? item.display_key : item.original_key)
    : null;
  const state = assetStates[previewKey];
  const url = canRenderPreview ? usableAssetUrl(state) : null;
  const shortId = item.file_id.slice(0, 8);
  const canOpenOriginal = canOpenFullImage(item);

  return (
    <article className="media-card">
      <div className="media-preview">
        {url && item.file_type === "image" && (
          canOpenOriginal ? (
            <button
              type="button"
              className="media-preview-action"
              onClick={() => onOpenOriginal(item)}
              aria-label={`Open full-size image ${shortId} in a new tab`}
            >
              <img src={url} alt={`Archive image ${shortId}`} loading="lazy" />
            </button>
          ) : <img src={url} alt={`Archive image ${shortId}`} loading="lazy" />
        )}
        {url && item.file_type === "video" && (
          <video
            controls
            preload="none"
            src={url}
            aria-label={`Archive video ${shortId}`}
            onPlay={(event) => {
              if (usableAssetUrl(state)) return;
              event.currentTarget.pause();
              event.currentTarget.removeAttribute("src");
              event.currentTarget.load();
            }}
          >
            Your browser cannot play this video.
          </video>
        )}
        {!url && <span {...previewStatusSemantics(state)}>{previewMessage(state)}</span>}
      </div>
      <div className="media-card-body">
        <div className="media-card-heading">
          <strong>{item.file_type}</strong>
          <span>{item.can_manage ? "Owned" : "Read-only"}</span>
        </div>
        <p className="file-id">ID {shortId}</p>
        {item.can_manage && (
          <label className="check-row">
            <input type="checkbox" checked={selected} onChange={() => onToggle(item.file_id)} />
            <span>Select for management</span>
          </label>
        )}
        <div className="inline-actions">
          {url && canOpenPreview(item) && (
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open time-limited ${item.file_type} preview ${shortId} in a new tab`}
              onClick={(event) => {
                if (!usableAssetUrl(state)) event.preventDefault();
              }}
            >
              {item.file_type === "video" ? "Open video" : "Open preview"}
            </a>
          )}
          {canOpenOriginal && (
            <button
              type="button"
              className="link-button"
              onClick={() => onOpenOriginal(item)}
              aria-label={`Open full-size image ${shortId} in a new tab`}
            >
              Full-size image
            </button>
          )}
        </div>
        {canRenderRawMediaKey(item) && (
          <details>
            <summary>Technical details</summary>
            <code>{item.display_key}</code>
          </details>
        )}
      </div>
    </article>
  );
}
