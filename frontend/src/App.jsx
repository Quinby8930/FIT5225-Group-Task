import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getAuthTest } from "./api/apiClient";
import AuthCallback from "./auth/AuthCallback";
import { appConfig } from "./auth/cognitoConfig";
import AppHeader from "./components/AppHeader";
import AppNav from "./components/AppNav";
import StatusBanner from "./components/StatusBanner";
import LandingPage from "./features/landing/LandingPage";
import ExplorePanel from "./features/explore/ExplorePanel";
import HomePanel from "./features/home/HomePanel";
import ManagePanel from "./features/manage/ManagePanel";
import NotificationsPanel from "./features/notifications/NotificationsPanel";
import UploadPanel from "./features/upload/UploadPanel";
import { beginQuerySelection, selectedMutationKeys, toggleFileSelection } from "./lib/manageSelection.mjs";
import { removeManagedItems } from "./lib/manageWorkflow.mjs";
import { beginQuery } from "./lib/queryLifecycle.mjs";
import {
  beginPendingQuery,
  clearQueryDescriptors,
  settleQueryFailure,
  settleQuerySuccess,
} from "./lib/queryDescriptor.mjs";
import { authRouteForPath } from "./lib/appRoutes.mjs";
import { navigateToView, queryStatusAfterDeletion, resetSessionViewState, setStatusForView, statusForView } from "./lib/viewState.mjs";
import useAuthSession from "./hooks/useAuthSession";

const EMPTY_QUERY = { items: [], structuredItems: [], legacyItems: [], count: 0 };
const VIEW_TITLES = {
  home: "Pacific BioArchive",
  explore: "Explore | Pacific BioArchive",
  upload: "Upload | Pacific BioArchive",
  manage: "Manage | Pacific BioArchive",
  notifications: "Notifications | Pacific BioArchive",
};

