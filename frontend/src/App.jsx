import { useState } from "react";
import { getAuthTest } from "./api/apiClient";
import AuthCallback from "./auth/AuthCallback";
import AuthControls from "./auth/AuthControls";
import { getCurrentUser, signIn } from "./auth/cognitoAuth";
import StatusBanner from "./components/StatusBanner";
import ExplorePanel from "./features/explore/ExplorePanel";
import ManagePanel from "./features/manage/ManagePanel";
import NotificationsPanel from "./features/notifications/NotificationsPanel";
import UploadPanel from "./features/upload/UploadPanel";
import { beginQuerySelection, toggleFileSelection } from "./lib/manageSelection.mjs";

const views = [["explore", "Explore"], ["upload", "Upload"], ["manage", "Manage"], ["notifications", "Notifications"]];

export default function App() {
  const [activeView, setActiveView] = useState("explore");
  const [status, setStatus] = useState(null);
  const [query, setQuery] = useState({ items: [], structuredItems: [], legacyItems: [], count: 0 });
  const [queryState, setQueryState] = useState("idle");
  const [selectedFileIds, setSelectedFileIds] = useState([]);
  const user = getCurrentUser();

  if (window.location.pathname === "/callback") return <AuthCallback />;
  if (window.location.pathname === "/logout") window.history.replaceState({}, document.title, "/");

  function startQuery() {
    setSelectedFileIds(beginQuerySelection());
    setQuery({ items: [], structuredItems: [], legacyItems: [], count: 0 });
    setQueryState("loading");
    setStatus({ type: "info", message: "Searching the archive…" });
  }

  function completeQuery(nextQuery, nextStatus) {
    setQuery(nextQuery);
    setQueryState(nextStatus.type === "error" ? "error" : (nextQuery.count ? "ready" : "empty"));
    setStatus(nextStatus);
  }

  async function testAuth() {
    try { const result = await getAuthTest(); setStatus({ type: "success", message: `Protected API available: ${JSON.stringify(result)}` }); }
    catch (error) { setStatus({ type: "error", message: error.message }); }
  }

  return <main className="app-shell">
    <header className="app-header"><div><p className="eyebrow">Pacific BioArchive</p><h1>Wildlife media archive</h1></div><AuthControls /></header>
    {!user ? <section className="auth-gate"><p className="eyebrow">Private collection</p><h2>Sign in to explore the archive.</h2><button type="button" onClick={signIn}>Continue with Cognito</button></section> : <>
      <section className="session-bar"><div><span>{user.email || user["cognito:username"] || "Signed in"}</span><code>Session {user.sub}</code></div><button type="button" className="secondary-button" onClick={testAuth}>Check auth</button></section>
      <nav className="tabs" aria-label="Primary navigation">{views.map(([id, label]) => <button key={id} type="button" className={activeView === id ? "active" : ""} aria-current={activeView === id ? "page" : undefined} onClick={() => setActiveView(id)}>{label}</button>)}</nav>
      <StatusBanner status={status} />
      {activeView === "explore" && <ExplorePanel items={query.items} queryState={queryState} selectedFileIds={selectedFileIds} onQueryStart={startQuery} onQueryResult={completeQuery} onToggle={(fileId) => setSelectedFileIds((current) => toggleFileSelection(current, fileId))} onStatus={setStatus} sessionKey={user.sub} />}
      {activeView === "upload" && <UploadPanel onStatus={setStatus} />}
      {activeView === "manage" && <ManagePanel selectedFileIds={selectedFileIds} currentItems={query.structuredItems} onStatus={setStatus} />}
      {activeView === "notifications" && <NotificationsPanel onStatus={setStatus} />}
    </>}
  </main>;
}
