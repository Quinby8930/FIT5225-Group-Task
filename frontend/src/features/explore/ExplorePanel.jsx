import { queryByFile, queryBySpecies, queryByTags, queryByThumbnail } from "../../api/mediaApi";
import MediaCard from "../../components/MediaCard";
import FilePicker from "../../components/FilePicker";
import Field from "../../components/Field";
import QueryChips from "../../components/QueryChips";
import SpeciesSpotlights from "../../components/SpeciesSpotlights";
import useSignedAssetUrls from "../../hooks/useSignedAssetUrls";
import { hasTagCounts, parseTagCounts } from "../../lib/forms";
import { normalizeQueryResponse } from "../../lib/queryResults.mjs";
import { useRef, useState } from "react";
import { beginQuery, settleQuery, shouldShowResultsHeader } from "../../lib/queryLifecycle.mjs";
import {
  fileDescriptor,
  speciesDescriptor,
  tagsDescriptor,
  thumbnailDescriptor,
} from "../../lib/queryDescriptor.mjs";

export default function ExplorePanel({
  items,
  queryState,
  lastDescriptor,
  pendingDescriptor,
  selectedFileIds,
  queryLifecycle,
  onQueryStart,
  onQueryResult,
  onQueryError,
  onClearQuery,
  onToggle,
  onStatus,
  sessionKey,
}) {
  const [species, setSpecies] = useState("");
  const [tagCounts, setTagCounts] = useState("");
  const [thumbnailKey, setThumbnailKey] = useState("");
  const [queryFile, setQueryFile] = useState(null);
  const speciesInputRef = useRef(null);
  const { assetStates, refresh, openOriginal } = useSignedAssetUrls(items, sessionKey, onStatus);
  const structuredItems = items.filter((item) => !item.legacy);
  const legacyItems = items.filter((item) => item.legacy);
  const loading = queryState === "loading";

  async function run(action, descriptor, successMessage) {
    const started = beginQuery(queryLifecycle.current);
    queryLifecycle.current = started;
    onQueryStart(descriptor);
    try {
      const data = await action();
      const normalized = normalizeQueryResponse(data);
      const settled = settleQuery(queryLifecycle.current, started.generation, normalized, "ready");
      if (settled === queryLifecycle.current) return;
      queryLifecycle.current = settled;
      onQueryResult(
        normalized,
        {
          type: normalized.count ? "success" : "info",
          message: successMessage || (normalized.count ? `${normalized.count} result(s) found.` : "No matching media."),
        },
        descriptor
      );
    } catch (error) {
      const settled = settleQuery(queryLifecycle.current, started.generation, null, "error");
      if (settled === queryLifecycle.current) return;
      queryLifecycle.current = settled;
      onQueryError({ type: "error", message: error.message });
    }
  }

  function submitSpecies(event) {
    event.preventDefault();
    const value = species.trim().toLowerCase();
    run(() => queryBySpecies(value), speciesDescriptor(value));
  }

  function selectSuggestedSpecies(value) {
    setSpecies(value);
    run(() => queryBySpecies(value), speciesDescriptor(value));
  }

  function submitTags(event) {
    event.preventDefault();
    const map = parseTagCounts(tagCounts);
    if (!hasTagCounts(map)) {
      onStatus({ type: "error", message: "Enter at least one valid tag before searching." });
      return;
    }
    run(() => queryByTags(map), tagsDescriptor(map));
  }

  function submitFile(event) {
    event.preventDefault();
    if (queryFile) run(() => queryByFile(queryFile), fileDescriptor(queryFile));
  }

  function submitThumbnail(event) {
    event.preventDefault();
    run(async () => {
      const data = await queryByThumbnail(thumbnailKey);
      return { results: data.original_key ? [data.original_key] : [], items: data.item ? [data.item] : [] };
    }, thumbnailDescriptor());
  }

  function clearQuery() {
    onClearQuery?.();
    window.requestAnimationFrame(() => speciesInputRef.current?.focus());
  }

  return (
    <div className="explore-view">
      <header className="page-head">
        <p className="eyebrow">Archive search</p>
        <h1>Explore</h1>
      </header>
      <section className="explore-layout" aria-busy={loading}>
      <aside className="explore-controls" aria-label="Archive search controls">
        <h2>Search the archive</h2>
        <form className="stack" onSubmit={submitSpecies}>
          <Field label="Species">
            <input ref={speciesInputRef} value={species} onChange={(event) => setSpecies(event.target.value)} placeholder="e.g. wombat" />
          </Field>
          <button type="submit" className="btn btn-primary" disabled={!species.trim() || loading}>Find species</button>
        </form>
        <form className="stack search-section" onSubmit={submitTags}>
          <Field label="Tag counts (AND logic)">
            <textarea value={tagCounts} onChange={(event) => setTagCounts(event.target.value)} placeholder="wombat:1, dingo:2" />
          </Field>
          <button type="submit" className="btn btn-secondary" disabled={!tagCounts.trim() || loading}>Find by tags</button>
        </form>
        <form className="stack search-section" onSubmit={submitFile}>
          <Field label="Match by image">
            <FilePicker
              accept="image/jpeg,image/png,image/webp"
              ariaLabel="Choose file to match by image"
              disabled={loading}
              onChange={setQueryFile}
            />
          </Field>
          <button type="submit" className="btn btn-secondary" disabled={!queryFile || loading}>Match image</button>
        </form>
        <details className="advanced-lookup">
          <summary>Advanced lookup (thumbnail key)</summary>
          <form className="stack" onSubmit={submitThumbnail}>
            <Field label="Thumbnail key">
              <input value={thumbnailKey} onChange={(event) => setThumbnailKey(event.target.value)} />
            </Field>
            <button type="submit" className="btn btn-secondary" disabled={!thumbnailKey.trim() || loading}>Find original</button>
          </form>
        </details>
      </aside>

      <section className="results-panel">
        {shouldShowResultsHeader(queryState) && (
          <div className="results-head">
            <div className="results-head-main">
              {lastDescriptor
                ? <QueryChips descriptor={lastDescriptor} onClear={clearQuery} />
                : <span className="results-hint">Completed archive media</span>}
              {items.length > 0 && <span className="result-count">{items.length} result(s)</span>}
            </div>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => refresh().catch(() => onStatus({ type: "error", message: "Media previews could not be refreshed." }))}
              disabled={!structuredItems.length || loading}
            >
              Refresh previews
            </button>
          </div>
        )}

        {queryState === "idle" && (
          <div className="explore-idle">
            <SpeciesSpotlights disabled={loading} onSelect={selectSuggestedSpecies} />
            <div className="explore-intro">
              <p className="eyebrow">Search field guide</p>
              <ol>
                <li><span>01</span><div><b>Species</b><p>Find every completed record containing at least one animal of that species.</p></div></li>
                <li><span>02</span><div><b>Tag counts</b><p>Combine minimum counts with AND logic, for example wombat:2 and dingo:1.</p></div></li>
                <li><span>03</span><div><b>Match by image</b><p>Discover matching species tags without adding the reference image to the archive.</p></div></li>
              </ol>
              <p className="privacy-note">Archive previews remain private and are delivered through short-lived signed URLs.</p>
            </div>
          </div>
        )}
        {loading && !items.length && <p className="empty-state">Searching the archive…</p>}
        {queryState === "empty" && <p className="empty-state">No archive media matched this query.</p>}
        {queryState === "error" && <p className="empty-state">The query could not be completed. Check the status message and try again.</p>}

        {items.length > 0 && (
          <div className="wall-wrap">
            {loading && (
              <div className="wall-overlay" role="status">
                <p className="overlay-title">Searching the archive…</p>
                <QueryChips descriptor={pendingDescriptor} />
                <p className="overlay-note">Previous results remain until the new query completes.</p>
              </div>
            )}
            <div className="media-wall" inert={loading ? true : undefined} aria-hidden={loading || undefined}>
              {structuredItems.map((item) => (
                <MediaCard
                  key={item.identity}
                  item={item}
                  assetStates={assetStates}
                  selected={selectedFileIds.includes(item.file_id)}
                  onToggle={onToggle}
                  onOpenOriginal={openOriginal}
                />
              ))}
            </div>
            {legacyItems.length > 0 && (
              <section className="legacy-results" aria-label="Legacy references">
                <h3>Legacy references</h3>
                <p>Preview and management are unavailable because this response did not provide trusted media metadata.</p>
                <ul>
                  {legacyItems.map((item) => <li key={item.identity}><code>{item.display_key}</code></li>)}
                </ul>
              </section>
            )}
          </div>
        )}
      </section>
      </section>
    </div>
  );
}
