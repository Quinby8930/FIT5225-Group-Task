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
