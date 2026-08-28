import { useEffect, useRef, useState } from "react";
import { getAuthTest } from "./api/apiClient";
import AuthCallback from "./auth/AuthCallback";
import AuthControls from "./auth/AuthControls";
import { signIn } from "./auth/cognitoAuth";
import StatusBanner from "./components/StatusBanner";
import ExplorePanel from "./features/explore/ExplorePanel";
import ManagePanel from "./features/manage/ManagePanel";
import NotificationsPanel from "./features/notifications/NotificationsPanel";
import UploadPanel from "./features/upload/UploadPanel";
import { beginQuerySelection, toggleFileSelection } from "./lib/manageSelection.mjs";
import { removeManagedItems } from "./lib/manageWorkflow.mjs";
import useAuthSession from "./hooks/useAuthSession";

const views = [["explore", "Explore"], ["upload", "Upload"], ["manage", "Manage"], ["notifications", "Notifications"]];

export default function App() {
  const [activeView, setActiveView] = useState("explore");
  const [status, setStatus] = useState(null);
  const [query, setQuery] = useState({ items: [], structuredItems: [], legacyItems: [], count: 0 });
  const [queryState, setQueryState] = useState("idle");
  const [selectedFileIds, setSelectedFileIds] = useState([]);
  const { user, reason: sessionReason } = useAuthSession();
  const previousUser = useRef(user?.sub || null);

  useEffect(() => {
    if (window.location.pathname === "/logout") window.history.replaceState({}, document.title, "/");
  }, []);
  useEffect(() => {
    if (previousUser.current !== (user?.sub || null)) {
      previousUser.current = user?.sub || null;
      setQuery({ items: [], structuredItems: [], legacyItems: [], count: 0 });
      setSelectedFileIds([]);
      setQueryState("idle");
    }
  }, [user?.sub]);

  if (window.location.pathname === "/callback") return <AuthCallback />;

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
    try { await getAuthTest(); setStatus({ type: "success", message: "Protected API is available." }); }
    catch (error) { setStatus({ type: "error", message: error.message }); }
  }

  return <main className="app-shell">
    <header className="app-header"><div><p className="eyebrow">Pacific BioArchive</p><h1>Wildlife media archive</h1></div>{user && <AuthControls user={user} />}</header>
    {!user ? <section className="auth-gate"><p className="eyebrow">Private collection</p><h2>Sign in to explore the archive.</h2>{sessionReason === "expired" && <p role="alert">Your session has expired. Please sign in again.</p>}<AuthControls user={null} /></section> : <>
      <details className="session-bar"><summary>Demo diagnostics</summary><div><span>{user.email || user["cognito:username"] || "Signed in"}</span><code>Session {user.sub}</code><button type="button" className="secondary-button" onClick={testAuth}>Check auth</button></div></details>
      <nav className="tabs" aria-label="Primary navigation">{views.map(([id, label]) => <button key={id} type="button" className={activeView === id ? "active" : ""} aria-current={activeView === id ? "page" : undefined} onClick={() => setActiveView(id)}>{label}</button>)}</nav>
      <StatusBanner status={status} />
      {activeView === "explore" && <ExplorePanel items={query.items} queryState={queryState} selectedFileIds={selectedFileIds} onQueryStart={startQuery} onQueryResult={completeQuery} onToggle={(fileId) => setSelectedFileIds((current) => toggleFileSelection(current, fileId))} onStatus={setStatus} sessionKey={user.sub} />}
      {activeView === "upload" && <UploadPanel onStatus={setStatus} />}
      {activeView === "manage" && <ManagePanel selectedFileIds={selectedFileIds} currentItems={query.structuredItems} onStatus={setStatus} onDeleted={(fileIds) => { const items = removeManagedItems(query.items, fileIds); const structuredItems = items.filter((item) => !item.legacy); const legacyItems = items.filter((item) => item.legacy); setQuery({ items, structuredItems, legacyItems, count: items.length }); setSelectedFileIds((current) => current.filter((id) => !fileIds.includes(id))); setQueryState(items.length ? "ready" : "empty"); }} />}
      {activeView === "notifications" && <NotificationsPanel onStatus={setStatus} />}
    </>}
  </main>;
}
