import assert from "node:assert/strict";
import test from "node:test";

test("management workflow rejects empty parsed tags and locks delete confirmation once", async () => {
  const { canSubmitTags, beginDeleteConfirmation, confirmDeleteOnce } = await import("./manageWorkflow.mjs").catch(() => ({}));
  assert.equal(canSubmitTags(", ,"), false);
  assert.equal(canSubmitTags("wombat, , dingo"), true);
  const pending = beginDeleteConfirmation({ open: false, pending: false });
  assert.equal(confirmDeleteOnce(pending).pending, true);
  assert.equal(confirmDeleteOnce(confirmDeleteOnce(pending)).pending, true);
});

test("management mutation state remains locked until it is settled", async () => {
  const { beginMutation, canStartMutation, finishMutation } = await import("./manageWorkflow.mjs");
  const pending = beginMutation({ pending: false });

  assert.equal(canStartMutation(pending), false);
  assert.equal(beginMutation(pending).pending, true);
  assert.equal(canStartMutation(finishMutation(pending)), true);
});

test("management effects require the same non-empty active session", async () => {
  const { canCommitManageEffect } = await import("./manageWorkflow.mjs");

  assert.equal(canCommitManageEffect("member-a", "member-a"), true);
  assert.equal(canCommitManageEffect("member-b", "member-a"), false);
  assert.equal(canCommitManageEffect("", ""), false);
  assert.equal(canCommitManageEffect(null, "member-a"), false);
});

test("deletion derives every result collection from the current query snapshot", async () => {
  const { removeManagedQueryItems } = await import("./manageWorkflow.mjs");
  const current = {
    items: [
      { file_id: "old", legacy: false },
      { file_id: "fresh", legacy: false },
      { file_id: null, legacy: true },
    ],
    structuredItems: [{ file_id: "stale-copy", legacy: false }],
    legacyItems: [],
    count: 99,
  };

  assert.deepEqual(removeManagedQueryItems(current, ["old"]), {
    items: [
      { file_id: "fresh", legacy: false },
      { file_id: null, legacy: true },
    ],
    structuredItems: [{ file_id: "fresh", legacy: false }],
    legacyItems: [{ file_id: null, legacy: true }],
    count: 2,
  });
});

test("rejects every A1 callback, updater, and finalizer after A returns as A2", async () => {
  const {
    canCommitManageEffect,
    finishMutationForSession,
    removeManagedQueryItemsForSession,
    removeManagedSelectionForSession,
  } = await import("./manageWorkflow.mjs");
  const { canCommitUploadEffect } = await import("./uploadWorkflow.mjs");
  const { advanceSessionIdentity } = await import("./viewState.mjs");
  const a1 = advanceSessionIdentity(undefined, "member-a");
  const b = advanceSessionIdentity(a1, "member-b");
  const a2 = advanceSessionIdentity(b, "member-a");
  const query = { items: [{ file_id: "file-1", legacy: false }] };
  const selection = ["file-1"];
  const pending = { pending: true };

  assert.equal(canCommitManageEffect(a2.key, a1.key), false);
  assert.equal(canCommitUploadEffect({
    mounted: true,
    activeSession: a2.key,
    sourceSession: a1.key,
    currentRun: 1,
    sourceRun: 1,
  }), false);
  assert.strictEqual(
    removeManagedQueryItemsForSession(query, ["file-1"], a2.key, a1.key),
    query,
  );
  assert.strictEqual(
    removeManagedSelectionForSession(selection, ["file-1"], a2.key, a1.key),
    selection,
  );
  assert.strictEqual(finishMutationForSession(pending, a2.key, a1.key), pending);
});
