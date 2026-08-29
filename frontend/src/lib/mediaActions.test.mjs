import assert from "node:assert/strict";
import test from "node:test";

async function loadMediaActions() {
  return import("./mediaActions.mjs").catch(() => ({}));
}

test("allows preview actions only for trusted previewable structured media", async () => {
  const { canOpenPreview, canOpenFullImage } = await loadMediaActions();
  assert.equal(typeof canOpenPreview, "function");
  assert.equal(typeof canOpenFullImage, "function");
  assert.equal(canOpenPreview({ legacy: false, can_preview: true, file_type: "video" }), true);
  assert.equal(canOpenFullImage({ legacy: false, can_preview: true, file_type: "image", original_key: "originals/u/f/a.jpg" }), true);
  assert.equal(canOpenPreview({ legacy: false, can_preview: false, file_type: "image" }), false);
  assert.equal(canOpenFullImage({ legacy: true, can_preview: true, file_type: "image", original_key: "originals/u/f/a.jpg" }), false);
});

test("allows inline media rendering only when preview permission is granted", async () => {
  const { canRenderInlinePreview } = await loadMediaActions();
  assert.equal(typeof canRenderInlinePreview, "function");

  assert.equal(canRenderInlinePreview({ legacy: false, can_preview: true, file_type: "image" }), true);
  assert.equal(canRenderInlinePreview({ legacy: false, can_preview: false, file_type: "video" }), false);
});

test("opens and detaches a navigable blank window without noopener feature flags", async () => {
  const { openDetachedWindow } = await loadMediaActions();
  assert.equal(typeof openDetachedWindow, "function");
  const popup = { opener: { unsafe: true }, location: { replace() {} } };
  const calls = [];
  const result = openDetachedWindow((...args) => { calls.push(args); return popup; });

  assert.equal(result, popup);
  assert.deepEqual(calls, [["about:blank", "_blank"]]);
  assert.equal(popup.opener, null);
  assert.equal(typeof result.location.replace, "function");
});

test("does not run follow-up work when the browser blocks the blank window", async () => {
  const { withDetachedWindow } = await loadMediaActions();
  assert.equal(typeof withDetachedWindow, "function");
  let followUpCalls = 0;
  const result = await withDetachedWindow(() => null, async () => { followUpCalls += 1; });

  assert.deepEqual(result, { opened: false, value: undefined });
  assert.equal(followUpCalls, 0);
});

test("announces only the terminal no-URL preview state politely", async () => {
  const { previewStatusSemantics } = await loadMediaActions();

  assert.deepEqual(previewStatusSemantics({ status: "retry_exhausted" }), {
    role: "status",
    "aria-live": "polite",
  });
  assert.deepEqual(previewStatusSemantics({ status: "loading" }), {});
  assert.deepEqual(previewStatusSemantics({ status: "signing_failed" }), {});
});
