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

test("assigns a distinct opaque identity to A1, B, and a later A2 session", async () => {
  const { advanceSessionIdentity } = await import("./viewState.mjs");
  const a1 = advanceSessionIdentity(undefined, "member-a");
  const b = advanceSessionIdentity(a1, "member-b");
  const a2 = advanceSessionIdentity(b, "member-a");

  assert.deepEqual([a1.generation, b.generation, a2.generation], [1, 2, 3]);
  assert.equal(typeof a1.key, "string");
  assert.notEqual(a1.key, "");
  assert.notEqual(a1.key, b.key);
  assert.notEqual(a1.key, a2.key);
  assert.equal(a1.key.includes("member-a"), false);
  assert.equal(a2.key.includes("member-a"), false);
});

test("projects no query data, status, selection, or raw keys across A1 to B to A2", async () => {
  const { advanceSessionIdentity, projectSessionViewState } = await import("./viewState.mjs");
  assert.equal(typeof projectSessionViewState, "function");
  const a1 = advanceSessionIdentity(undefined, "member-a");
  const b = advanceSessionIdentity(a1, "member-b");
  const a2 = advanceSessionIdentity(b, "member-a");
  const a1State = {
    activeView: "manage",
    statuses: { explore: { type: "success", message: "A results" } },
    query: {
      items: [{ file_id: "a-file", original_key: "originals/member-a/a-file/private.jpg" }],
      structuredItems: [{ file_id: "a-file", original_key: "originals/member-a/a-file/private.jpg" }],
      legacyItems: [],
      count: 1,
    },
    queryState: "ready",
    descriptors: {
      lastSuccessfulDescriptor: { kind: "species", summary: "Species: wombat" },
      pendingDescriptor: null,
    },
    selectedFileIds: ["a-file"],
  };

  for (const activeIdentity of [b.key, a2.key]) {
    const projected = projectSessionViewState(a1.key, activeIdentity, a1State);
    assert.deepEqual(projected, {
      activeView: "home",
      statuses: {},
      query: { items: [], structuredItems: [], legacyItems: [], count: 0 },
      queryState: "idle",
      descriptors: { lastSuccessfulDescriptor: null, pendingDescriptor: null },
      selectedFileIds: [],
    });
    assert.equal(JSON.stringify(projected).includes("originals/member-a"), false);
    assert.equal(JSON.stringify(projected).includes("A results"), false);
  }

  assert.equal(projectSessionViewState(a1.key, a1.key, a1State), a1State);
});
