import assert from "node:assert/strict";
import test from "node:test";

import {
  loadRecentUploads,
  mergeUploadStatus,
  pendingUploadIds,
  rememberRecentUpload,
  saveRecentUploads,
  uploadStatusView,
} from "./recentUploads.mjs";

function storage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

test("recent uploads survive refresh for the same authenticated user only", () => {
  const session = storage();
  const uploads = rememberRecentUpload([], {
    file_id: "file-1",
    filename: "cassowary.jpg",
    checksum: "checksum-1",
    file_type: "image",
    status: "pending_upload",
  });

  saveRecentUploads(session, "user-a", uploads);

  assert.deepEqual(loadRecentUploads(session, "user-a"), uploads);
  assert.deepEqual(loadRecentUploads(session, "user-b"), []);
});

test("malformed stored upload history is discarded without leaking across sessions", () => {
  const session = storage();
  session.setItem("pacificBioArchive.recentUploads", "not-json");
  assert.deepEqual(loadRecentUploads(session, "user-a"), []);

  session.setItem(
    "pacificBioArchive.recentUploads",
    JSON.stringify({
      subject: "user-a",
      uploads: [
        { file_id: "", filename: "bad.jpg", status: "processing" },
        { file_id: "file-2", filename: "boar.jpg", status: "unknown" },
      ],
    }),
  );
  assert.deepEqual(loadRecentUploads(session, "user-a"), []);
});

test("status polling merges safe completion details and stops terminal records", () => {
  const initial = rememberRecentUpload([], {
    file_id: "file-1",
    filename: "boar.jpg",
    checksum: "checksum-1",
    file_type: "image",
    status: "pending_upload",
  });

  const processing = mergeUploadStatus(initial, {
    file_id: "file-1",
    filename: "boar.jpg",
    file_type: "image",
    status: "processing",
    tags: {},
    detections: [],
    model_version: "",
    error_code: null,
    message: null,
    upload_time: "2026-09-02T06:10:00Z",
  });
  assert.deepEqual(pendingUploadIds(processing), ["file-1"]);

  const completed = mergeUploadStatus(processing, {
    file_id: "file-1",
    filename: "boar.jpg",
    file_type: "image",
    status: "completed",
    tags: { boar: 1 },
    detections: [
      { species: "boar", confidence: 0.991, bbox: [0, 0, 1, 1] },
    ],
    model_version: "v1",
    error_code: null,
    message: null,
    upload_time: "2026-09-02T06:10:00Z",
  });

  assert.deepEqual(pendingUploadIds(completed), []);
  assert.deepEqual(completed[0].tags, { boar: 1 });
  assert.deepEqual(completed[0].detections, [
    { species: "boar", confidence: 0.991 },
  ]);
  assert.equal(uploadStatusView(completed[0]).label, "Completed");
  assert.deepEqual(uploadStatusView(completed[0]).tagRows, ["boar × 1"]);
  assert.deepEqual(uploadStatusView(completed[0]).detectionRows, [
    "boar — model score 99.10%",
  ]);
});

test("failed uploads retain a bounded user-facing failure and stop polling", () => {
  const initial = rememberRecentUpload([], {
    file_id: "file-2",
    filename: "video.mp4",
    checksum: "checksum-2",
    file_type: "video",
    status: "processing",
  });

  const failed = mergeUploadStatus(initial, {
    file_id: "file-2",
    filename: "video.mp4",
    file_type: "video",
    status: "failed",
    tags: { unexpected: 1 },
    detections: [{ species: "unexpected", confidence: 0.5 }],
    model_version: "private-on-failure",
    error_code: "INFERENCE_UNAVAILABLE",
    message: "Processing could not reach the inference service.",
    upload_time: "2026-09-02T06:10:00Z",
  });

  assert.deepEqual(pendingUploadIds(failed), []);
  assert.deepEqual(failed[0].tags, {});
  assert.deepEqual(failed[0].detections, []);
  assert.equal(failed[0].model_version, "");
  assert.deepEqual(uploadStatusView(failed[0]), {
    label: "Processing failed",
    tagRows: [],
    detectionRows: [],
    modelVersion: "",
    failure: "Processing could not reach the inference service.",
  });
});
