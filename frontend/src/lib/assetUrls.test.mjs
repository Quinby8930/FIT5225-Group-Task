import assert from "node:assert/strict";
import test from "node:test";


async function loadAssetUrlModule() {
  return import("./assetUrls.mjs").catch(() => ({}));
}


test("indexes valid signed URLs by their exact S3 key", async () => {
  const { indexAssetUrls } = await loadAssetUrlModule();
  assert.equal(typeof indexAssetUrls, "function");

  const result = indexAssetUrls({
    assets: [
      {
        key: "thumbnails/user-1/file-1/thumbnail.jpg",
        url: "https://signed.example/thumbnail.jpg",
        expires_in: 900,
      },
      {
        key: "originals/user-1/file-1/wombat.jpg",
        url: "https://signed.example/wombat.jpg",
        expires_in: 900,
      },
    ],
  }, 1_000);

  assert.deepEqual(result, {
    "thumbnails/user-1/file-1/thumbnail.jpg": {
      url: "https://signed.example/thumbnail.jpg",
      expiresAt: 901_000,
    },
    "originals/user-1/file-1/wombat.jpg": {
      url: "https://signed.example/wombat.jpg",
      expiresAt: 901_000,
    },
  });
});


test("ignores malformed asset entries instead of creating unsafe links", async () => {
  const { indexAssetUrls } = await loadAssetUrlModule();
  assert.equal(typeof indexAssetUrls, "function");

  assert.deepEqual(indexAssetUrls(null), {});
  assert.deepEqual(indexAssetUrls({ assets: [
    { key: "originals/user-1/a.jpg", url: "javascript:alert(1)" },
    { key: "", url: "https://signed.example/empty-key" },
    null,
  ] }), {});
});


test("keeps only deduplicated originals and thumbnails owned by the signed-in user", async () => {
  const { ownedAssetKeys } = await loadAssetUrlModule();
  assert.equal(typeof ownedAssetKeys, "function");

  assert.deepEqual(ownedAssetKeys("user-1", [
    "thumbnails/user-1/file-1/thumbnail.jpg",
    "originals/user-2/file-2/private.jpg",
    "processing/user-1/file-1/frame.jpg",
    "originals/user-1/file-3/wombat.jpg",
    "thumbnails/user-1/file-1/thumbnail.jpg",
    "originals/user-1/",
  ]), [
    "thumbnails/user-1/file-1/thumbnail.jpg",
    "originals/user-1/file-3/wombat.jpg",
  ]);
});


test("recognizes only canonical original and thumbnail keys for the signed-in user", async () => {
  const { isOwnedAssetKey } = await loadAssetUrlModule();
  assert.equal(typeof isOwnedAssetKey, "function");

  assert.equal(isOwnedAssetKey("user-1", "originals/user-1/file-1/wombat.jpg"), true);
  assert.equal(isOwnedAssetKey("user-1", "thumbnails/user-1/file-1/thumbnail.jpg"), true);
  assert.equal(isOwnedAssetKey("user-1", "originals/user-2/file-1/wombat.jpg"), false);
  assert.equal(isOwnedAssetKey("user-1", "originals/user-1/"), false);
  assert.equal(isOwnedAssetKey("user-1", "originals/user-1/file-1/"), false);
  assert.equal(isOwnedAssetKey("user-1", "thumbnails/user-1/file-1/not-thumbnail.jpg"), false);
  assert.equal(isOwnedAssetKey("user-1", "originals/user-1//wombat.jpg"), false);
  assert.equal(isOwnedAssetKey("user-1", "originals/user-1/../wombat.jpg"), false);
  assert.equal(isOwnedAssetKey("user-1", "originals/user-1/file-1/nested/wombat.jpg"), false);
  assert.equal(isOwnedAssetKey("user-1", "originals\\user-1\\file-1\\wombat.jpg"), false);
  assert.equal(isOwnedAssetKey("user/1", "originals/user/1/file-1/wombat.jpg"), false);
});


