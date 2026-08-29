import { useEffect, useRef, useState } from "react";
import { listNotifications, listSubscriptions, subscribeToSpecies, unsubscribeFromSpecies } from "../../api/mediaApi";
import Field from "../../components/Field";
import { beginNotificationRefresh, commitNotificationEffect, loadNotificationSnapshot, notificationMutationStatus, notificationPresentation, notificationRefreshFailureCopy, settleNotificationRefresh, shortFileId } from "../../lib/notificationState.mjs";

export default function NotificationsPanel({ getActiveSession, onStatus, sessionKey }) {
  const [species, setSpecies] = useState("");
  const [subscriptions, setSubscriptions] = useState([]);
  const [state, setState] = useState({ generation: 0, phase: "idle", items: [] });
  const [busy, setBusy] = useState(false);
  const current = useRef(state);
  const mounted = useRef(true);

  function commitForSession(sourceSession, commit) {
    if (!mounted.current || sourceSession !== sessionKey) return false;
    return commitNotificationEffect(getActiveSession?.(), sourceSession, commit);
  }

  async function refresh(sourceSession = sessionKey) {
    if (!commitForSession(sourceSession, () => {})) {
      return { ok: false, stale: true };
    }
    const started = beginNotificationRefresh(current.current);
    current.current = started;
    setState(started);
    const result = await loadNotificationSnapshot({ listSubscriptions, listNotifications });
    if (!commitForSession(sourceSession, () => {})) {
      return { ok: false, stale: true };
    }
    if (result.ok) {
      const next = settleNotificationRefresh(current.current, started.generation, result.items);
      if (next === current.current) {
        return { ok: false, error: new Error("Notification refresh was superseded.") };
      }
      commitForSession(sourceSession, () => {
        current.current = next;
        setSubscriptions(result.subscriptions);
        setState(next);
      });
      return result;
    }

    const next = settleNotificationRefresh(current.current, started.generation, [], "error");
    if (next !== current.current) {
      commitForSession(sourceSession, () => {
        current.current = next;
        setState(next);
        onStatus(sourceSession, { type: "error", message: result.error?.message || "Notifications could not be refreshed." });
      });
    }
    return result;
  }

  useEffect(() => {
    mounted.current = true;
    refresh(sessionKey);
    return () => {
      mounted.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function subscribe(event) {
    event.preventDefault();
    if (busy || !species.trim()) return;
    const sourceSession = sessionKey;
    setBusy(true);
    try {
      await subscribeToSpecies(species.trim().toLowerCase());
      if (!commitForSession(sourceSession, () => setSpecies(""))) return;
      const refreshResult = await refresh(sourceSession);
      commitForSession(sourceSession, () => (
        onStatus(sourceSession, notificationMutationStatus("Subscription saved.", refreshResult))
      ));
    } catch (error) {
      commitForSession(sourceSession, () => (
        onStatus(sourceSession, { type: "error", message: error.message })
      ));
    } finally {
      commitForSession(sourceSession, () => setBusy(false));
    }
  }

  async function unsubscribe(value) {
    if (busy) return;
    const sourceSession = sessionKey;
    setBusy(true);
    try {
      await unsubscribeFromSpecies(value);
      if (!commitForSession(sourceSession, () => {})) return;
      const refreshResult = await refresh(sourceSession);
      commitForSession(sourceSession, () => (
        onStatus(sourceSession, notificationMutationStatus(`Stopped watching ${value}.`, refreshResult))
      ));
    } catch (error) {
      commitForSession(sourceSession, () => (
        onStatus(sourceSession, { type: "error", message: error.message })
      ));
    } finally {
      commitForSession(sourceSession, () => setBusy(false));
    }
  }

  return (
    <div className="notifications-view">
      <header className="page-head">
        <p className="eyebrow">Species alerts</p>
        <h1>Notifications</h1>
      </header>
      <section className="workspace-grid">
      <section className="panel">
        <div className="panel-title">
          <div>
            <p className="eyebrow">Watch a species</p>
            <h2>Subscriptions</h2>
          </div>
          <span>{subscriptions.length} active</span>
        </div>
        <form className="inline-form" onSubmit={subscribe}>
          <Field label="Species">
            <input value={species} onChange={(event) => setSpecies(event.target.value)} />
          </Field>
          <button type="submit" className="btn btn-primary" disabled={busy || !species.trim()}>Subscribe</button>
        </form>
        <div className="pill-list">
          {subscriptions.map((item) => (
            <button key={item} type="button" aria-label={`Unsubscribe from ${item}`} disabled={busy} onClick={() => unsubscribe(item)}>
              {item} ×
            </button>
          ))}
          {!subscriptions.length && state.phase === "loading" && (
            <p className="empty-state">Loading subscriptions…</p>
          )}
          {state.phase === "error" && (
            <p className="empty-state">
              {notificationRefreshFailureCopy("Subscriptions", subscriptions.length > 0)}
            </p>
          )}
          {!subscriptions.length && state.phase === "ready" && (
            <div className="notification-empty">
              <strong>Build a species watchlist</strong>
              <p>Enter a model species label above. You can remove a subscription at any time.</p>
            </div>
          )}
        </div>
      </section>
      <section className="panel">
        <div className="panel-title">
          <div>
            <p className="eyebrow">Recent activity</p>
            <h2>Notifications</h2>
          </div>
          <button type="button" className="btn btn-secondary" disabled={busy || state.phase === "loading"} onClick={() => refresh(sessionKey)}>Refresh</button>
        </div>
        {state.phase === "loading" && <p className="empty-state">Loading notifications…</p>}
        {state.phase === "error" && (
          <p className="empty-state">
            {notificationRefreshFailureCopy("Notifications", state.items.length > 0)}
          </p>
        )}
        <div className="notification-list">
          {state.items.map((item) => {
            const visible = notificationPresentation(item);
            return (
              <article key={visible.notification_id} className="notification-item">
                <strong>{visible.species}</strong>
                <span>A matching archive item is available.</span>
                <span>File {shortFileId(visible.file_id)}</span>
                <time dateTime={visible.created_at}>{new Date(visible.created_at).toLocaleString()}</time>
              </article>
            );
          })}
          {state.phase === "ready" && !state.items.length && (
            <div className="notification-empty delivery-protocol">
              <p className="eyebrow">Delivery protocol</p>
              <ol>
                <li><span>01</span><b>Subscribe to a species</b></li>
                <li><span>02</span><b>A matching upload completes</b></li>
                <li><span>03</span><b>The alert appears here</b></li>
              </ol>
              <p>No matching activity has arrived yet.</p>
            </div>
          )}
        </div>
      </section>
      </section>
    </div>
  );
}
