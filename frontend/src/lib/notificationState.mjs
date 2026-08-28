export function beginNotificationRefresh(previous = {}) { return { generation: (previous.generation || 0) + 1, phase: "loading", items: previous.items || [] }; }
export function settleNotificationRefresh(current, generation, items, phase = "ready") { return current.generation === generation ? { generation, phase, items } : current; }
export function normalizeNotifications(data) {
  return (Array.isArray(data?.notifications) ? data.notifications : []).flatMap((item) => {
    if (!item || typeof item !== "object" || typeof item.notification_id !== "string" || !item.notification_id.trim() || typeof item.species !== "string" || !item.species.trim() || typeof item.file_id !== "string" || !item.file_id.trim() || typeof item.created_at !== "string" || !item.created_at.trim()) return [];
    const timestamp = new Date(item.created_at);
    if (Number.isNaN(timestamp.valueOf())) return [];
    return [{ notification_id: item.notification_id.trim(), species: item.species.trim(), file_id: item.file_id.trim(), created_at: timestamp.toISOString(), object_key: typeof item.object_key === "string" ? item.object_key : null }];
  });
}
export function shortFileId(value) { return typeof value === "string" ? value.slice(0, 8) : "unknown"; }
