function validKeySegment(segment) {
  return Boolean(segment) && segment !== "." && segment !== "..";
}


export function isOwnedAssetKey(userId, key) {
  if (
    typeof userId !== "string"
    || !userId
    || userId.includes("/")
    || userId.includes("\\")
    || typeof key !== "string"
    || key.includes("\\")
  ) {
    return false;
  }

  const parts = key.split("/");
  if (parts.length !== 4 || parts[1] !== userId || !validKeySegment(parts[2])) {
    return false;
  }

  if (parts[0] === "originals") {
    return validKeySegment(parts[3]);
  }
  return parts[0] === "thumbnails" && parts[3] === "thumbnail.jpg";
}


export function partitionOwnedAssetKeys(userId, keys) {
  if (!Array.isArray(keys)) {
    return { ownedKeys: [], excludedKeys: [] };
  }

  const uniqueKeys = [...new Set(keys)];
  return {
    ownedKeys: uniqueKeys.filter((key) => isOwnedAssetKey(userId, key)),
    excludedKeys: uniqueKeys.filter((key) => !isOwnedAssetKey(userId, key)),
  };
}


export function ownedAssetKeys(userId, keys) {
  return partitionOwnedAssetKeys(userId, keys).ownedKeys;
}


export function toggleOwnedAssetKey(userId, selectedKeys, key) {
  if (!Array.isArray(selectedKeys) || !isOwnedAssetKey(userId, key)) {
    return Array.isArray(selectedKeys) ? selectedKeys : [];
  }
  return selectedKeys.includes(key)
    ? selectedKeys.filter((item) => item !== key)
    : [...selectedKeys, key];
}


export function chunkAssetKeys(keys, batchSize = 100) {
  if (!Array.isArray(keys) || !Number.isInteger(batchSize) || batchSize < 1) {
    return [];
  }

  const batches = [];
  for (let index = 0; index < keys.length; index += batchSize) {
    batches.push(keys.slice(index, index + batchSize));
  }
  return batches;
}


export async function requestAssetUrlBatches(keys, requestBatch, now = Date.now) {
  if (typeof requestBatch !== "function") return [];

  return Promise.all(chunkAssetKeys(keys).map(async (batch) => {
    const signedAt = now();
    try {
      return {
        status: "fulfilled",
        keys: batch,
        signedAt,
        response: await requestBatch(batch),
      };
    } catch (error) {
      return {
        status: "rejected",
        keys: batch,
        signedAt,
        error,
      };
    }
  }));
}


export function indexAssetUrls(response, nowMs = Date.now()) {
  if (!Array.isArray(response?.assets)) {
    return {};
  }

  const indexed = {};
  for (const asset of response.assets) {
    if (
      typeof asset?.key === "string"
      && asset.key
      && typeof asset.url === "string"
      && /^https:\/\//i.test(asset.url)
      && Number.isFinite(asset.expires_in)
      && asset.expires_in > 0
    ) {
      indexed[asset.key] = {
        url: asset.url,
        expiresAt: nowMs + (asset.expires_in * 1_000),
      };
    }
  }
  return indexed;
}

export function assetErrorStatus(code) {
  const states = {
    SIGNING_FAILED: "signing_failed",
    FORBIDDEN_KEY: "forbidden",
    NOT_FOUND: "not_found",
    NOT_COMPLETED: "not_completed",
  };
  return states[code] || "unavailable";
}

export function usableAssetUrl(state, nowMs = Date.now()) {
  return state?.status === "ready"
    && typeof state.url === "string"
    && /^https:\/\//i.test(state.url)
    && Number.isFinite(state.expiresAt)
    && state.expiresAt > nowMs
    ? state.url
    : null;
}

export function markAssetKeysLoading(keys, current = {}, nowMs = Date.now()) {
  const next = { ...(current || {}) };
  for (const key of Array.isArray(keys) ? keys : []) {
    if (typeof key !== "string" || !key) continue;
    const previous = next[key];
    if (
      previous?.status === "ready"
      && typeof previous.url === "string"
      && Number.isFinite(previous.expiresAt)
      && previous.expiresAt > nowMs
    ) {
      const { retryable, retryExhausted, requestInFlight, ...ready } = previous;
      next[key] = { ...ready, requestInFlight: true };
      continue;
    }
    next[key] = { status: "loading", requestInFlight: true };
  }
  return next;
}

export function mergeAssetUrlStates(current, response, signedAt = Date.now(), nowMs = Date.now(), autoRetryCounts = {}) {
  const next = { ...(current || {}) };
  const successful = indexAssetUrls(response, signedAt);
  for (const [key, asset] of Object.entries(successful)) {
    next[key] = { status: "ready", ...asset };
  }
  for (const error of Array.isArray(response?.errors) ? response.errors : []) {
    if (
      typeof error?.key === "string"
      && error.key
      && !Object.hasOwn(successful, error.key)
    ) {
      const previous = next[error.key];
      const transient = error.code === "SIGNING_FAILED" || error.code === "UNAVAILABLE";
      const exhausted = Number.isInteger(autoRetryCounts?.[error.key])
        && autoRetryCounts[error.key] >= 4;
      if (
        transient
        && usableAssetUrl(previous, nowMs)
      ) {
        const { retryable, retryExhausted, requestInFlight, ...ready } = previous;
        next[error.key] = exhausted
          ? { ...ready, retryExhausted: true }
          : { ...ready, retryable: true };
      } else {
        const state = { status: assetErrorStatus(error.code) };
        if (error.code === "UNAVAILABLE") state.retryable = true;
        next[error.key] = state;
      }
      if (
        exhausted
        && (next[error.key]?.status === "signing_failed" || next[error.key]?.status === "unavailable")
      ) {
        next[error.key] = { status: "retry_exhausted" };
      }
    }
  }
  return next;
}

