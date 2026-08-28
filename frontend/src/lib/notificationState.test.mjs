import assert from "node:assert/strict";
import test from "node:test";

test("normalizes notification fields and accepts only the latest refresh generation", async () => {
  const { beginNotificationRefresh, settleNotificationRefresh, normalizeNotifications } = await import("./notificationState.mjs").catch(() => ({}));
  const first = beginNotificationRefresh({ generation: 0, phase: "idle", items: [] });
  const second = beginNotificationRefresh(first);
  assert.equal(settleNotificationRefresh(second, first.generation, []).generation, second.generation);
  assert.deepEqual(normalizeNotifications({ notifications: [{ notification_id: "n", species: "wombat", file_id: "abcdef123", created_at: "2026-01-01T00:00:00Z", object_key: "originals/u/a.jpg" }, null] }), [{ notification_id: "n", species: "wombat", file_id: "abcdef123", created_at: "2026-01-01T00:00:00.000Z", object_key: "originals/u/a.jpg" }]);
  assert.deepEqual(normalizeNotifications({ notifications: [{ notification_id: "", species: "wombat", file_id: "f", created_at: "2026-01-01" }, { notification_id: "n", species: " ", file_id: "f", created_at: "2026-01-01" }, { notification_id: "n", species: "wombat", file_id: "f", created_at: "" }, { notification_id: "n", species: "wombat", file_id: "f", created_at: null }] }), []);
});
