function uniqueKeys(keys) {
  return [...new Set((Array.isArray(keys) ? keys : []).filter((key) => typeof key === "string" && key))];
}

export function assetDatasetIdentity(items) {
  return JSON.stringify((Array.isArray(items) ? items : []).map((item) => [
    item?.file_id,
    item?.file_type,
    item?.can_preview === true,
    item?.display_key,
    item?.original_key,
  ]));
}

export function beginAssetDataset(previous = {}) {
  return {
    datasetEpoch: (Number.isInteger(previous.datasetEpoch) ? previous.datasetEpoch : 0) + 1,
    nextToken: Number.isInteger(previous.nextToken) ? previous.nextToken : 0,
    keyTokens: {},
  };
}

export function beginAssetRequest(current, keys) {
  const token = (Number.isInteger(current?.nextToken) ? current.nextToken : 0) + 1;
  const requestKeys = uniqueKeys(keys);
  const state = {
    datasetEpoch: current?.datasetEpoch,
    nextToken: token,
    keyTokens: { ...(current?.keyTokens || {}) },
  };
  requestKeys.forEach((key) => { state.keyTokens[key] = token; });
  return { state, request: { datasetEpoch: state.datasetEpoch, token, keys: requestKeys } };
}

export function filterLatestAssetResponse(current, request, response) {
  if (!current || !request || current.datasetEpoch !== request.datasetEpoch) return { assets: [], errors: [] };
  const isLatest = (key) => request.keys.includes(key) && current.keyTokens[key] === request.token;
  const latestKeys = request.keys.filter(isLatest);
  const assets = (Array.isArray(response?.assets) ? response.assets : []).filter((asset) => (
    isLatest(asset?.key)
    && typeof asset?.url === "string"
    && /^https:\/\//i.test(asset.url)
    && Number.isFinite(asset.expires_in)
    && asset.expires_in > 0
  ));
  const errors = (Array.isArray(response?.errors) ? response.errors : []).filter((error) => (
    isLatest(error?.key) && typeof error?.code === "string" && error.code.length > 0
  ));
  const decided = new Set([...assets, ...errors].map((decision) => decision.key));
  for (const key of latestKeys) {
    if (!decided.has(key)) errors.push({ key, code: "UNAVAILABLE" });
  }
  return { assets, errors };
}
