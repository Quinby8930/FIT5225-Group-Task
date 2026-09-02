import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
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
import { canCommitManageEffect, removeManagedQueryItemsForSession, removeManagedSelectionForSession } from "./lib/manageWorkflow.mjs";
import { beginQuery } from "./lib/queryLifecycle.mjs";
import { createExploreSpeciesRequest } from "./lib/duplicateUpload.mjs";
import {
  beginPendingQuery,
  clearQueryDescriptors,
  settleQueryFailure,
  settleQuerySuccess,
} from "./lib/queryDescriptor.mjs";
import { authRouteForPath } from "./lib/appRoutes.mjs";
import { advanceSessionIdentity, navigateToView, projectSessionViewState, queryStatusAfterDeletion, resetSessionViewState, setStatusForView, statusForView } from "./lib/viewState.mjs";
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
  const [requestedSpecies, setRequestedSpecies] = useState(null);
  const { user, reason: sessionReason } = useAuthSession();
  const subject = typeof user?.sub === "string" && user.sub.trim() ? user.sub : null;
  const sessionIdentity = useRef(null);
  if (!sessionIdentity.current || sessionIdentity.current.subject !== subject) {
    sessionIdentity.current = advanceSessionIdentity(sessionIdentity.current, subject);
  }
  const sessionKey = sessionIdentity.current.key;
  const [sessionStateOwner, setSessionStateOwner] = useState(sessionKey);
  const activeSession = useRef(sessionKey);
  activeSession.current = sessionKey;
  const mainRef = useRef(null);
  const queryLifecycle = useRef({ generation: 0, phase: "idle", result: null });
  const pendingDeletionFeedback = useRef(null);
  const exploreRequestId = useRef(0);
  const authRoute = authRouteForPath(window.location.pathname, appConfig);
  const demoMode = useMemo(
    () => Boolean(import.meta.env.DEV) || new URLSearchParams(window.location.search).has("demo"),
    []
  );
  const visibleSessionState = projectSessionViewState(
    sessionStateOwner,
    sessionKey,
    { activeView, statuses, query, queryState, descriptors, selectedFileIds },
  );
  const visibleActiveView = visibleSessionState.activeView;
  const visibleStatuses = visibleSessionState.statuses;
  const visibleQuery = visibleSessionState.query;
  const visibleQueryState = visibleSessionState.queryState;
  const visibleDescriptors = visibleSessionState.descriptors;
  const visibleSelectedFileIds = visibleSessionState.selectedFileIds;

  const setViewStatus = useCallback((view, status) => {
    setStatuses((current) => setStatusForView(current, view, status));
  }, []);
  const setExploreStatus = useCallback((sourceSession, status) => {
    if (sourceSession !== activeSession.current) return;
    setViewStatus("explore", status);
  }, [setViewStatus]);
  const setUploadStatus = useCallback((sourceSession, status) => {
    if (sourceSession !== activeSession.current) return;
    setViewStatus("upload", status);
  }, [setViewStatus]);
  const setManageStatus = useCallback((sourceSession, status) => {
    if (!canCommitManageEffect(activeSession.current, sourceSession)) return;
    setViewStatus("manage", status);
  }, [setViewStatus]);
  const setNotificationsStatus = useCallback((sourceSession, status) => {
    if (sourceSession !== activeSession.current) return;
    setViewStatus("notifications", status);
  }, [setViewStatus]);
  const navigate = useCallback((view) => {
    navigateToView(view, {
      setActiveView,
      scrollTo: window.scrollTo.bind(window),
    });
    window.requestAnimationFrame(() => mainRef.current?.focus({ preventScroll: true }));
  }, []);
  const exploreDuplicateSpecies = useCallback((sourceSession, species) => {
    if (sourceSession !== activeSession.current) return;
    const request = createExploreSpeciesRequest(
      exploreRequestId.current,
      sourceSession,
      species,
    );
    exploreRequestId.current = request.id;
    setRequestedSpecies(request);
    navigate("explore");
  }, [navigate]);

  useEffect(() => {
    document.title = VIEW_TITLES[visibleActiveView] || "Pacific BioArchive";
  }, [visibleActiveView]);

  useEffect(() => {
    if (authRoute === "logout") {
      window.history.replaceState({}, document.title, appConfig.homePath);
    }
  }, [authRoute]);

  useLayoutEffect(() => {
    if (sessionStateOwner !== sessionKey) {
      pendingDeletionFeedback.current = null;
      const sessionView = resetSessionViewState();
      queryLifecycle.current = beginQuery(queryLifecycle.current);
      setActiveView(sessionView.activeView);
      setStatuses(sessionView.statuses);
      setQuery(EMPTY_QUERY);
      setSelectedFileIds([]);
      setQueryState("idle");
      setDescriptors(clearQueryDescriptors());
      setRequestedSpecies(null);
      setSessionStateOwner(sessionKey);
    }
  }, [sessionKey, sessionStateOwner]);

  useEffect(() => {
    if (sessionStateOwner !== sessionKey) return;
    const feedback = pendingDeletionFeedback.current;
    if (!feedback) return;
    if (!canCommitManageEffect(activeSession.current, feedback.sourceSession)) {
      pendingDeletionFeedback.current = null;
      return;
    }
    pendingDeletionFeedback.current = null;
    setQueryState(query.items.length ? "ready" : "empty");
    setExploreStatus(sessionKey, queryStatusAfterDeletion(query.items.length));
  }, [query, sessionKey, sessionStateOwner, setExploreStatus]);

  // The Cognito callback route must win over every other view, including the
  // public landing page, or the hosted-UI redirect would be swallowed.
  if (authRoute === "callback") {
    return <div className="auth-callback-shell"><AuthCallback /></div>;
  }

  if (!user) {
    return <LandingPage sessionReason={sessionReason} />;
  }

  function startQuery(sourceSession, descriptor) {
    if (sourceSession !== activeSession.current) return;
    setSelectedFileIds(beginQuerySelection());
    setDescriptors((current) => beginPendingQuery(current, descriptor));
    setQueryState("loading");
    setViewStatus("explore", { type: "info", message: "Searching the archive…" });
  }

  function completeQuery(sourceSession, nextQuery, nextStatus, descriptor) {
    if (sourceSession !== activeSession.current) return;
    setQuery(nextQuery);
    setDescriptors((current) => settleQuerySuccess(current, descriptor));
    setQueryState(nextQuery.count ? "ready" : "empty");
    setViewStatus("explore", nextStatus);
  }

  function failQuery(sourceSession, nextStatus) {
    if (sourceSession !== activeSession.current) return;
    setDescriptors((current) => settleQueryFailure(current));
    // Keep the previous successful results visible with their own descriptor.
    setQueryState(query.items.length ? "ready" : "error");
    setViewStatus("explore", nextStatus);
  }

  function clearQuery(sourceSession) {
    if (sourceSession !== activeSession.current) return;
    queryLifecycle.current = beginQuery(queryLifecycle.current);
    setQuery(EMPTY_QUERY);
    setQueryState("idle");
    setSelectedFileIds([]);
    setDescriptors(clearQueryDescriptors());
    setViewStatus("explore", null);
  }

  async function testAuth() {
    const sourceView = visibleActiveView;
    const sourceSession = sessionKey;
    try {
      await getAuthTest();
      if (sourceSession !== activeSession.current) return;
      setViewStatus(sourceView, { type: "success", message: "Protected API is available." });
    } catch (error) {
      if (sourceSession !== activeSession.current) return;
      setViewStatus(sourceView, { type: "error", message: error.message });
    }
  }

  const manageCount = selectedMutationKeys(visibleSelectedFileIds, visibleQuery.structuredItems).length;

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
      <AppHeader user={user} activeView={visibleActiveView} onNavigate={navigate} />
      <div className="app-body">
        <AppNav
          activeView={visibleActiveView}
          manageCount={manageCount}
          onNavigate={navigate}
          diagnostics={diagnostics}
        />
        <main ref={mainRef} className="app-main" tabIndex="-1">
          <StatusBanner status={statusForView(visibleStatuses, visibleActiveView)} />
          {visibleActiveView === "home" && <HomePanel onNavigate={navigate} />}
          {visibleActiveView === "explore" && (
            <ExplorePanel
              key={sessionKey}
              items={visibleQuery.items}
              queryState={visibleQueryState}
              lastDescriptor={visibleDescriptors.lastSuccessfulDescriptor}
              pendingDescriptor={visibleDescriptors.pendingDescriptor}
              selectedFileIds={visibleSelectedFileIds}
              queryLifecycle={queryLifecycle}
              onQueryStart={(descriptor) => startQuery(sessionKey, descriptor)}
              onQueryResult={(nextQuery, nextStatus, descriptor) => (
                completeQuery(sessionKey, nextQuery, nextStatus, descriptor)
              )}
              onQueryError={(nextStatus) => failQuery(sessionKey, nextStatus)}
              onClearQuery={() => clearQuery(sessionKey)}
              onToggle={(fileId) => setSelectedFileIds((current) => (
                canCommitManageEffect(activeSession.current, sessionKey)
                  ? toggleFileSelection(current, fileId)
                  : current
              ))}
              onStatus={(status) => setExploreStatus(sessionKey, status)}
              sessionKey={sessionKey}
              requestedSpecies={requestedSpecies}
              onRequestedSpeciesConsumed={(requestId) => setRequestedSpecies(
                (current) => current?.id === requestId ? null : current,
              )}
            />
          )}
          <div hidden={visibleActiveView !== "upload"}>
            <UploadPanel
              key={sessionKey}
              active={visibleActiveView === "upload"}
              sessionKey={sessionKey}
              getActiveSession={() => activeSession.current}
              onStatus={(status) => setUploadStatus(sessionKey, status)}
              onNavigate={navigate}
              onExploreSpecies={(species) => exploreDuplicateSpecies(
                sessionKey,
                species,
              )}
            />
          </div>
          {visibleActiveView === "manage" && (
            <ManagePanel
              key={sessionKey}
              selectedFileIds={visibleSelectedFileIds}
              currentItems={visibleQuery.structuredItems}
              sessionKey={sessionKey}
              getActiveSession={() => activeSession.current}
              onStatus={setManageStatus}
              onNavigate={navigate}
              onDeleted={(sourceSession, fileIds) => {
                if (!canCommitManageEffect(activeSession.current, sourceSession)) return;
                pendingDeletionFeedback.current = { sourceSession };
                setQuery((current) => removeManagedQueryItemsForSession(
                  current,
                  fileIds,
                  activeSession.current,
                  sourceSession,
                ));
                setSelectedFileIds((current) => removeManagedSelectionForSession(
                  current,
                  fileIds,
                  activeSession.current,
                  sourceSession,
                ));
              }}
            />
          )}
          {visibleActiveView === "notifications" && (
            <NotificationsPanel
              key={sessionKey}
              sessionKey={sessionKey}
              getActiveSession={() => activeSession.current}
              onStatus={setNotificationsStatus}
            />
          )}
        </main>
      </div>
    </div>
  );
}