export function retryableAssetKeys(assetStates) {
  return Object.entries(assetStates || {})
    .filter(([, state]) => (
      state?.requestInFlight !== true
      && (state?.status === "signing_failed" || state?.retryable === true)
    ))
    .map(([key]) => key);
}

export function assetRetrySchedule(assetStates, retryEpisodes, nowMs = Date.now()) {
  return retryableAssetKeys(assetStates).flatMap((key) => {
    const state = assetStates[key];
    const attempt = retryEpisodes?.[key] || 0;
    const delay = state?.status === "ready"
      ? nextAssetRetryDelay({ [key]: state }, attempt, nowMs)
      : nextSigningRetryDelay(attempt);
    return delay === null ? [] : [{ key, delay }];
  });
}

export function transitionExpiredAssetStates(assetStates, requestedKeys, nowMs = Date.now()) {
  const requested = new Set(Array.isArray(requestedKeys) ? requestedKeys : []);
  const requestKeys = [];
  const states = Object.fromEntries(Object.entries(assetStates || {}).map(([key, state]) => {
    if (state?.status === "expired") {
      if (requested.has(key)) requestKeys.push(key);
      return [key, state];
    }
    if (state?.status !== "ready" || !Number.isFinite(state.expiresAt) || state.expiresAt > nowMs) {
      return [key, state];
    }
    if (state.requestInFlight === true) {
      return [key, { status: "loading", requestInFlight: true }];
    }
    if (state.retryExhausted === true) return [key, { status: "retry_exhausted" }];
    if (requested.has(key)) requestKeys.push(key);
    return [key, { status: "expired" }];
  }));
  return { states, requestKeys: [...new Set(requestKeys)] };
}

export function pruneAssetUrlStates(assetStates, nowMs = Date.now()) {
  return Object.fromEntries(Object.entries(assetStates || {}).map(([key, state]) => {
    if (state?.status === "ready" && Number.isFinite(state.expiresAt) && state.expiresAt <= nowMs) {
      return [key, { status: "expired" }];
    }
    return [key, state];
  }));
}

export function expiredAssetKeys(assetStates, requestedKeys, nowMs = Date.now()) {
  const states = assetStates && typeof assetStates === "object" ? assetStates : {};
  return [...new Set(Array.isArray(requestedKeys) ? requestedKeys : [])].filter((key) => {
    const state = states[key];
    if (state?.status === "expired") return true;
    return state?.status === "ready"
      && Number.isFinite(state.expiresAt)
      && state.expiresAt <= nowMs;
  });
}

export function isCurrentAssetRequest(currentVersion, responseVersion) {
  return Number.isInteger(currentVersion)
    && Number.isInteger(responseVersion)
    && currentVersion === responseVersion;
}

export function nextSigningRetryDelay(attempt, maxAttempts = 4) {
  if (!Number.isInteger(attempt) || attempt < 0 || attempt >= maxAttempts) return null;
  return Math.min(1_000 * (2 ** attempt), 15_000);
}


export function nextAssetRefreshDelay(assetUrls, nowMs = Date.now(), leadMs = 30_000) {
  const expirations = Object.values(assetUrls || {})
    .filter((asset) => asset?.retryExhausted !== true)
    .map((asset) => asset?.expiresAt)
    .filter((expiresAt) => Number.isFinite(expiresAt));
  if (!expirations.length) return null;

  return Math.max(1_000, Math.min(...expirations) - nowMs - leadMs);
}

export function assetRefreshSchedule(assetStates, requestedKeys, nowMs = Date.now(), leadMs = 30_000) {
  const states = assetStates && typeof assetStates === "object" ? assetStates : {};
  const keys = [...new Set(Array.isArray(requestedKeys) ? requestedKeys : [])].filter((key) => (
    states[key]?.status === "ready"
    && states[key]?.requestInFlight !== true
    && states[key]?.retryExhausted !== true
    && Number.isFinite(states[key]?.expiresAt)
  ));
  const delay = nextAssetRefreshDelay(
    Object.fromEntries(keys.map((key) => [key, states[key]])),
    nowMs,
    leadMs,
  );
  return delay === null ? null : { keys, delay };
}


export function assetExpiryDelay(assetUrls, nowMs = Date.now()) {
  const expirations = Object.values(assetUrls || {})
    .map((asset) => asset?.expiresAt)
    .filter((expiresAt) => Number.isFinite(expiresAt));
  if (!expirations.length) return null;

  return Math.max(0, Math.min(...expirations) - nowMs);
}


export function nextAssetRetryDelay(
  assetUrls,
  attempt,
  nowMs = Date.now(),
  maxAttempts = 4
) {
  if (!Number.isInteger(attempt) || attempt < 0 || attempt >= maxAttempts) return null;

  const remaining = assetExpiryDelay(assetUrls, nowMs);
  if (remaining === null || remaining <= 0) return null;

  const retryDelay = Math.min(1_000 * (2 ** attempt), 15_000);
  return retryDelay < remaining ? retryDelay : null;
}


export function unexpiredAssetUrls(assetUrls, nowMs = Date.now()) {
  return Object.fromEntries(
    Object.entries(assetUrls || {}).filter(([, asset]) => (
      typeof asset?.url === "string"
      && Number.isFinite(asset.expiresAt)
      && asset.expiresAt > nowMs
    ))
  );
}
