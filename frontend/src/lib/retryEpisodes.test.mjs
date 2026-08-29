import assert from "node:assert/strict";
import test from "node:test";

async function loadRetries() {
  return import("./retryEpisodes.mjs").catch(() => ({}));
}

test("gives a key a fresh retry budget after signing failure, success, and a later failure", async () => {
  const { retryDelayForKey, recordRetryDispatch, clearRetryEpisodes } = await loadRetries();
  assert.equal(typeof retryDelayForKey, "function");
  assert.equal(typeof recordRetryDispatch, "function");
  assert.equal(typeof clearRetryEpisodes, "function");

  let episodes = {};
  assert.equal(retryDelayForKey(episodes, "asset-a"), 1_000);
  episodes = recordRetryDispatch(episodes, "asset-a");
  assert.equal(retryDelayForKey(episodes, "asset-a"), 2_000);
  episodes = clearRetryEpisodes(episodes, ["asset-a"]);
  assert.deepEqual(episodes, {});
  assert.equal(retryDelayForKey(episodes, "asset-a"), 1_000);
});

test("keeps retry accounting separate for independent keys", async () => {
  const { retryDelayForKey, recordRetryDispatch } = await loadRetries();
  let episodes = recordRetryDispatch({}, "asset-a");
  assert.equal(retryDelayForKey(episodes, "asset-a"), 2_000);
  assert.equal(retryDelayForKey(episodes, "asset-b"), 1_000);
});

test("manual refresh clears an exhausted key and resumes its automatic retry budget", async () => {
  const { clearRetryEpisodes, retryDelayForKey } = await loadRetries();
  const { markAssetKeysLoading, mergeAssetUrlStates } = await import("./assetUrls.mjs");
  const key = "preview";
  const episodes = clearRetryEpisodes({ [key]: 4 }, [key]);
  const loading = markAssetKeysLoading([key], { [key]: { status: "retry_exhausted" } });
  const failedManualRefresh = mergeAssetUrlStates(loading, {
    errors: [{ key, code: "SIGNING_FAILED" }],
  });

  assert.deepEqual(episodes, {});
  assert.deepEqual(failedManualRefresh, { [key]: { status: "signing_failed" } });
  assert.equal(retryDelayForKey(episodes, key), 1_000);
});
