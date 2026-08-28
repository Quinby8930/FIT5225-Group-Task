import { nextSigningRetryDelay } from "./assetUrls.mjs";

export function retryDelayForKey(episodes, key) {
  if (typeof key !== "string" || !key) return null;
  return nextSigningRetryDelay(episodes?.[key] || 0);
}

export function recordRetryDispatch(episodes, key) {
  if (typeof key !== "string" || !key) return { ...(episodes || {}) };
  return { ...(episodes || {}), [key]: (episodes?.[key] || 0) + 1 };
}

export function clearRetryEpisodes(episodes, keys) {
  const next = { ...(episodes || {}) };
  for (const key of Array.isArray(keys) ? keys : []) delete next[key];
  return next;
}
