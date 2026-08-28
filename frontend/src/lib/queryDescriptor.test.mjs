import test from "node:test";
import assert from "node:assert/strict";

import {
  beginPendingQuery,
  clearQueryDescriptors,
  fileDescriptor,
  settleQueryFailure,
  settleQuerySuccess,
  speciesDescriptor,
  tagsDescriptor,
  thumbnailDescriptor,
} from "./queryDescriptor.mjs";

test("species descriptor uses the normalized submitted value", () => {
  const descriptor = speciesDescriptor("  WoMBat  ");
  assert.equal(descriptor.kind, "species");
  assert.deepEqual(descriptor.chips, [{ label: "Species", value: "wombat" }]);
  assert.equal(descriptor.summary, "Species: wombat");
});

test("species descriptor rejects empty input", () => {
  assert.equal(speciesDescriptor(""), null);
  assert.equal(speciesDescriptor("   "), null);
  assert.equal(speciesDescriptor(null), null);
  assert.equal(speciesDescriptor(42), null);
});

test("tags descriptor reflects the actual AND map with sorted chips", () => {
  const descriptor = tagsDescriptor({ wombat: 2, dingo: 1 });
  assert.equal(descriptor.kind, "tags");
  assert.deepEqual(descriptor.chips, [
    { label: "dingo", value: "≥ 1" },
    { label: "wombat", value: "≥ 2" },
  ]);
});

test("tags descriptor drops invalid entries and rejects empty maps", () => {
  assert.equal(tagsDescriptor({ wombat: 0, dingo: -1 }), null);
  assert.equal(tagsDescriptor({ "": 2 }), null);
  assert.equal(tagsDescriptor({}), null);
  assert.equal(tagsDescriptor(null), null);
  assert.equal(tagsDescriptor(["wombat"]), null);
});

test("file descriptor exposes only the file name", () => {
  const descriptor = fileDescriptor({ name: "  reference.jpg  ", size: 1024 });
  assert.deepEqual(descriptor.chips, [{ label: "Matched by image", value: "reference.jpg" }]);
  assert.equal(fileDescriptor({ name: " " }), null);
  assert.equal(fileDescriptor(null), null);
});

test("thumbnail descriptor is neutral and echoes no key or URL", () => {
  const descriptor = thumbnailDescriptor();
  assert.equal(descriptor.kind, "thumbnail");
  const serialized = JSON.stringify(descriptor);
  assert.equal(serialized.includes("http"), false);
  assert.equal(serialized.includes("thumbnails/"), false);
});

test("pending query keeps the last successful descriptor", () => {
  const previous = { lastSuccessfulDescriptor: speciesDescriptor("wombat"), pendingDescriptor: null };
  const next = beginPendingQuery(previous, speciesDescriptor("dingo"));
  assert.equal(next.lastSuccessfulDescriptor.summary, "Species: wombat");
  assert.equal(next.pendingDescriptor.summary, "Species: dingo");
});

test("success atomically promotes pending to last successful", () => {
  const pending = beginPendingQuery(
    { lastSuccessfulDescriptor: speciesDescriptor("wombat"), pendingDescriptor: null },
    speciesDescriptor("dingo")
  );
  const settled = settleQuerySuccess(pending, pending.pendingDescriptor);
  assert.equal(settled.lastSuccessfulDescriptor.summary, "Species: dingo");
  assert.equal(settled.pendingDescriptor, null);
});

test("success with no descriptor clears a previous successful descriptor", () => {
  const settled = settleQuerySuccess(
    { lastSuccessfulDescriptor: speciesDescriptor("wombat"), pendingDescriptor: null },
    null
  );
  assert.equal(settled.lastSuccessfulDescriptor, null);
  assert.equal(settled.pendingDescriptor, null);
});

test("failure clears pending but keeps the last successful descriptor", () => {
  const pending = beginPendingQuery(
    { lastSuccessfulDescriptor: speciesDescriptor("wombat"), pendingDescriptor: null },
    speciesDescriptor("dingo")
  );
  const settled = settleQueryFailure(pending);
  assert.equal(settled.lastSuccessfulDescriptor.summary, "Species: wombat");
  assert.equal(settled.pendingDescriptor, null);
});

test("failure with no prior success leaves both descriptors null", () => {
  const settled = settleQueryFailure(beginPendingQuery(undefined, speciesDescriptor("dingo")));
  assert.deepEqual(settled, { lastSuccessfulDescriptor: null, pendingDescriptor: null });
});

test("clear resets both descriptors", () => {
  assert.deepEqual(clearQueryDescriptors(), {
    lastSuccessfulDescriptor: null,
    pendingDescriptor: null,
  });
});
