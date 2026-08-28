import assert from "node:assert/strict";
import test from "node:test";

async function loadFilePickerState() {
  return import("./filePickerState.mjs").catch(() => ({}));
}

test("selecting a file retains it and advances the input revision", async () => {
  const { selectFilePickerFile = () => null } = await loadFilePickerState();
  const file = { name: "wombat.jpg", type: "image/jpeg" };

  assert.deepEqual(
    selectFilePickerFile({ file: null, inputRevision: 0 }, file),
    { file, inputRevision: 1 },
  );
});

test("clearing a selection removes the file and advances the input revision", async () => {
  const { clearFilePickerFile = () => null } = await loadFilePickerState();
  const file = { name: "wombat.jpg", type: "image/jpeg" };

  assert.deepEqual(
    clearFilePickerFile({ file, inputRevision: 4 }),
    { file: null, inputRevision: 5 },
  );
});

test("uses a visible picker frame only for the explicit dropzone variant", async () => {
  const { filePickerClassName } = await loadFilePickerState();

  assert.equal(filePickerClassName(), "file-picker");
  assert.equal(filePickerClassName("compact"), "file-picker");
  assert.equal(filePickerClassName("dropzone"), "file-picker file-picker-dropzone");
});
