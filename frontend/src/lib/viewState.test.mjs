import assert from "node:assert/strict";
import test from "node:test";

test("keeps an async status with its source view", async () => {
  const { setStatusForView, statusForView } = await import("./viewState.mjs");
  const afterExplore = setStatusForView({}, "explore", { type: "info", message: "Searching" });
  const afterUpload = setStatusForView(afterExplore, "upload", { type: "success", message: "Queued" });

  assert.deepEqual(statusForView(afterUpload, "explore"), { type: "info", message: "Searching" });
  assert.deepEqual(statusForView(afterUpload, "upload"), { type: "success", message: "Queued" });
  assert.equal(statusForView(afterUpload, "manage"), null);
});

test("resets navigation and statuses when the authenticated identity changes", async () => {
  const { resetSessionViewState } = await import("./viewState.mjs");
  assert.deepEqual(resetSessionViewState(), {
    activeView: "home",
    statuses: {},
  });
});

test("navigates internal views from the top of the document", async () => {
  const { navigateToView } = await import("./viewState.mjs");
  const calls = [];

  navigateToView("explore", {
    setActiveView: (view) => calls.push(["view", view]),
    scrollTo: (options) => calls.push(["scroll", options]),
  });

  assert.deepEqual(calls, [
    ["view", "explore"],
    ["scroll", { top: 0, left: 0, behavior: "auto" }],
  ]);
});

test("reconciles the Explore status after deleting items from its result set", async () => {
  const { queryStatusAfterDeletion } = await import("./viewState.mjs");
  assert.deepEqual(queryStatusAfterDeletion(2), {
    type: "info",
    message: "2 result(s) remain after deletion.",
  });
  assert.deepEqual(queryStatusAfterDeletion(0), {
    type: "info",
    message: "No media remain in this result set.",
  });
});
