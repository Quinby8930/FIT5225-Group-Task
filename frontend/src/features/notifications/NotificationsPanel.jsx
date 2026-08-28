import { useEffect, useRef, useState } from "react";
import { listNotifications, listSubscriptions, subscribeToSpecies, unsubscribeFromSpecies } from "../../api/mediaApi";
import Field from "../../components/Field";
import { beginNotificationRefresh, normalizeNotifications, settleNotificationRefresh, shortFileId } from "../../lib/notificationState.mjs";

export default function NotificationsPanel({ onStatus }) {
  const [species, setSpecies] = useState("");
  const [subscriptions, setSubscriptions] = useState([]);
  const [state, setState] = useState({ generation: 0, phase: "idle", items: [] });
  const [busy, setBusy] = useState(false);
  const current = useRef(state);

  async function refresh() {
    const started = beginNotificationRefresh(current.current);
    current.current = started;
    setState(started);
    try {
      const [subs, notes] = await Promise.all([listSubscriptions(), listNotifications()]);
      const items = normalizeNotifications(notes);
      const next = settleNotificationRefresh(current.current, started.generation, items);
      if (next === current.current) return;
      current.current = next;
      setSubscriptions(Array.isArray(subs?.species) ? subs.species.filter((value) => typeof value === "string") : []);
      setState(next);
    } catch (error) {
      const next = settleNotificationRefresh(current.current, started.generation, [], "error");
      if (next !== current.current) {
        current.current = next;
        setState(next);
        onStatus({ type: "error", message: error.message });
      }
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function subscribe(event) {
    event.preventDefault();
    if (busy || !species.trim()) return;
    setBusy(true);
    try {
      await subscribeToSpecies(species.trim().toLowerCase());
      setSpecies("");
      await refresh();
      onStatus({ type: "success", message: "Subscription saved." });
    } catch (error) {
      onStatus({ type: "error", message: error.message });
    } finally {
      setBusy(false);
    }
  }

  async function unsubscribe(value) {
    if (busy) return;
    setBusy(true);
    try {
      await unsubscribeFromSpecies(value);
      await refresh();
      onStatus({ type: "success", message: `Stopped watching ${value}.` });
    } catch (error) {
      onStatus({ type: "error", message: error.message });
    } finally {
      setBusy(false);
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
          {!subscriptions.length && state.phase === "error" && (
            <p className="empty-state">Subscriptions could not be loaded.</p>
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
          <button type="button" className="btn btn-secondary" disabled={busy || state.phase === "loading"} onClick={refresh}>Refresh</button>
        </div>
        {state.phase === "loading" && <p className="empty-state">Loading notifications…</p>}
        {state.phase === "error" && <p className="empty-state">Notifications could not be loaded.</p>}
        <div className="notification-list">
          {state.phase === "ready" && state.items.map((item) => (
            <article key={item.notification_id} className="notification-item">
              <strong>{item.species}</strong>
              <span>A matching archive item is available.</span>
              <span>File {shortFileId(item.file_id)}</span>
              <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time>
              {item.object_key && (
                <details>
                  <summary>Technical details</summary>
                  <code>{item.object_key}</code>
                </details>
              )}
            </article>
          ))}
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
