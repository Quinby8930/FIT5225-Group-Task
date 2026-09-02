import assert from "node:assert/strict";
import test from "node:test";

import { ApiError } from "../api/apiClient.js";
import {
  consumeExploreSpeciesRequest,
  copyDuplicateFileId,
  createExploreSpeciesRequest,
  duplicateCardModel,
  duplicateFailureForError,
} from "./duplicateUpload.mjs";

function duplicateError(tags = { cat: 1 }) {
  return new ApiError("duplicate", {
    status: 409,
    code: "DUPLICATE_FILE",
    payload: {
      code: "DUPLICATE_FILE",
      existing_file_id: "736f684a-b448-4184-a452-b24f3d304ce8",
      tags,
    },
  });
}

test("duplicate card model exposes the complete id and current tag counts", () => {
  const failure = duplicateFailureForError(
    duplicateError({ wombat: 2, cat: 1 }),
  );
  const card = duplicateCardModel(failure.duplicate, "image/jpeg");

  assert.equal(failure.message, null);
  assert.deepEqual(card, {
    heading: "This image already exists",
    guidance: "No new file was uploaded.",
    fileId: "736f684a-b448-4184-a452-b24f3d304ce8",
    tagRows: [
      { species: "cat", count: 1, label: "cat × 1" },
      { species: "wombat", count: 2, label: "wombat × 2" },
    ],
    exploreActions: ["cat", "wombat"],
    emptyTags: false,
  });
});

test("duplicate card copies the complete file id", async () => {
  const copied = [];
  await copyDuplicateFileId(
    "736f684a-b448-4184-a452-b24f3d304ce8",
    { writeText: async (value) => copied.push(value) },
  );

  assert.deepEqual(copied, ["736f684a-b448-4184-a452-b24f3d304ce8"]);
});

test("empty duplicate tags produce a plain Explore fallback", () => {
  const failure = duplicateFailureForError(duplicateError({}));
  const card = duplicateCardModel(failure.duplicate, "video/mp4");

  assert.equal(card.heading, "This file already exists");
  assert.deepEqual(card.tagRows, []);
  assert.deepEqual(card.exploreActions, []);
  assert.equal(card.emptyTags, true);
});

test("a duplicate species request fills Explore and runs exactly once", () => {
  const request = createExploreSpeciesRequest(4, "session-a", "cat");
  const speciesValues = [];
  const queries = [];
  const consumed = [];
  const handlers = {
    sessionKey: "session-a",
    onSpecies: (species) => speciesValues.push(species),
    onQuery: (species) => queries.push(species),
    onConsumed: (requestId) => consumed.push(requestId),
  };

  const first = consumeExploreSpeciesRequest(request, {
    ...handlers,
    lastRequestId: null,
  });
  const second = consumeExploreSpeciesRequest(request, {
    ...handlers,
    lastRequestId: first,
  });

  assert.deepEqual(request, { id: 5, sessionKey: "session-a", species: "cat" });
  assert.equal(first, 5);
  assert.equal(second, 5);
  assert.deepEqual(speciesValues, ["cat"]);
  assert.deepEqual(queries, ["cat"]);
  assert.deepEqual(consumed, [5]);
});

test("an Explore request from another session cannot run", () => {
  const calls = [];
  const lastRequestId = consumeExploreSpeciesRequest(
    { id: 8, sessionKey: "old-session", species: "wombat" },
    {
      sessionKey: "new-session",
      lastRequestId: null,
      onSpecies: () => calls.push("species"),
      onQuery: () => calls.push("query"),
      onConsumed: () => calls.push("consumed"),
    },
  );

  assert.equal(lastRequestId, null);
  assert.deepEqual(calls, []);
});

test("non-duplicate upload errors retain the existing generic error path", () => {
  const error = new ApiError("Upload service is unavailable", {
    status: 503,
    code: "DEPENDENCY_UNAVAILABLE",
  });

  assert.deepEqual(duplicateFailureForError(error), {
    duplicate: null,
    message: "Upload service is unavailable",
  });
});
