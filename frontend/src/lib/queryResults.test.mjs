import assert from "node:assert/strict";
import test from "node:test";
import { selectedMutationKeys } from "./manageSelection.mjs";

async function loadQueryResults() {
  return import("./queryResults.mjs").catch(() => ({}));
}

test("normalizes structured query items with file_id identity and no inferred authority", async () => {
  const { normalizeQueryResponse } = await loadQueryResults();
  assert.equal(typeof normalizeQueryResponse, "function");

  const result = normalizeQueryResponse({
    items: [{
      file_id: "file-image",
      file_type: "image",
      display_key: "thumbnails/other/file-image/thumbnail.jpg",
      original_key: "originals/other/file-image/wombat.jpg",
      thumbnail_key: "thumbnails/other/file-image/thumbnail.jpg",
      can_preview: true,
      can_manage: "truthy but not authority",
      ignored_server_field: "must not become UI data",
    }, {
      file_id: "file-video",
      file_type: "video",
      display_key: "originals/other/file-video/cassowary.mp4",
      original_key: "originals/other/file-video/cassowary.mp4",
      thumbnail_key: null,
      can_preview: false,
      can_manage: true,
    }],
    results: [
      "thumbnails/other/file-image/thumbnail.jpg",
      "originals/other/file-video/cassowary.mp4",
    ],
  });

  assert.deepEqual(result.structuredItems, [{
    identity: "file-image",
    file_id: "file-image",
    file_type: "image",
    display_key: "thumbnails/other/file-image/thumbnail.jpg",
    original_key: "originals/other/file-image/wombat.jpg",
    thumbnail_key: "thumbnails/other/file-image/thumbnail.jpg",
    can_preview: true,
    can_manage: false,
    tags: {},
    detections: [],
    model_version: "",
    legacy: false,
  }, {
    identity: "file-video",
    file_id: "file-video",
    file_type: "video",
    display_key: "originals/other/file-video/cassowary.mp4",
    original_key: "originals/other/file-video/cassowary.mp4",
    thumbnail_key: null,
    can_preview: false,
    can_manage: true,
    tags: {},
    detections: [],
    model_version: "",
    legacy: false,
  }]);
  assert.deepEqual(result.legacyItems, []);
});

test("uses safe indexed legacy fallbacks only for result keys absent from structured items", async () => {
  const { normalizeQueryResponse } = await loadQueryResults();
  assert.equal(typeof normalizeQueryResponse, "function");

  const result = normalizeQueryResponse({
    items: [{
      file_id: "file-1",
      file_type: "image",
      display_key: "thumbnails/u/file-1/thumbnail.jpg",
      original_key: "originals/u/file-1/a.jpg",
      thumbnail_key: "thumbnails/u/file-1/thumbnail.jpg",
      can_preview: true,
      can_manage: true,
    }, { file_id: "bad", file_type: "audio" }],
    results: [
      "thumbnails/u/file-1/thumbnail.jpg",
      "legacy/unknown.bin",
      "legacy/unknown.bin",
      17,
    ],
  });

  assert.deepEqual(result.legacyItems, [{
    identity: "legacy:1:legacy/unknown.bin",
    file_id: null,
    file_type: null,
    display_key: "legacy/unknown.bin",
    original_key: null,
    thumbnail_key: null,
    can_preview: false,
    can_manage: false,
    tags: {},
    detections: [],
    model_version: "",
    legacy: true,
  }, {
    identity: "legacy:2:legacy/unknown.bin",
    file_id: null,
    file_type: null,
    display_key: "legacy/unknown.bin",
    original_key: null,
    thumbnail_key: null,
    can_preview: false,
    can_manage: false,
    tags: {},
    detections: [],
    model_version: "",
    legacy: true,
  }]);
  assert.equal(result.items.length, 3);
});

test("treats malformed query responses as an empty result set", async () => {
  const { normalizeQueryResponse } = await loadQueryResults();
  assert.equal(typeof normalizeQueryResponse, "function");
  assert.deepEqual(normalizeQueryResponse(null), {
    items: [], structuredItems: [], legacyItems: [], count: 0,
  });
});

test("keeps only the first valid structured item for each file id", async () => {
  const { normalizeQueryResponse } = await loadQueryResults();
  const result = normalizeQueryResponse({ items: [
    { file_id: "same", file_type: "image", display_key: "thumbnails/u/same/thumbnail.jpg", original_key: "originals/u/same/first.jpg", thumbnail_key: "thumbnails/u/same/thumbnail.jpg", can_preview: true, can_manage: true },
    { file_id: "same", file_type: "image", display_key: "thumbnails/u/same/other.jpg", original_key: "originals/u/same/second.jpg", thumbnail_key: "thumbnails/u/same/other.jpg", can_preview: true, can_manage: true },
  ] });
  assert.deepEqual(result.structuredItems.map((item) => item.original_key), ["originals/u/same/first.jpg"]);
  assert.deepEqual(selectedMutationKeys(["same"], result.structuredItems), ["originals/u/same/first.jpg"]);
});

test("does not append a legacy original when a structured thumbnail lookup item covers it", async () => {
  const { normalizeQueryResponse } = await loadQueryResults();
  const result = normalizeQueryResponse({ results: ["originals/u/f/wombat.jpg"], items: [{ file_id: "f", file_type: "image", display_key: "thumbnails/u/f/thumbnail.jpg", original_key: "originals/u/f/wombat.jpg", thumbnail_key: "thumbnails/u/f/thumbnail.jpg", can_preview: true, can_manage: false }] });
  assert.equal(result.structuredItems.length, 1);
  assert.deepEqual(result.legacyItems, []);
});

