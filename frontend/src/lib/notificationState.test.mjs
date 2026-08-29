import assert from "node:assert/strict";
import test from "node:test";

test("normalizes notification fields and accepts only the latest refresh generation", async () => {
  const { beginNotificationRefresh, settleNotificationRefresh, normalizeNotifications } = await import("./notificationState.mjs").catch(() => ({}));
  const first = beginNotificationRefresh({ generation: 0, phase: "idle", items: [] });
  const second = beginNotificationRefresh(first);
  assert.equal(settleNotificationRefresh(second, first.generation, []).generation, second.generation);
  assert.deepEqual(normalizeNotifications({ notifications: [{ notification_id: "n", species: "wombat", file_id: "abcdef123", created_at: "2026-01-01T00:00:00Z", object_key: " originals/u/a.jpg " }, null] }), [{ notification_id: "n", species: "wombat", file_id: "abcdef123", created_at: "2026-01-01T00:00:00.000Z", object_key: "originals/u/a.jpg" }]);
  assert.deepEqual(normalizeNotifications({ notifications: [{ notification_id: "", species: "wombat", file_id: "f", created_at: "2026-01-01" }, { notification_id: "n", species: " ", file_id: "f", created_at: "2026-01-01" }, { notification_id: "n", species: "wombat", file_id: "f", created_at: "" }, { notification_id: "n", species: "wombat", file_id: "f", created_at: null }] }), []);
});

test("failed notification refresh preserves the prior items", async () => {
  const { beginNotificationRefresh, settleNotificationRefresh } = await import("./notificationState.mjs");
  const priorItems = [{ notification_id: "existing" }];
  const started = beginNotificationRefresh({ generation: 2, phase: "ready", items: priorItems });

  assert.deepEqual(settleNotificationRefresh(started, started.generation, [], "error"), {
    generation: 3,
    phase: "error",
    items: priorItems,
  });
});

test("notification refresh explicitly reports a complete snapshot or a failure", async () => {
  const { loadNotificationSnapshot } = await import("./notificationState.mjs");
  const success = await loadNotificationSnapshot({
    listSubscriptions: async () => ({ species: ["wombat", 17] }),
    listNotifications: async () => ({ notifications: [{
      notification_id: "n-1",
      species: "wombat",
      file_id: "file-1",
      created_at: "2026-01-01T00:00:00Z",
    }] }),
  });
  assert.equal(success.ok, true);
  assert.deepEqual(success.subscriptions, ["wombat"]);
  assert.equal(success.items.length, 1);

  const error = new Error("refresh offline");
  const failure = await loadNotificationSnapshot({
    listSubscriptions: async () => ({ species: ["existing"] }),
    listNotifications: async () => { throw error; },
  });
  assert.deepEqual(failure, { ok: false, error });
});

test("successful notification mutation with failed refresh reports partial success as an error", async () => {
  const { notificationMutationStatus } = await import("./notificationState.mjs");
  const status = notificationMutationStatus("Subscription saved.", {
    ok: false,
    error: new Error("refresh offline"),
  });

  assert.deepEqual(status, {
    type: "error",
    message: "Subscription saved, but notification data could not be refreshed: refresh offline",
  });
  assert.deepEqual(notificationMutationStatus("Subscription saved.", { ok: true }), {
    type: "success",
    message: "Subscription saved.",
  });
});

test("notification presentation omits the internally retained object key", async () => {
  const { notificationPresentation } = await import("./notificationState.mjs");
  const internal = {
    notification_id: "n-1",
    species: "wombat",
    file_id: "file-1",
    created_at: "2026-01-01T00:00:00.000Z",
    object_key: "originals/private/file-1/wombat.jpg",
  };

  assert.deepEqual(notificationPresentation(internal), {
    notification_id: "n-1",
    species: "wombat",
    file_id: "file-1",
    created_at: "2026-01-01T00:00:00.000Z",
  });
  assert.equal(Object.hasOwn(notificationPresentation(internal), "object_key"), false);
});

test("refresh failure copy distinguishes initial failure from preserved data", async () => {
  const { notificationRefreshFailureCopy } = await import("./notificationState.mjs");

  assert.equal(
    notificationRefreshFailureCopy("Notifications", false),
    "Notifications could not be loaded.",
  );
  assert.equal(
    notificationRefreshFailureCopy("Notifications", true),
    "Notifications could not be refreshed; showing previously loaded notifications.",
  );
  assert.equal(
    notificationRefreshFailureCopy("Subscriptions", true),
    "Subscriptions could not be refreshed; showing previously loaded subscriptions.",
  );
});

test("ignores an old notification panel callback after a session switch", async () => {
  const { commitNotificationEffect } = await import("./notificationState.mjs");
  assert.equal(typeof commitNotificationEffect, "function");
  const committed = [];
  const oldCallback = () => committed.push("A1 status");

  assert.equal(commitNotificationEffect("session-2", "session-1", oldCallback), false);
  assert.equal(commitNotificationEffect("session-3", "session-1", oldCallback), false);
  assert.deepEqual(committed, []);
  assert.equal(
    commitNotificationEffect("session-3", "session-3", () => committed.push("A2 status")),
    true,
  );
  assert.deepEqual(committed, ["A2 status"]);
});
