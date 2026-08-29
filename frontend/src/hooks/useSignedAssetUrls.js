import { useCallback, useEffect, useRef, useState } from "react";
import { requestAssetUrls } from "../api/mediaApi";
import {
  assetExpiryDelay, assetRefreshSchedule, assetRetrySchedule, markAssetKeysLoading, mergeAssetUrlStates,
  requestAssetUrlBatches, retryableAssetKeys, transitionExpiredAssetStates,
} from "../lib/assetUrls.mjs";
import {
  assetDatasetIdentity, beginAssetDataset, beginAssetRequest, createLatestAssetStateCoordinator,
  filterLatestAssetResponse,
} from "../lib/assetRequestCoordinator.mjs";
import { canOpenFullImage, canRenderInlinePreview, withDetachedWindow } from "../lib/mediaActions.mjs";
import { clearRetryEpisodes, recordRetryDispatch } from "../lib/retryEpisodes.mjs";

function previewKey(item) {
  if (!canRenderInlinePreview(item)) return null;
  return item.file_type === "image" ? item.display_key : (item.file_type === "video" ? item.original_key : null);
}

function uniquePreviewKeys(items) {
  return [...new Set((Array.isArray(items) ? items : []).map(previewKey).filter(Boolean))];
}

export default function useSignedAssetUrls(items, sessionKey, onStatus) {
  const [assetStates, setAssetStates] = useState({});
  const latestAssetStates = useRef(null);
  if (latestAssetStates.current === null) {
    latestAssetStates.current = createLatestAssetStateCoordinator({});
  }
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

  const replaceAssetStates = useCallback((nextStates) => {
    const next = latestAssetStates.current.replace(nextStates);
    setAssetStates(next);
    return next;
  }, []);

  const transitionAssetStates = useCallback((updater) => {
    const next = latestAssetStates.current.transition(updater);
    setAssetStates(next);
    return next;
  }, []);

  const loadKeys = useCallback(async (keys, datasetEpoch, { resetRetries = false, autoRetryCounts = {} } = {}) => {
    if (!keys.length || coordinator.current.datasetEpoch !== datasetEpoch) return;
    const started = beginAssetRequest(coordinator.current, keys);
    coordinator.current = started.state;
    if (resetRetries) retryEpisodes.current = clearRetryEpisodes(retryEpisodes.current, keys);
    transitionAssetStates((current) => markAssetKeysLoading(keys, current));
    const outcomes = await requestAssetUrlBatches(keys, requestAssetUrls);
    if (coordinator.current.datasetEpoch !== datasetEpoch) return;

    transitionAssetStates((current) => outcomes.reduce((next, outcome) => {
      const rawResponse = outcome.status === "fulfilled"
        ? outcome.response
        : { errors: outcome.keys.map((key) => ({ key, code: "UNAVAILABLE" })) };
      const response = filterLatestAssetResponse(coordinator.current, started.request, rawResponse);
      const clearedKeys = [
        ...response.assets.map((asset) => asset.key),
        ...response.errors
          .filter((error) => error.code !== "SIGNING_FAILED" && error.code !== "UNAVAILABLE")
          .map((error) => error.key),
      ];
      if (clearedKeys.length) retryEpisodes.current = clearRetryEpisodes(retryEpisodes.current, clearedKeys);
      return mergeAssetUrlStates(next, response, outcome.signedAt, Date.now(), autoRetryCounts);
    }, current));
  }, [transitionAssetStates]);

  const keys = uniquePreviewKeys(items);
  const itemIdentity = assetDatasetIdentity(items);
  const keyIdentity = keys.join("|");
  const stateIdentity = Object.entries(assetStates).map(([key, state]) => `${key}:${state?.status}:${state?.expiresAt || ""}:${state?.retryable === true}:${state?.retryExhausted === true}:${state?.requestInFlight === true}`).sort().join("|");

  useEffect(() => {
    coordinator.current = beginAssetDataset(coordinator.current);
    const datasetEpoch = coordinator.current.datasetEpoch;
    retryEpisodes.current = {};
    clearTimers();
    replaceAssetStates({});
    if (keys.length) loadKeys(keys, datasetEpoch).catch(() => {});
    return () => {
      coordinator.current = beginAssetDataset(coordinator.current);
      clearTimers();
    };
  }, [clearTimers, itemIdentity, loadKeys, replaceAssetStates, sessionKey]);

  useEffect(() => {
    const datasetEpoch = coordinator.current.datasetEpoch;
    if (!keys.length) return undefined;
    clearTimers();
    const expiryDelay = assetExpiryDelay(assetStates);
    if (expiryDelay !== null) schedule(() => {
      if (coordinator.current.datasetEpoch !== datasetEpoch) return;
      let transition;
      transitionAssetStates((current) => {
        transition = transitionExpiredAssetStates(current, keys);
        return transition.states;
      });
      if (transition.requestKeys.length) loadKeys(transition.requestKeys, datasetEpoch).catch(() => {});
    }, Math.max(0, expiryDelay));
    const refreshPlan = assetRefreshSchedule(assetStates, keys);
    if (refreshPlan) {
      schedule(
        () => {
          if (coordinator.current.datasetEpoch !== datasetEpoch) return;
          const latestPlan = assetRefreshSchedule(latestAssetStates.current.current(), refreshPlan.keys);
          if (!latestPlan || latestPlan.delay > 1_000) return;
          loadKeys(latestPlan.keys, datasetEpoch).catch(() => {});
        },
        refreshPlan.delay,
      );
    }
    for (const { key, delay } of assetRetrySchedule(assetStates, retryEpisodes.current)) {
      const scheduledAttempt = retryEpisodes.current[key] || 0;
      schedule(() => {
        if (coordinator.current.datasetEpoch !== datasetEpoch) return;
        if ((retryEpisodes.current[key] || 0) !== scheduledAttempt) return;
        const stillRetryable = retryableAssetKeys(latestAssetStates.current.current()).includes(key);
        if (!stillRetryable) return;
        retryEpisodes.current = recordRetryDispatch(retryEpisodes.current, key);
        loadKeys([key], datasetEpoch, {
          autoRetryCounts: { [key]: retryEpisodes.current[key] },
        }).catch(() => {});
      }, delay);
    }
    return clearTimers;
  }, [clearTimers, keyIdentity, loadKeys, schedule, stateIdentity, transitionAssetStates]);

  useEffect(() => () => { coordinator.current = beginAssetDataset(coordinator.current); clearTimers(); }, [clearTimers]);

  useEffect(() => {
    const pruneExpired = () => {
      if (document.visibilityState === "hidden") return;
      const datasetEpoch = coordinator.current.datasetEpoch;
      let transition;
      transitionAssetStates((current) => {
        transition = transitionExpiredAssetStates(current, keys);
        return transition.states;
      });
      if (transition.requestKeys.length) loadKeys(transition.requestKeys, datasetEpoch).catch(() => {});
    };
    window.addEventListener("focus", pruneExpired);
    document.addEventListener("visibilitychange", pruneExpired);
    return () => {
      window.removeEventListener("focus", pruneExpired);
      document.removeEventListener("visibilitychange", pruneExpired);
    };
  }, [keyIdentity, loadKeys, transitionAssetStates]);

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
