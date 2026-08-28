import assert from "node:assert/strict";
import test from "node:test";

test("does not allow the successfully uploaded file to be submitted again", async () => {
  const { canSubmitUpload, completeUpload, selectUploadFile, startUpload } = await import("./uploadWorkflow.mjs");
  const selected = selectUploadFile({}, { name: "wombat.jpg" });
  const submitting = startUpload(selected);
  const completed = completeUpload(submitting, { file_id: "file-1", checksum: "abc" });

  assert.equal(canSubmitUpload(completed), false);
});

test("selecting a new file clears the previous receipt and progress", async () => {
  const { selectUploadFile } = await import("./uploadWorkflow.mjs");
  const next = selectUploadFile(
    { file: { name: "old.jpg" }, stage: "queued", submitting: false, receipt: { file_id: "old" } },
    { name: "new.jpg" },
  );

  assert.equal(next.file.name, "new.jpg");
  assert.equal(next.stage, "");
  assert.equal(next.receipt, null);
});

test("accepts upload effects only from the mounted current run and session", async () => {
  const { canCommitUploadEffect } = await import("./uploadWorkflow.mjs");
  const current = {
    mounted: true,
    activeSession: "member-b",
    sourceSession: "member-b",
    currentRun: 3,
    sourceRun: 3,
  };

  assert.equal(canCommitUploadEffect(current), true);
  assert.equal(canCommitUploadEffect({ ...current, mounted: false }), false);
  assert.equal(canCommitUploadEffect({ ...current, activeSession: "member-a" }), false);
  assert.equal(canCommitUploadEffect({ ...current, sourceRun: 2 }), false);
});

test("pauses a local preview only when the Upload view becomes inactive", async () => {
  const { pauseInactivePreview } = await import("./uploadWorkflow.mjs");
  const calls = [];
  const media = { pause: () => calls.push("pause") };

  pauseInactivePreview(true, media);
  assert.deepEqual(calls, []);
  pauseInactivePreview(false, media);
  assert.deepEqual(calls, ["pause"]);
  assert.doesNotThrow(() => pauseInactivePreview(false, null));
});
