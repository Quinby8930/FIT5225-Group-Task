import assert from "node:assert/strict";
import test from "node:test";

async function loadCoordinator() {
  return import("./assetRequestCoordinator.mjs").catch(() => ({}));
}

test("drops an older same-dataset asset response after a newer refresh owns the key", async () => {
  const { beginAssetDataset, beginAssetRequest, filterLatestAssetResponse } = await loadCoordinator();
  assert.equal(typeof beginAssetDataset, "function");
  assert.equal(typeof beginAssetRequest, "function");
  assert.equal(typeof filterLatestAssetResponse, "function");

  let coordinator = beginAssetDataset({ datasetEpoch: 0, nextToken: 0, keyTokens: {} });
  const first = beginAssetRequest(coordinator, ["originals/u/f/a.jpg"]);
  coordinator = first.state;
  const second = beginAssetRequest(coordinator, ["originals/u/f/a.jpg"]);
  coordinator = second.state;

  assert.deepEqual(filterLatestAssetResponse(coordinator, second.request, {
    assets: [{ key: "originals/u/f/a.jpg", url: "https://signed.example/new", expires_in: 900 }], errors: [],
  }).assets.map((asset) => asset.url), ["https://signed.example/new"]);
  assert.deepEqual(filterLatestAssetResponse(coordinator, first.request, {
    assets: [{ key: "originals/u/f/a.jpg", url: "https://signed.example/old", expires_in: 900 }], errors: [],
  }), { assets: [], errors: [] });
});

test("drops responses from a previous dataset epoch", async () => {
  const { beginAssetDataset, beginAssetRequest, filterLatestAssetResponse } = await loadCoordinator();
  assert.equal(typeof beginAssetDataset, "function");
  let coordinator = beginAssetDataset({ datasetEpoch: 0, nextToken: 0, keyTokens: {} });
  const request = beginAssetRequest(coordinator, ["originals/u/f/a.jpg"]);
  coordinator = beginAssetDataset(request.state);
  assert.deepEqual(filterLatestAssetResponse(coordinator, request.request, {
    assets: [{ key: "originals/u/f/a.jpg", url: "https://signed.example/old", expires_in: 900 }], errors: [],
  }), { assets: [], errors: [] });
});

test("turns an omitted latest decision into a retryable unavailable error", async () => {
  const { beginAssetDataset, beginAssetRequest, filterLatestAssetResponse } = await loadCoordinator();
  let coordinator = beginAssetDataset({ datasetEpoch: 0, nextToken: 0, keyTokens: {} });
  const started = beginAssetRequest(coordinator, ["preview-a", "preview-b"]);
  coordinator = started.state;

  assert.deepEqual(filterLatestAssetResponse(coordinator, started.request, {
    assets: [{ key: "preview-a", url: "https://signed.example/a", expires_in: 900 }],
    errors: [],
  }), {
    assets: [{ key: "preview-a", url: "https://signed.example/a", expires_in: 900 }],
    errors: [{ key: "preview-b", code: "UNAVAILABLE" }],
  });
});

test("changes dataset identity when preview permission changes for the same file", async () => {
  const { assetDatasetIdentity } = await loadCoordinator();
  assert.equal(typeof assetDatasetIdentity, "function");

  const base = [{
    file_id: "file-1",
    file_type: "image",
    display_key: "thumbnails/u/file-1/thumbnail.jpg",
    original_key: "originals/u/file-1/a.jpg",
    can_preview: true,
  }];
  const noPreview = [{ ...base[0], can_preview: false }];

  assert.notEqual(assetDatasetIdentity(base), assetDatasetIdentity(noPreview));
});

test("changes dataset identity when the effective preview key changes for the same file", async () => {
  const { assetDatasetIdentity } = await loadCoordinator();
  assert.equal(typeof assetDatasetIdentity, "function");

  const base = [{
    file_id: "file-1",
    file_type: "video",
    display_key: "thumbnails/u/file-1/thumbnail.jpg",
    original_key: "originals/u/file-1/a.mp4",
    can_preview: true,
  }];
  const changedPreview = [{ ...base[0], original_key: "originals/u/file-1/b.mp4" }];

  assert.notEqual(assetDatasetIdentity(base), assetDatasetIdentity(changedPreview));
});
