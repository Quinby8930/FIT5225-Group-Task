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