test("partitions mutation input without removing foreign global results", async () => {
  const { partitionOwnedAssetKeys } = await loadAssetUrlModule();
  assert.equal(typeof partitionOwnedAssetKeys, "function");

  const globalResults = [
    "originals/user-1/file-1/wombat.jpg",
    "originals/user-2/file-2/cassowary.jpg",
    "processing/user-1/file-3/frame.jpg",
  ];
  const partitioned = partitionOwnedAssetKeys("user-1", globalResults);

  assert.deepEqual(partitioned, {
    ownedKeys: ["originals/user-1/file-1/wombat.jpg"],
    excludedKeys: [
      "originals/user-2/file-2/cassowary.jpg",
      "processing/user-1/file-3/frame.jpg",
    ],
  });
  assert.deepEqual(globalResults, [
    "originals/user-1/file-1/wombat.jpg",
    "originals/user-2/file-2/cassowary.jpg",
    "processing/user-1/file-3/frame.jpg",
  ]);
});


test("selection toggles ignore foreign or malformed keys", async () => {
  const { toggleOwnedAssetKey } = await loadAssetUrlModule();
  assert.equal(typeof toggleOwnedAssetKey, "function");

  const selected = ["originals/user-1/file-1/wombat.jpg"];
  assert.deepEqual(
    toggleOwnedAssetKey("user-1", selected, "originals/user-2/file-2/private.jpg"),
    selected
  );
  assert.deepEqual(
    toggleOwnedAssetKey("user-1", selected, "processing/user-1/file-1/frame.jpg"),
    selected
  );
  assert.deepEqual(
    toggleOwnedAssetKey("user-1", selected, "thumbnails/user-1/file-2/thumbnail.jpg"),
    [...selected, "thumbnails/user-1/file-2/thumbnail.jpg"]
  );
  assert.deepEqual(
    toggleOwnedAssetKey("user-1", selected, "originals/user-1/file-1/wombat.jpg"),
    []
  );
});


test("splits asset keys into API-safe batches without losing order", async () => {
  const { chunkAssetKeys } = await loadAssetUrlModule();
  assert.equal(typeof chunkAssetKeys, "function");

  const keys = Array.from({ length: 201 }, (_, index) => `originals/user-1/${index}/a.jpg`);
  const batches = chunkAssetKeys(keys, 100);

  assert.deepEqual(batches.map((batch) => batch.length), [100, 100, 1]);
  assert.deepEqual(batches.flat(), keys);
});


test("refreshes shortly before the earliest temporary URL expires", async () => {
  const { nextAssetRefreshDelay } = await loadAssetUrlModule();
  assert.equal(typeof nextAssetRefreshDelay, "function");

  assert.equal(nextAssetRefreshDelay({
    first: { url: "https://signed.example/first", expiresAt: 101_000 },
    second: { url: "https://signed.example/second", expiresAt: 201_000 },
  }, 1_000, 30_000), 70_000);
  assert.equal(nextAssetRefreshDelay({}, 1_000), null);
});


test("backs off refresh retries but never keeps URLs beyond their expiry", async () => {
  const { assetExpiryDelay, nextAssetRetryDelay } = await loadAssetUrlModule();
  assert.equal(typeof assetExpiryDelay, "function");
  assert.equal(typeof nextAssetRetryDelay, "function");

  const assets = {
    first: { url: "https://signed.example/first", expiresAt: 31_000 },
    second: { url: "https://signed.example/second", expiresAt: 61_000 },
  };

  assert.equal(nextAssetRetryDelay(assets, 0, 1_000), 1_000);
  assert.equal(nextAssetRetryDelay(assets, 1, 1_000), 2_000);
  assert.equal(nextAssetRetryDelay(assets, 3, 1_000), 8_000);
  assert.equal(nextAssetRetryDelay(assets, 4, 1_000), null);
  assert.equal(nextAssetRetryDelay(assets, 0, 30_500), null);
  assert.equal(nextAssetRetryDelay(assets, 0, 31_000), null);
  assert.equal(assetExpiryDelay(assets, 1_000), 30_000);
  assert.equal(assetExpiryDelay(assets, 31_000), 0);
  assert.equal(assetExpiryDelay({}, 1_000), null);
});


