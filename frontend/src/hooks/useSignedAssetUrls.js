import { useCallback, useEffect, useRef, useState } from "react";
import { requestAssetUrls } from "../api/mediaApi";
import {
  assetExpiryDelay, markAssetKeysLoading, mergeAssetUrlStates, nextAssetRefreshDelay,
  pruneAssetUrlStates, requestAssetUrlBatches, retryableAssetKeys,
} from "../lib/assetUrls.mjs";
import { beginAssetDataset, beginAssetRequest, filterLatestAssetResponse } from "../lib/assetRequestCoordinator.mjs";
import { canOpenFullImage, withDetachedWindow } from "../lib/mediaActions.mjs";
import { clearRetryEpisodes, recordRetryDispatch, retryDelayForKey } from "../lib/retryEpisodes.mjs";

function previewKey(item) {
  if (!item || item.legacy || item.can_preview !== true) return null;
  return item.file_type === "image" ? item.display_key : (item.file_type === "video" ? item.original_key : null);
}

function uniquePreviewKeys(items) {
  return [...new Set((Array.isArray(items) ? items : []).map(previewKey).filter(Boolean))];
}

export default function useSignedAssetUrls(items, sessionKey, onStatus) {
  const [assetStates, setAssetStates] = useState({});
  const coordinator = useRef({ datasetEpoch: 0, nextToken: 0, keyTokens: {} });
  const retryEpisodes = useRef({});
  const timers = useRef(new Set());

  const clearTimers = useCallback(() => {
    timers.current.forEach((timer) => window.clearTimeout(timer));
    timers.current.clear();
  }, []);

  const schedule = useCallback((callback, delay) => {
    const timer = window.setTimeout(() => { timers.current.delete(timer); callback(); }, delay);
    timers.current.add(timer);
  }, []);

  const loadKeys = useCallback(async (keys, datasetEpoch, { resetRetries = false } = {}) => {
    if (!keys.length || coordinator.current.datasetEpoch !== datasetEpoch) return;
    const started = beginAssetRequest(coordinator.current, keys);
    coordinator.current = started.state;
    if (resetRetries) retryEpisodes.current = clearRetryEpisodes(retryEpisodes.current, keys);
    setAssetStates((current) => markAssetKeysLoading(keys, current));
    const outcomes = await requestAssetUrlBatches(keys, requestAssetUrls);
    if (coordinator.current.datasetEpoch !== datasetEpoch) return;

    setAssetStates((current) => outcomes.reduce((next, outcome) => {
      const rawResponse = outcome.status === "fulfilled"
        ? outcome.response
        : { errors: outcome.keys.map((key) => ({ key, code: "UNAVAILABLE" })) };
      const response = filterLatestAssetResponse(coordinator.current, started.request, rawResponse);
      const clearedKeys = [
        ...response.assets.map((asset) => asset.key),
        ...response.errors.filter((error) => error.code !== "SIGNING_FAILED").map((error) => error.key),
      ];
      if (clearedKeys.length) retryEpisodes.current = clearRetryEpisodes(retryEpisodes.current, clearedKeys);
      return mergeAssetUrlStates(next, response, outcome.signedAt);
    }, current));
  }, []);

  const keys = uniquePreviewKeys(items);
  const itemIdentity = (Array.isArray(items) ? items : []).map((item) => item.identity).join("|");
  const keyIdentity = keys.join("|");
  const stateIdentity = Object.entries(assetStates).map(([key, state]) => `${key}:${state?.status}:${state?.expiresAt || ""}`).sort().join("|");

  useEffect(() => {
    coordinator.current = beginAssetDataset(coordinator.current);
    const datasetEpoch = coordinator.current.datasetEpoch;
    retryEpisodes.current = {};
    clearTimers();
    setAssetStates({});
    if (keys.length) loadKeys(keys, datasetEpoch).catch(() => {});
    return () => {
      coordinator.current = beginAssetDataset(coordinator.current);
      clearTimers();
    };
  }, [clearTimers, itemIdentity, loadKeys, sessionKey]);

  useEffect(() => {
    const datasetEpoch = coordinator.current.datasetEpoch;
    if (!keys.length) return undefined;
    clearTimers();
    const expiryDelay = assetExpiryDelay(assetStates);
    if (expiryDelay !== null) schedule(() => {
      if (coordinator.current.datasetEpoch !== datasetEpoch) return;
      setAssetStates((current) => pruneAssetUrlStates(current));
      loadKeys(keys, datasetEpoch).catch(() => {});
    }, Math.max(0, expiryDelay));
    const refreshDelay = nextAssetRefreshDelay(assetStates);
    if (refreshDelay !== null) schedule(() => loadKeys(keys, datasetEpoch).catch(() => {}), refreshDelay);
    for (const key of retryableAssetKeys(assetStates)) {
      const delay = retryDelayForKey(retryEpisodes.current, key);
      if (delay !== null) schedule(() => {
        retryEpisodes.current = recordRetryDispatch(retryEpisodes.current, key);
        loadKeys([key], datasetEpoch).catch(() => {});
      }, delay);
    }
    return clearTimers;
  }, [clearTimers, keyIdentity, loadKeys, schedule, stateIdentity]);

  useEffect(() => () => { coordinator.current = beginAssetDataset(coordinator.current); clearTimers(); }, [clearTimers]);

  const refresh = useCallback(() => {
    const datasetEpoch = coordinator.current.datasetEpoch;
    clearTimers();
    return loadKeys(keys, datasetEpoch, { resetRetries: true });
  }, [clearTimers, keyIdentity, loadKeys]);

  const openOriginal = useCallback(async (item) => {
    if (!canOpenFullImage(item)) return;
    try {
      const opened = await withDetachedWindow(window.open.bind(window), async (popup) => {
        try {
          const datasetEpoch = coordinator.current.datasetEpoch;
          const outcomes = await requestAssetUrlBatches([item.original_key], requestAssetUrls);
          if (coordinator.current.datasetEpoch !== datasetEpoch) { popup.close(); return; }
          const response = outcomes[0]?.status === "fulfilled" ? outcomes[0].response : { errors: [] };
          const original = mergeAssetUrlStates({}, response, outcomes[0]?.signedAt)[item.original_key];
          if (original?.status !== "ready") throw new Error("The full image is currently unavailable.");
          popup.location.replace(original.url);
        } catch (error) {
          popup.close();
          throw error;
        }
      });
      if (!opened.opened) onStatus?.({ type: "error", message: "Your browser blocked the full-image window. Allow pop-ups and try again." });
    } catch (error) {
      onStatus?.({ type: "error", message: error.message });
    }
  }, [onStatus]);

  return { assetStates, refresh, openOriginal };
}
