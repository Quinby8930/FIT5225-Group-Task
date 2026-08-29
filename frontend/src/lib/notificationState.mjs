export function beginNotificationRefresh(previous = {}) { return { generation: (previous.generation || 0) + 1, phase: "loading", items: previous.items || [] }; }
export function settleNotificationRefresh(current, generation, items, phase = "ready") {
  if (current.generation !== generation) return current;
  return { generation, phase, items: phase === "error" ? current.items || [] : items };
}
export function normalizeNotifications(data) {
  return (Array.isArray(data?.notifications) ? data.notifications : []).flatMap((item) => {
    if (!item || typeof item !== "object" || typeof item.notification_id !== "string" || !item.notification_id.trim() || typeof item.species !== "string" || !item.species.trim() || typeof item.file_id !== "string" || !item.file_id.trim() || typeof item.created_at !== "string" || !item.created_at.trim()) return [];
    const timestamp = new Date(item.created_at);
    if (Number.isNaN(timestamp.valueOf())) return [];
    return [{
      notification_id: item.notification_id.trim(),
      species: item.species.trim(),
      file_id: item.file_id.trim(),
      created_at: timestamp.toISOString(),
      object_key: typeof item.object_key === "string" && item.object_key.trim()
        ? item.object_key.trim()
        : null,
    }];
  });
}
export function shortFileId(value) { return typeof value === "string" ? value.slice(0, 8) : "unknown"; }

export function notificationPresentation(item = {}) {
  return {
    notification_id: item.notification_id,
    species: item.species,
    file_id: item.file_id,
    created_at: item.created_at,
  };
}

export function notificationRefreshFailureCopy(collection, hasPreviousData) {
  return hasPreviousData
    ? `${collection} could not be refreshed; showing previously loaded ${collection.toLowerCase()}.`
    : `${collection} could not be loaded.`;
}

export async function loadNotificationSnapshot({ listSubscriptions, listNotifications }) {
  try {
    const [subscriptionsResponse, notificationsResponse] = await Promise.all([
      listSubscriptions(),
      listNotifications(),
    ]);
    return {
      ok: true,
      subscriptions: Array.isArray(subscriptionsResponse?.species)
        ? subscriptionsResponse.species.filter((value) => typeof value === "string")
        : [],
      items: normalizeNotifications(notificationsResponse),
    };
  } catch (error) {
    return { ok: false, error };
  }
}

export function notificationMutationStatus(successMessage, refreshResult) {
  if (refreshResult?.ok) return { type: "success", message: successMessage };
  const detail = refreshResult?.error?.message || "Unknown refresh error";
  return {
    type: "error",
    message: `${successMessage.replace(/\.$/, "")}, but notification data could not be refreshed: ${detail}`,
  };
}

export function commitNotificationEffect(activeSession, sourceSession, commit) {
  if (
    typeof activeSession !== "string"
    || !activeSession
    || activeSession !== sourceSession
    || typeof commit !== "function"
  ) return false;
  commit();
  return true;
}
