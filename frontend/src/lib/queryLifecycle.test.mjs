import assert from "node:assert/strict";
import test from "node:test";

async function loadQueryLifecycle() {
  return import("./queryLifecycle.mjs").catch(() => ({}));
}

test("accepts only the latest query generation when responses resolve out of order", async () => {
  const { beginQuery, settleQuery } = await loadQueryLifecycle();
  assert.equal(typeof beginQuery, "function");
  assert.equal(typeof settleQuery, "function");

  const initial = { generation: 0, phase: "idle", result: null };
  const first = beginQuery(initial);
  const second = beginQuery(first);
  const stale = settleQuery(second, first.generation, { file_id: "A" }, "ready");
  const latest = settleQuery(stale, second.generation, { file_id: "B" }, "ready");

  assert.deepEqual(stale, second);
  assert.deepEqual(latest, { generation: 2, phase: "ready", result: { file_id: "B" } });
});

test("hides the result controls only before the first Explore query", async () => {
  const { shouldShowResultsHeader } = await loadQueryLifecycle();

  assert.equal(shouldShowResultsHeader("idle"), false);
  for (const phase of ["loading", "ready", "empty", "error"]) {
    assert.equal(shouldShowResultsHeader(phase), true, phase);
  }
});
