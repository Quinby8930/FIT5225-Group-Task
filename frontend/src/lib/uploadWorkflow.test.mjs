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

test("accepts exact upload limits and rejects the next byte for each supported media family", async () => {
  const { validateUploadFile } = await import("../api/mediaApi.js");
  const cases = [
    ["image/jpeg", 12_582_912, "Images must be 12 MiB or smaller."],
    ["image/png", 12_582_912, "Images must be 12 MiB or smaller."],
    ["image/webp", 12_582_912, "Images must be 12 MiB or smaller."],
    ["video/mp4", 262_144_000, "Videos must be 250 MiB or smaller."],
    ["video/quicktime", 262_144_000, "Videos must be 250 MiB or smaller."],
  ];

  for (const [type, limit, message] of cases) {
    assert.doesNotThrow(() => validateUploadFile({ type, size: limit }));
    assert.throws(() => validateUploadFile({ type, size: limit + 1 }), { message });
  }
});

test("rejects invalid upload MIME and size before hashing, status, or network work", async () => {
  const { uploadMedia } = await import("../api/mediaApi.js");
  const originalFetch = globalThis.fetch;
  let arrayBufferCalls = 0;
  let fetchCalls = 0;
  const stages = [];
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error("fetch must not run");
  };

  try {
    for (const file of [
      { name: "too-large.jpg", type: "image/jpeg", size: 12_582_913 },
      { name: "too-large.mov", type: "video/quicktime", size: 262_144_001 },
      { name: "looks-like-an-image.jpg", type: "", size: 100 },
      { name: "unknown.jpg", type: "application/octet-stream", size: 100 },
    ]) {
      file.arrayBuffer = async () => {
        arrayBufferCalls += 1;
        return new ArrayBuffer(0);
      };
      await assert.rejects(
        uploadMedia(file, { onStage: (stage) => stages.push(stage) }),
      );
    }
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(arrayBufferCalls, 0);
  assert.equal(fetchCalls, 0);
  assert.deepEqual(stages, []);
});
