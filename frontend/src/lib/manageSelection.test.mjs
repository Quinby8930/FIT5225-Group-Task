import assert from "node:assert/strict";
import test from "node:test";

async function loadManageSelection() {
  return import("./manageSelection.mjs").catch(() => ({}));
}

test("starts each query with no retained file selection", async () => {
  const { beginQuerySelection } = await loadManageSelection();
  assert.equal(typeof beginQuerySelection, "function");
  assert.deepEqual(beginQuerySelection(["old-file", "old-file-2"]), []);
  assert.deepEqual(beginQuerySelection(new Set(["old-file"])), []);
});

test("reconciles selections to current structured file ids only", async () => {
  const { reconcileSelection, toggleFileSelection } = await loadManageSelection();
  assert.equal(typeof reconcileSelection, "function");
  assert.equal(typeof toggleFileSelection, "function");
  const items = [{ file_id: "a" }, { file_id: "b" }, { file_id: null, legacy: true }];

  assert.deepEqual(reconcileSelection(["old", "b", "b", "a"], items), ["b", "a"]);
  assert.deepEqual(toggleFileSelection(["a"], "b"), ["a", "b"]);
  assert.deepEqual(toggleFileSelection(["a", "b"], "a"), ["b"]);
});

test("maps mutations exclusively from selected manageable structured original keys", async () => {
  const { selectedMutationKeys } = await loadManageSelection();
  assert.equal(typeof selectedMutationKeys, "function");
  const keys = selectedMutationKeys(["image", "video", "legacy"], [
    {
      file_id: "image", can_manage: true,
      original_key: "originals/u/image/wombat.jpg",
      display_key: "thumbnails/u/image/thumbnail.jpg",
    },
    {
      file_id: "video", can_manage: false,
      original_key: "originals/u/video/cassowary.mp4",
    },
    {
      file_id: "legacy", can_manage: true, legacy: true,
      original_key: "https://signed.example/should-not-pass",
    },
    {
      file_id: "unselected", can_manage: true,
      original_key: "originals/u/unselected/fox.jpg",
    },
  ]);

  assert.deepEqual(keys, ["originals/u/image/wombat.jpg"]);
});
