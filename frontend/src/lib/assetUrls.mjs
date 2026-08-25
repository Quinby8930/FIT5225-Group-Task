export function ownedAssetKeys(userId, keys) {
  if (typeof userId !== "string" || !userId || userId.includes("/") || !Array.isArray(keys)) {
    return [];
  }

  const prefixes = [`originals/${userId}/`, `thumbnails/${userId}/`];
  return [...new Set(keys.filter((key) => (
    typeof key === "string"
    && prefixes.some((prefix) => key.startsWith(prefix) && key.length > prefix.length)
  )))];
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


export function nextAssetRefreshDelay(assetUrls, nowMs = Date.now(), leadMs = 30_000) {
  const expirations = Object.values(assetUrls || {})
    .map((asset) => asset?.expiresAt)
    .filter((expiresAt) => Number.isFinite(expiresAt));
  if (!expirations.length) return null;

  return Math.max(1_000, Math.min(...expirations) - nowMs - leadMs);
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