export default function App() {
  const [activeView, setActiveView] = useState("home");
  const [statuses, setStatuses] = useState({});
  const [query, setQuery] = useState(EMPTY_QUERY);
  const [queryState, setQueryState] = useState("idle");
  const [descriptors, setDescriptors] = useState(clearQueryDescriptors);
  const [selectedFileIds, setSelectedFileIds] = useState([]);
  const { user, reason: sessionReason } = useAuthSession();
  const previousUser = useRef(user?.sub || null);
  const activeSession = useRef(user?.sub || null);
  activeSession.current = user?.sub || null;
  const mainRef = useRef(null);
  const queryLifecycle = useRef({ generation: 0, phase: "idle", result: null });
  const authRoute = authRouteForPath(window.location.pathname, appConfig);
  const demoMode = useMemo(
    () => Boolean(import.meta.env.DEV) || new URLSearchParams(window.location.search).has("demo"),
    []
  );

  const setViewStatus = useCallback((view, status) => {
    setStatuses((current) => setStatusForView(current, view, status));
  }, []);
  const setExploreStatus = useCallback((status) => setViewStatus("explore", status), [setViewStatus]);
  const setUploadStatus = useCallback((sourceSession, status) => {
    if (sourceSession !== activeSession.current) return;
    setViewStatus("upload", status);
  }, [setViewStatus]);
  const setManageStatus = useCallback((status) => setViewStatus("manage", status), [setViewStatus]);
  const setNotificationsStatus = useCallback((status) => setViewStatus("notifications", status), [setViewStatus]);
  const navigate = useCallback((view) => {
    navigateToView(view, {
      setActiveView,
      scrollTo: window.scrollTo.bind(window),
    });
    window.requestAnimationFrame(() => mainRef.current?.focus({ preventScroll: true }));
  }, []);

  useEffect(() => {
    document.title = VIEW_TITLES[activeView] || "Pacific BioArchive";
  }, [activeView]);

  useEffect(() => {
    if (authRoute === "logout") {
      window.history.replaceState({}, document.title, appConfig.homePath);
    }
  }, [authRoute]);

  useEffect(() => {
    if (previousUser.current !== (user?.sub || null)) {
      previousUser.current = user?.sub || null;
      const sessionView = resetSessionViewState();
      queryLifecycle.current = beginQuery(queryLifecycle.current);
      setActiveView(sessionView.activeView);
      setStatuses(sessionView.statuses);
      setQuery(EMPTY_QUERY);
      setSelectedFileIds([]);
      setQueryState("idle");
      setDescriptors(clearQueryDescriptors());
    }
  }, [user?.sub]);

  // The Cognito callback route must win over every other view, including the
  // public landing page, or the hosted-UI redirect would be swallowed.
  if (authRoute === "callback") {
    return <div className="auth-callback-shell"><AuthCallback /></div>;
  }

  if (!user) {
    return <LandingPage sessionReason={sessionReason} />;
  }

  function startQuery(descriptor) {
    setSelectedFileIds(beginQuerySelection());
    setDescriptors((current) => beginPendingQuery(current, descriptor));
    setQueryState("loading");
    setViewStatus("explore", { type: "info", message: "Searching the archive…" });
  }

  function completeQuery(nextQuery, nextStatus, descriptor) {
    setQuery(nextQuery);
    setDescriptors((current) => settleQuerySuccess(current, descriptor));
    setQueryState(nextQuery.count ? "ready" : "empty");
    setViewStatus("explore", nextStatus);
  }

  function failQuery(nextStatus) {
    setDescriptors((current) => settleQueryFailure(current));
    // Keep the previous successful results visible with their own descriptor.
    setQueryState(query.items.length ? "ready" : "error");
    setViewStatus("explore", nextStatus);
  }

  function clearQuery() {
    queryLifecycle.current = beginQuery(queryLifecycle.current);
    setQuery(EMPTY_QUERY);
    setQueryState("idle");
    setSelectedFileIds([]);
    setDescriptors(clearQueryDescriptors());
    setViewStatus("explore", null);
  }

  async function testAuth() {
    const sourceView = activeView;
    try {
      await getAuthTest();
      setViewStatus(sourceView, { type: "success", message: "Protected API is available." });
    } catch (error) {
      setViewStatus(sourceView, { type: "error", message: error.message });
    }
  }

  const manageCount = selectedMutationKeys(selectedFileIds, query.structuredItems).length;

  const diagnostics = demoMode ? (
    <details className="demo-diagnostics">
      <summary>Demo diagnostics</summary>
      <div>
        <span>{user.email || user["cognito:username"] || "Signed in"}</span>
        <code>Session {user.sub}</code>
        <button type="button" className="btn btn-secondary" onClick={testAuth}>Check auth</button>
      </div>
    </details>
  ) : null;

  return (
    <div className="app-shell">
      <AppHeader user={user} activeView={activeView} onNavigate={navigate} />
      <div className="app-body">
        <AppNav
          activeView={activeView}
          manageCount={manageCount}
          onNavigate={navigate}
          diagnostics={diagnostics}
        />
        <main ref={mainRef} className="app-main" tabIndex="-1">
          <StatusBanner status={statusForView(statuses, activeView)} />
          {activeView === "home" && <HomePanel onNavigate={navigate} />}
          {activeView === "explore" && (
            <ExplorePanel
              items={query.items}
              queryState={queryState}
              lastDescriptor={descriptors.lastSuccessfulDescriptor}
              pendingDescriptor={descriptors.pendingDescriptor}
              selectedFileIds={selectedFileIds}
              queryLifecycle={queryLifecycle}
              onQueryStart={startQuery}
              onQueryResult={completeQuery}
              onQueryError={failQuery}
              onClearQuery={clearQuery}
              onToggle={(fileId) => setSelectedFileIds((current) => toggleFileSelection(current, fileId))}
              onStatus={setExploreStatus}
              sessionKey={user.sub}
            />
          )}
          <div hidden={activeView !== "upload"}>
            <UploadPanel
              key={user.sub}
              active={activeView === "upload"}
              sessionKey={user.sub}
              getActiveSession={() => activeSession.current}
              onStatus={(status) => setUploadStatus(user.sub, status)}
              onNavigate={navigate}
            />
          </div>
          {activeView === "manage" && (
            <ManagePanel
              selectedFileIds={selectedFileIds}
              currentItems={query.structuredItems}
              onStatus={setManageStatus}
              onNavigate={navigate}
              onDeleted={(fileIds) => {
                const items = removeManagedItems(query.items, fileIds);
                const structuredItems = items.filter((item) => !item.legacy);
                const legacyItems = items.filter((item) => item.legacy);
                setQuery({ items, structuredItems, legacyItems, count: items.length });
                setSelectedFileIds((current) => current.filter((id) => !fileIds.includes(id)));
                setQueryState(items.length ? "ready" : "empty");
                setExploreStatus(queryStatusAfterDeletion(items.length));
              }}
            />
          )}
          {activeView === "notifications" && <NotificationsPanel onStatus={setNotificationsStatus} />}
        </main>
      </div>
    </div>
  );
}
