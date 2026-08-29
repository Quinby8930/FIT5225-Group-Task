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

test("a deferred fourth automatic retry excludes proactive expiry and focus dispatches before succeeding", async () => {
  const { createLatestAssetStateCoordinator } = await loadCoordinator();
  const {
    assetRefreshSchedule,
    assetRetrySchedule,
    markAssetKeysLoading,
    mergeAssetUrlStates,
    transitionExpiredAssetStates,
  } = await import("./assetUrls.mjs");
  assert.equal(typeof createLatestAssetStateCoordinator, "function");

  const key = "preview";
  const latest = createLatestAssetStateCoordinator({
    [key]: {
      status: "ready",
      url: "https://signed.example/current",
      expiresAt: 100_000,
      retryable: true,
    },
  });
  let automaticDispatches = 0;

  for (let dispatched = 1; dispatched <= 4; dispatched += 1) {
    const schedule = assetRetrySchedule(latest.current(), { [key]: dispatched - 1 }, 1_000);
    assert.equal(schedule.length, 1);
    automaticDispatches += 1;
    latest.transition((states) => markAssetKeysLoading([key], states, 1_000));

    if (dispatched < 4) {
      latest.transition((states) => mergeAssetUrlStates(states, {
        errors: [{ key, code: "UNAVAILABLE" }],
      }, 1_000, 1_000, { [key]: dispatched }));
    }
  }

  assert.equal(automaticDispatches, 4);
  assert.equal(latest.current()[key].requestInFlight, true);
  assert.deepEqual(assetRetrySchedule(latest.current(), { [key]: 4 }, 1_000), []);
  assert.equal(assetRefreshSchedule(latest.current(), [key], 1_000), null);

  const focusTransition = transitionExpiredAssetStates(latest.current(), [key], 99_999);
  assert.deepEqual(focusTransition.requestKeys, []);
  latest.replace(focusTransition.states);
  const expiryTransition = transitionExpiredAssetStates(latest.current(), [key], 100_000);
  assert.deepEqual(expiryTransition, {
    states: { [key]: { status: "loading", requestInFlight: true } },
    requestKeys: [],
  });
  latest.replace(expiryTransition.states);

  latest.transition((states) => mergeAssetUrlStates(states, {
    assets: [{ key, url: "https://signed.example/replacement", expires_in: 900 }],
    errors: [],
  }, 100_000, 100_000, { [key]: 4 }));
  assert.deepEqual(latest.current(), {
    [key]: {
      status: "ready",
      url: "https://signed.example/replacement",
      expiresAt: 1_000_000,
    },
  });
  assert.deepEqual(assetRetrySchedule(latest.current(), { [key]: 4 }, 100_000), []);
});

test("a deferred fourth automatic retry fails terminally without allowing a fifth dispatch", async () => {
  const { createLatestAssetStateCoordinator } = await loadCoordinator();
  const {
    assetRefreshSchedule,
    assetRetrySchedule,
    markAssetKeysLoading,
    mergeAssetUrlStates,
    transitionExpiredAssetStates,
  } = await import("./assetUrls.mjs");
  assert.equal(typeof createLatestAssetStateCoordinator, "function");

  const key = "preview";
  const latest = createLatestAssetStateCoordinator({
    [key]: {
      status: "ready",
      url: "https://signed.example/current",
      expiresAt: 100_000,
      retryable: true,
    },
  });
  let automaticDispatches = 0;

  for (let dispatched = 1; dispatched <= 4; dispatched += 1) {
    assert.equal(assetRetrySchedule(latest.current(), { [key]: dispatched - 1 }, 1_000).length, 1);
    automaticDispatches += 1;
    latest.transition((states) => markAssetKeysLoading([key], states, 1_000));
    if (dispatched < 4) {
      latest.transition((states) => mergeAssetUrlStates(states, {
        errors: [{ key, code: "SIGNING_FAILED" }],
      }, 1_000, 1_000, { [key]: dispatched }));
    }
  }

  assert.equal(assetRefreshSchedule(latest.current(), [key], 70_000), null);
  const expired = transitionExpiredAssetStates(latest.current(), [key], 100_000);
  assert.deepEqual(expired.requestKeys, []);
  latest.replace(expired.states);
  latest.transition((states) => mergeAssetUrlStates(states, {
    errors: [{ key, code: "UNAVAILABLE" }],
  }, 100_000, 100_000, { [key]: 4 }));

  assert.equal(automaticDispatches, 4);
  assert.deepEqual(latest.current(), { [key]: { status: "retry_exhausted" } });
  assert.deepEqual(assetRetrySchedule(latest.current(), { [key]: 4 }, 100_000), []);
  assert.equal(assetRefreshSchedule(latest.current(), [key], 100_000), null);
});

test("queued expiry and focus transitions cannot overwrite a newer manual refresh response", async () => {
  const { createLatestAssetStateCoordinator } = await loadCoordinator();
  const { markAssetKeysLoading, mergeAssetUrlStates, transitionExpiredAssetStates } = await import("./assetUrls.mjs");
  assert.equal(typeof createLatestAssetStateCoordinator, "function");

  const key = "preview";
  const latest = createLatestAssetStateCoordinator({
    [key]: {
      status: "ready",
      url: "https://signed.example/old",
      expiresAt: 2_000,
    },
  });
  const queuedExpiryOrFocus = () => {
    const transition = transitionExpiredAssetStates(latest.current(), [key], 3_000);
    latest.replace(transition.states);
    return transition.requestKeys;
  };

  latest.transition((states) => markAssetKeysLoading([key], states, 1_500));
  latest.transition((states) => mergeAssetUrlStates(states, {
    assets: [{ key, url: "https://signed.example/new", expires_in: 900 }],
    errors: [],
  }, 2_500, 2_500));

  assert.deepEqual(queuedExpiryOrFocus(), []);
  assert.deepEqual(queuedExpiryOrFocus(), []);
  assert.deepEqual(latest.current(), {
    [key]: {
      status: "ready",
      url: "https://signed.example/new",
      expiresAt: 902_500,
    },
  });
});
