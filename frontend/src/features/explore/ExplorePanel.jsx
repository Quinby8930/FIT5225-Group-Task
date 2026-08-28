import { queryByFile, queryBySpecies, queryByTags, queryByThumbnail } from "../../api/mediaApi";
import MediaCard from "../../components/MediaCard";
import Field from "../../components/Field";
import useSignedAssetUrls from "../../hooks/useSignedAssetUrls";
import { parseTagCounts } from "../../lib/forms";
import { normalizeQueryResponse } from "../../lib/queryResults.mjs";
import { useEffect, useRef, useState } from "react";
import { beginQuery, settleQuery } from "../../lib/queryLifecycle.mjs";

export default function ExplorePanel({ items, queryState, selectedFileIds, onQueryStart, onQueryResult, onToggle, onStatus, sessionKey }) {
  const [species, setSpecies] = useState("");
  const [tagCounts, setTagCounts] = useState("");
  const [thumbnailKey, setThumbnailKey] = useState("");
  const [queryFile, setQueryFile] = useState(null);
  const queryLifecycle = useRef({ generation: 0, phase: "idle", result: null });
  const { assetStates, refresh, openOriginal } = useSignedAssetUrls(items, sessionKey, onStatus);
  const structuredItems = items.filter((item) => !item.legacy);
  const legacyItems = items.filter((item) => item.legacy);

  useEffect(() => () => { queryLifecycle.current = beginQuery(queryLifecycle.current); }, []);

  async function run(action, successMessage) {
    const started = beginQuery(queryLifecycle.current);
    queryLifecycle.current = started;
    onQueryStart();
    try {
      const data = await action();
      const normalized = normalizeQueryResponse(data);
      const settled = settleQuery(queryLifecycle.current, started.generation, normalized, "ready");
      if (settled === queryLifecycle.current) return;
      queryLifecycle.current = settled;
      onQueryResult(normalized, { type: normalized.count ? "success" : "info", message: successMessage || (normalized.count ? `${normalized.count} result(s) found.` : "No matching media.") });
    } catch (error) {
      const empty = { items: [], structuredItems: [], legacyItems: [], count: 0 };
      const settled = settleQuery(queryLifecycle.current, started.generation, empty, "error");
      if (settled === queryLifecycle.current) return;
      queryLifecycle.current = settled;
      onQueryResult(empty, { type: "error", message: error.message });
    }
  }

  return <section className="explore-layout" aria-busy={queryState === "loading"}>
    <aside className="explore-controls" aria-label="Explore search controls">
      <div className="panel-title"><div><p className="eyebrow">Archive search</p><h2>Explore media</h2></div><span>Completed archive</span></div>
      <form className="stack" onSubmit={(event) => { event.preventDefault(); run(() => queryBySpecies(species.trim().toLowerCase())); }}>
        <Field label="Species"><input value={species} onChange={(event) => setSpecies(event.target.value)} placeholder="e.g. wombat" /></Field>
        <button type="submit" disabled={!species.trim() || queryState === "loading"}>Find species</button>
      </form>
      <form className="stack search-section" onSubmit={(event) => { event.preventDefault(); run(() => queryByTags(parseTagCounts(tagCounts))); }}>
        <Field label="Tag counts"><textarea value={tagCounts} onChange={(event) => setTagCounts(event.target.value)} placeholder="wombat:1, dingo:2" /></Field>
        <button type="submit" className="secondary-button" disabled={!tagCounts.trim() || queryState === "loading"}>Find by tags</button>
      </form>
      <form className="stack search-section" onSubmit={(event) => { event.preventDefault(); if (queryFile) run(() => queryByFile(queryFile)); }}>
        <Field label="Match by image"><input type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => setQueryFile(event.target.files?.[0] || null)} /></Field>
        <button type="submit" className="secondary-button" disabled={!queryFile || queryState === "loading"}>Match image</button>
      </form>
      <details className="advanced-lookup"><summary>Advanced lookup</summary><form className="stack" onSubmit={(event) => { event.preventDefault(); run(async () => { const data = await queryByThumbnail(thumbnailKey); return { results: data.original_key ? [data.original_key] : [] }; }, "Original located as a legacy reference."); }}><Field label="Thumbnail key"><input value={thumbnailKey} onChange={(event) => setThumbnailKey(event.target.value)} /></Field><button type="submit" className="secondary-button" disabled={!thumbnailKey.trim() || queryState === "loading"}>Find original</button></form></details>
    </aside>
    <section className="results-panel" aria-live="polite">
      <div className="panel-title"><div><p className="eyebrow">Query results</p><h2>{queryState === "loading" ? "Searching archive…" : "Media wall"}</h2></div><button type="button" className="secondary-button" onClick={() => refresh().catch(() => onStatus({ type: "error", message: "Media previews could not be refreshed." }))} disabled={!structuredItems.length}>Refresh previews</button></div>
      {queryState === "idle" && <p className="empty-state">Choose a search method to explore completed archive media.</p>}
      {queryState === "loading" && <p className="empty-state">Previous results were cleared while the new query runs.</p>}
      {queryState === "empty" && <p className="empty-state">No archive media matched this query.</p>}
      {queryState === "error" && <p className="empty-state">The query could not be completed. Check the status message and try again.</p>}
      <div className="media-wall">{structuredItems.map((item) => <MediaCard key={item.identity} item={item} assetStates={assetStates} selected={selectedFileIds.includes(item.file_id)} onToggle={onToggle} onOpenOriginal={openOriginal} />)}</div>
      {legacyItems.length > 0 && <section className="legacy-results"><h3>Legacy references</h3><p>Preview and management are unavailable because this response did not provide trusted media metadata.</p><ul>{legacyItems.map((item) => <li key={item.identity}><code>{item.display_key}</code></li>)}</ul></section>}
    </section>
  </section>;
}
