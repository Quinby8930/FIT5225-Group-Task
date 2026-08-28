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
