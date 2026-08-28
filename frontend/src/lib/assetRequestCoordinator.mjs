function uniqueKeys(keys) {
  return [...new Set((Array.isArray(keys) ? keys : []).filter((key) => typeof key === "string" && key))];
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
  return {
    assets: (Array.isArray(response?.assets) ? response.assets : []).filter((asset) => isLatest(asset?.key)),
    errors: (Array.isArray(response?.errors) ? response.errors : []).filter((error) => isLatest(error?.key)),
  };
}
