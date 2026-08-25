import React, { useEffect, useMemo, useState } from "react";
import { getAuthTest } from "./api/apiClient";
import {
  deleteFiles,
  displayUrlForKey,
  editTags,
  queryByFile,
  queryBySpecies,
  queryByTags,
  queryByThumbnail,
  listNotifications,
  listSubscriptions,
  subscribeToSpecies,
  unsubscribeFromSpecies,
  uploadMedia,
} from "./api/mediaApi";
import AuthCallback from "./auth/AuthCallback";
import AuthControls from "./auth/AuthControls";
import { getCurrentUser, signIn } from "./auth/cognitoAuth";
import { parseKeyList, parseSpeciesList, parseTagCounts } from "./lib/forms";

const tabs = [
  ["upload", "Upload"],
  ["query", "Query"],
  ["manage", "Tags & delete"],
  ["notify", "Notifications"],
];

function Status({ status }) {
  if (!status?.message) return null;
  return <p className={`status ${status.type || "info"}`}>{status.message}</p>;
}

function Field({ label, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function ResultCard({ resultKey, selected, onToggle, onOpenOriginal }) {
  const url = displayUrlForKey(resultKey);
  const isImage = /\.(jpg|jpeg|png|webp)$/i.test(resultKey);
  const kind = resultKey.includes(".mp4") || resultKey.includes(".mov") ? "video" : "image";

  return (
    <article className="result-card">
      <div className="preview">
        {url && isImage ? <img src={url} alt={resultKey} loading="lazy" /> : <span>{kind}</span>}
      </div>
      <div className="result-body">
        <label className="check-row">
          <input type="checkbox" checked={selected} onChange={() => onToggle(resultKey)} />
          <span>Select</span>
        </label>
        <code title={resultKey}>{resultKey}</code>
        <div className="inline-actions">
          {url && (
            <a href={url} target="_blank" rel="noreferrer">
              Open
            </a>
          )}
          {resultKey.startsWith("thumbnails/") && (
            <button type="button" className="link-button" onClick={() => onOpenOriginal(resultKey)}>
              Full image
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

function UploadPanel({ onStatus }) {
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState(null);

  async function submit(event) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setReceipt(null);
    onStatus({ type: "info", message: "Preparing upload..." });
    try {
      const result = await uploadMedia(file);
      setReceipt(result);
      onStatus({ type: "success", message: "Upload accepted. Processing starts from S3." });
    } catch (error) {
      onStatus({
        type: "error",
        message: error.message.includes("DUPLICATE_FILE")
          ? "Duplicate file detected by checksum."
          : error.message,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="panel-title">
        <h2>Media upload</h2>
        <span>Images and videos</span>
      </div>
      <form className="stack" onSubmit={submit}>
        <Field label="File">
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime"
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
        </Field>
        {file && (
          <dl className="metadata-grid">
            <div>
              <dt>Name</dt>
              <dd>{file.name}</dd>
            </div>
            <div>
              <dt>Type</dt>
              <dd>{file.type || "unknown"}</dd>
            </div>
            <div>
              <dt>Size</dt>
              <dd>{Math.ceil(file.size / 1024)} KB</dd>
            </div>
          </dl>
        )}
        <button type="submit" disabled={!file || busy}>
          {busy ? "Uploading..." : "Upload"}
        </button>
      </form>
      {receipt && <pre className="success-output">{JSON.stringify(receipt, null, 2)}</pre>}
    </section>
  );
}

function QueryPanel({ results, selectedKeys, setResults, toggleKey, onStatus }) {
  const [tagText, setTagText] = useState("dingo:1, wombat:1");
  const [species, setSpecies] = useState("wombat");
  const [thumbnailKey, setThumbnailKey] = useState("");
  const [queryFile, setQueryFile] = useState(null);

  async function run(action) {
    onStatus({ type: "info", message: "Querying..." });
    try {
      const data = await action();
      setResults(data.results || []);
      onStatus({ type: "success", message: `${data.count ?? data.results?.length ?? 0} result(s).` });
    } catch (error) {
      onStatus({ type: "error", message: error.message });
    }
  }

  async function openOriginal(key) {
    try {
      const data = await queryByThumbnail(key);
      window.open(displayUrlForKey(data.original_key) || data.original_key, "_blank", "noreferrer");
    } catch (error) {
      onStatus({ type: "error", message: error.message });
    }
  }

  return (
    <section className="workspace-grid">
      <div className="panel">
        <div className="panel-title">
          <h2>Query tools</h2>
          <span>AND tags, species, thumbnail, file</span>
        </div>
        <div className="query-grid">
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              run(() => queryByTags(parseTagCounts(tagText)));
            }}
          >
            <Field label="Tags with counts">
              <textarea value={tagText} onChange={(event) => setTagText(event.target.value)} />
            </Field>
            <button type="submit">Find by tags</button>
          </form>
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              run(() => queryBySpecies(species.trim().toLowerCase()));
            }}
          >
            <Field label="Species">
              <input value={species} onChange={(event) => setSpecies(event.target.value)} />
            </Field>
            <button type="submit">Find species</button>
          </form>
          <form
            className="stack"
            onSubmit={async (event) => {
              event.preventDefault();
              try {
                const data = await queryByThumbnail(thumbnailKey);
                setResults([data.original_key]);
                onStatus({ type: "success", message: `Original file: ${data.file_id}` });
              } catch (error) {
                onStatus({ type: "error", message: error.message });
              }
            }}
          >
            <Field label="Thumbnail key">
              <input value={thumbnailKey} onChange={(event) => setThumbnailKey(event.target.value)} />
            </Field>
            <button type="submit">Find original</button>
          </form>
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              if (queryFile) run(() => queryByFile(queryFile));
            }}
          >
            <Field label="Query file">
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                onChange={(event) => setQueryFile(event.target.files?.[0] || null)}
              />
            </Field>
            <button type="submit" disabled={!queryFile}>
              Match by file
            </button>
          </form>
        </div>
      </div>
      <section className="panel results-panel">
        <div className="panel-title">
          <h2>Results</h2>
          <span>{results.length} item(s)</span>
        </div>
        <div className="result-grid">
          {results.map((key) => (
            <ResultCard
              key={key}
              resultKey={key}
              selected={selectedKeys.includes(key)}
              onToggle={toggleKey}
              onOpenOriginal={openOriginal}
            />
          ))}
          {!results.length && <p className="empty-state">No results loaded.</p>}
        </div>
      </section>
    </section>
  );
}

function ManagePanel({ selectedKeys, onStatus }) {
  const [keysText, setKeysText] = useState("");
  const [tagsText, setTagsText] = useState("wombat");
  const [operation, setOperation] = useState(1);
  const keys = useMemo(
    () => [...new Set([...selectedKeys, ...parseKeyList(keysText)])],
    [selectedKeys, keysText]
  );

  async function mutate(action, success) {
    if (!keys.length) return;
    onStatus({ type: "info", message: "Updating..." });
    try {
      const data = await action(keys);
      onStatus({ type: "success", message: `${success}: ${JSON.stringify(data)}` });
    } catch (error) {
      onStatus({ type: "error", message: error.message });
    }
  }

  return (
    <section className="panel narrow-panel">
      <div className="panel-title">
        <h2>Bulk management</h2>
        <span>{keys.length} selected key(s)</span>
      </div>
      <div className="stack">
        <Field label="Additional keys">
          <textarea value={keysText} onChange={(event) => setKeysText(event.target.value)} />
        </Field>
        <Field label="Tags">
          <input value={tagsText} onChange={(event) => setTagsText(event.target.value)} />
        </Field>
        <div className="segmented">
          <button
            type="button"
            className={operation === 1 ? "active" : ""}
            onClick={() => setOperation(1)}
          >
            Add
          </button>
          <button
            type="button"
            className={operation === 0 ? "active" : ""}
            onClick={() => setOperation(0)}
          >
            Remove
          </button>
        </div>
        <button
          type="button"
          onClick={() =>
            mutate(
              (items) => editTags(items, parseSpeciesList(tagsText), operation),
              "Tags updated"
            )
          }
          disabled={!keys.length}
        >
          Apply tags
        </button>
        <button
          type="button"
          className="danger-button"
          onClick={() => mutate(deleteFiles, "Files deleted")}
          disabled={!keys.length}
        >
          Delete files
        </button>
      </div>
    </section>
  );
}

function NotificationPanel({ user, onStatus }) {
  const [species, setSpecies] = useState("wombat");
  const [subscriptions, setSubscriptions] = useState([]);
  const [notifications, setNotifications] = useState([]);

  async function refresh() {
    try {
      const [subs, notes] = await Promise.all([
        listSubscriptions(user.sub),
        listNotifications(user.sub),
      ]);
      setSubscriptions(subs.species || []);
      setNotifications(notes.notifications || []);
    } catch (error) {
      onStatus({ type: "error", message: error.message });
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function subscribe(event) {
    event.preventDefault();
    try {
      await subscribeToSpecies(user.sub, species.trim().toLowerCase());
      onStatus({ type: "success", message: "Subscription saved." });
      refresh();
    } catch (error) {
      onStatus({ type: "error", message: error.message });
    }
  }

  async function unsubscribe(item) {
    try {
      await unsubscribeFromSpecies(user.sub, item);
      refresh();
    } catch (error) {
      onStatus({ type: "error", message: error.message });
    }
  }

  return (
    <section className="workspace-grid">
      <div className="panel">
        <div className="panel-title">
          <h2>Subscriptions</h2>
          <span>{subscriptions.length} watched tag(s)</span>
        </div>
        <form className="inline-form" onSubmit={subscribe}>
          <Field label="Species">
            <input value={species} onChange={(event) => setSpecies(event.target.value)} />
          </Field>
          <button type="submit">Subscribe</button>
        </form>
        <div className="pill-list">
          {subscriptions.map((item) => (
            <button key={item} type="button" onClick={() => unsubscribe(item)}>
              {item} x
            </button>
          ))}
          {!subscriptions.length && <p className="empty-state">No subscriptions.</p>}
        </div>
      </div>
      <div className="panel">
        <div className="panel-title">
          <h2>Inbox</h2>
          <button type="button" className="secondary-button" onClick={refresh}>
            Refresh
          </button>
        </div>
        <div className="notification-list">
          {notifications.map((item) => (
            <article key={item.notification_id} className="notification-item">
              <strong>{item.species}</strong>
              <code>{item.object_key}</code>
              <time>{new Date(item.created_at).toLocaleString()}</time>
            </article>
          ))}
          {!notifications.length && <p className="empty-state">No notifications.</p>}
        </div>
      </div>
    </section>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState("upload");
  const [status, setStatus] = useState(null);
  const [results, setResults] = useState([]);
  const [selectedKeys, setSelectedKeys] = useState([]);
  const user = getCurrentUser();

  if (window.location.pathname === "/callback") {
    return <AuthCallback />;
  }

  if (window.location.pathname === "/logout") {
    window.history.replaceState({}, document.title, "/");
  }

  function toggleKey(key) {
    setSelectedKeys((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key]
    );
  }

  async function testAuth() {
    try {
      const result = await getAuthTest();
      setStatus({ type: "success", message: `Protected API ok: ${JSON.stringify(result)}` });
    } catch (error) {
      setStatus({ type: "error", message: error.message });
    }
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">Pacific BioArchive</p>
          <h1>Wildlife media console</h1>
        </div>
        <AuthControls />
      </header>

      {!user ? (
        <section className="auth-gate">
          <h2>Sign in required</h2>
          <button type="button" onClick={signIn}>
            Continue with Cognito
          </button>
        </section>
      ) : (
        <>
          <section className="session-bar">
            <div>
              <span>{user.email || user["cognito:username"] || "Signed in"}</span>
              <code>{user.sub}</code>
            </div>
            <button type="button" className="secondary-button" onClick={testAuth}>
              Check auth
            </button>
          </section>

          <nav className="tabs" aria-label="Workflow">
            {tabs.map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={activeTab === id ? "active" : ""}
                onClick={() => setActiveTab(id)}
              >
                {label}
              </button>
            ))}
          </nav>

          <Status status={status} />

          {activeTab === "upload" && <UploadPanel onStatus={setStatus} />}
          {activeTab === "query" && (
            <QueryPanel
              results={results}
              selectedKeys={selectedKeys}
              setResults={setResults}
              toggleKey={toggleKey}
              onStatus={setStatus}
            />
          )}
          {activeTab === "manage" && <ManagePanel selectedKeys={selectedKeys} onStatus={setStatus} />}
          {activeTab === "notify" && <NotificationPanel user={user} onStatus={setStatus} />}
        </>
      )}
    </main>
  );
}