test("records each signing batch timestamp before dispatch and preserves failed batch identity", async () => {
  const { requestAssetUrlBatches } = await loadAssetUrlModule();
  assert.equal(typeof requestAssetUrlBatches, "function");

  const keys = Array.from({ length: 201 }, (_, index) => `originals/user-1/${index}/a.jpg`);
  const dispatched = [];
  const timestamps = [10_000, 20_000, 30_000];
  const outcomes = await requestAssetUrlBatches(
    keys,
    async (batch) => {
      dispatched.push(batch);
      if (batch[0].includes("/100/")) throw new Error("temporary outage");
      return {
        assets: batch.map((key) => ({ key, url: `https://signed.example/${key}`, expires_in: 900 })),
      };
    },
    () => timestamps.shift()
  );

  assert.deepEqual(dispatched.map((batch) => batch.length), [100, 100, 1]);
  assert.deepEqual(outcomes.map((outcome) => outcome.signedAt), [10_000, 20_000, 30_000]);
  assert.deepEqual(outcomes.map((outcome) => outcome.status), ["fulfilled", "rejected", "fulfilled"]);
  assert.deepEqual(outcomes[1].keys, keys.slice(100, 200));
});


test("prunes only expired URL records and leaves later batches available", async () => {
  const { unexpiredAssetUrls } = await loadAssetUrlModule();
  assert.equal(typeof unexpiredAssetUrls, "function");

  assert.deepEqual(unexpiredAssetUrls({
    expired: { url: "https://signed.example/expired", expiresAt: 1_000 },
    current: { url: "https://signed.example/current", expiresAt: 2_000 },
  }, 1_500), {
    current: { url: "https://signed.example/current", expiresAt: 2_000 },
  });
});

test("merges same-batch signed assets and per-key errors without losing successful URLs", async () => {
  const { mergeAssetUrlStates } = await loadAssetUrlModule();
  assert.equal(typeof mergeAssetUrlStates, "function");

  assert.deepEqual(mergeAssetUrlStates({
    previous: { status: "ready", url: "https://signed.example/previous", expiresAt: 9_000 },
  }, {
    assets: [{ key: "current", url: "https://signed.example/current", expires_in: 900 }],
    errors: [
      { key: "forbidden", code: "FORBIDDEN_KEY" },
      { key: "retry", code: "SIGNING_FAILED" },
    ],
  }, 1_000), {
    previous: { status: "ready", url: "https://signed.example/previous", expiresAt: 9_000 },
    current: { status: "ready", url: "https://signed.example/current", expiresAt: 901_000 },
    forbidden: { status: "forbidden" },
    retry: { status: "signing_failed" },
  });
});

test("retries only explicit signing failures and marks expired ready states", async () => {
  const { retryableAssetKeys, pruneAssetUrlStates } = await loadAssetUrlModule();
  assert.equal(typeof retryableAssetKeys, "function");
  assert.equal(typeof pruneAssetUrlStates, "function");
  const states = {
    retry: { status: "signing_failed" },
    forbidden: { status: "forbidden" },
    missing: { status: "not_found" },
    queued: { status: "not_completed" },
    expired: { status: "ready", url: "https://signed.example/expired", expiresAt: 1_000 },
    ready: { status: "ready", url: "https://signed.example/ready", expiresAt: 2_000 },
  };

  assert.deepEqual(retryableAssetKeys(states), ["retry"]);
  assert.deepEqual(pruneAssetUrlStates(states, 1_000), {
    retry: { status: "signing_failed" },
    forbidden: { status: "forbidden" },
    missing: { status: "not_found" },
    queued: { status: "not_completed" },
    expired: { status: "expired" },
    ready: { status: "ready", url: "https://signed.example/ready", expiresAt: 2_000 },
  });
});

test("accepts only the latest asset request version for state writes", async () => {
  const { isCurrentAssetRequest } = await loadAssetUrlModule();
  assert.equal(typeof isCurrentAssetRequest, "function");
  assert.equal(isCurrentAssetRequest(4, 4), true);
  assert.equal(isCurrentAssetRequest(4, 3), false);
});

test("backs off only bounded automatic signing retries", async () => {
  const { nextSigningRetryDelay } = await loadAssetUrlModule();
  assert.equal(typeof nextSigningRetryDelay, "function");
  assert.equal(nextSigningRetryDelay(0), 1_000);
  assert.equal(nextSigningRetryDelay(3), 8_000);
  assert.equal(nextSigningRetryDelay(4), null);
});