test("raw media keys are displayable only with explicit management permission", async () => {
  const { canRenderRawMediaKey } = await loadQueryResults();

  assert.equal(canRenderRawMediaKey({ can_manage: true }), true);
  assert.equal(canRenderRawMediaKey({ can_manage: false }), false);
  assert.equal(canRenderRawMediaKey({ can_manage: 1 }), false);
  assert.equal(canRenderRawMediaKey(null), false);
});

test("keeps current tags and original AI detections as separate safe details", async () => {
  const {
    canRenderRawMediaKey,
    mediaTechnicalDetails,
    normalizeQueryResponse,
  } = await loadQueryResults();
  assert.equal(typeof mediaTechnicalDetails, "function");
  const result = normalizeQueryResponse({
    items: [{
      file_id: "read-only-rat",
      file_type: "image",
      display_key: "thumbnails/u2/read-only-rat.jpg",
      original_key: "originals/u2/read-only-rat.jpg",
      thumbnail_key: "thumbnails/u2/read-only-rat.jpg",
      can_preview: true,
      can_manage: false,
      tags: { rat: 1 },
      detections: [{
        species: "cat",
        confidence: 0.677265,
        bbox: [0.1, 0.2, 0.3, 0.4],
      }],
      model_version: "v1",
      owner: "must-not-survive",
      checksum: "must-not-survive",
      filename: "must-not-survive.jpg",
    }],
  });

  const item = result.structuredItems[0];
  const details = mediaTechnicalDetails(item);

  assert.deepEqual(details, {
    tagRows: [{ species: "rat", count: 1, label: "rat × 1" }],
    detectionRows: [{
      species: "cat",
      confidence: 0.677265,
      label: "cat — model score 67.73%",
    }],
    detectionNote: null,
    modelVersion: "v1",
    notice: "AI-generated result; it may be incorrect. Archive tags can be corrected by the owner.",
    hasDetails: true,
    hasAiDetails: true,
  });
  assert.equal(canRenderRawMediaKey(item), false);
  assert.deepEqual(
    Object.keys(item).filter((key) => ["owner", "checksum", "filename"].includes(key)),
    [],
  );
});

test("groups video sampled-frame detections by species with occurrence counts and maxima", async () => {
  const { mediaTechnicalDetails, normalizeQueryResponse } = await loadQueryResults();
  const result = normalizeQueryResponse({
    items: [{
      file_id: "video-slideshow",
      file_type: "video",
      display_key: "originals/u/video-slideshow/species.mp4",
      original_key: "originals/u/video-slideshow/species.mp4",
      thumbnail_key: null,
      can_preview: true,
      can_manage: true,
      tags: { cassowary: 1, dingo: 1 },
      detections: [
        { species: "cassowary", confidence: 0.8 },
        { species: "dingo", confidence: 0.92 },
        { species: "cassowary", confidence: 1 },
      ],
      model_version: "v1",
    }],
  });

  const details = mediaTechnicalDetails(result.structuredItems[0]);

  assert.deepEqual(details.detectionRows, [{
    species: "cassowary",
    confidence: 1,
    occurrences: 2,
    label: "cassowary — 2 sampled-frame detections, max model score 100.00%",
  }, {
    species: "dingo",
    confidence: 0.92,
    occurrences: 1,
    label: "dingo — 1 sampled-frame detection, max model score 92.00%",
  }]);
  assert.equal(
    details.detectionNote,
    "Sampled-frame detections are model evidence, not counts of individual animals.",
  );
});

test("keeps image detections as separate rows even when their species repeats", async () => {
  const { mediaTechnicalDetails } = await loadQueryResults();

  const details = mediaTechnicalDetails({
    file_type: "image",
    detections: [
      { species: "wombat", confidence: 0.7 },
      { species: "wombat", confidence: 0.9 },
    ],
  });

  assert.deepEqual(details.detectionRows.map(({ label }) => label), [
    "wombat — model score 70.00%",
    "wombat — model score 90.00%",
  ]);
  assert.equal(details.detectionNote, null);
});

test("missing or malformed ML details degrade without undefined placeholders", async () => {
  const { mediaTechnicalDetails, normalizeQueryResponse } = await loadQueryResults();
  assert.equal(typeof mediaTechnicalDetails, "function");
  const result = normalizeQueryResponse({
    items: [{
      file_id: "legacy",
      file_type: "image",
      display_key: "thumbnails/u2/legacy.jpg",
      original_key: "originals/u2/legacy.jpg",
      thumbnail_key: "thumbnails/u2/legacy.jpg",
      can_preview: true,
      can_manage: false,
      tags: { wombat: 2, zero: 0, boolean: true },
      detections: [{ species: "cat", confidence: Number.NaN }],
      model_version: { unexpected: "shape" },
    }],
  });

  const details = mediaTechnicalDetails(result.structuredItems[0]);

  assert.deepEqual(details, {
    tagRows: [{ species: "wombat", count: 2, label: "wombat × 2" }],
    detectionRows: [],
    detectionNote: null,
    modelVersion: null,
    notice: null,
    hasDetails: true,
    hasAiDetails: false,
  });
  assert.equal(JSON.stringify(details).includes("undefined"), false);
});

test("legacy result labels do not expose the normalized storage key", async () => {
  const { legacyReferenceLabel, normalizeQueryResponse } = await loadQueryResults();
  const result = normalizeQueryResponse({ results: ["legacy/private/storage-key.jpg"] });

  assert.equal(result.legacyItems[0].display_key, "legacy/private/storage-key.jpg");
  assert.equal(legacyReferenceLabel(0), "Legacy reference 1");
  assert.equal(legacyReferenceLabel(0).includes(result.legacyItems[0].display_key), false);
});
