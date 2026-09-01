import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, apiRequest, isDuplicateFileError } from "./apiClient.js";
import * as cognitoConfig from "../auth/cognitoConfig.js";

function storage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

function installStorage() {
  globalThis.localStorage = storage();
  globalThis.sessionStorage = storage();
}

async function captureApiError(response) {
  globalThis.fetch = async () => response;
  return apiRequest("/test").then(
    () => assert.fail("expected apiRequest to reject"),
    (error) => error
  );
}

test("top-level backend error code and payload survive the API boundary", async () => {
  installStorage();
  const payload = {
    code: "DUPLICATE_FILE",
    message: "The checksum already exists.",
    existing_file_id: "file-1",
  };

  const error = await captureApiError(new Response(JSON.stringify(payload), { status: 409 }));

  assert.ok(error instanceof ApiError);
  assert.equal(error.message, "The checksum already exists.");
  assert.equal(error.status, 409);
  assert.equal(error.code, "DUPLICATE_FILE");
  assert.deepEqual(error.payload, payload);
});

test("FastAPI nested detail code and payload survive the API boundary", async () => {
  installStorage();
  const payload = {
    detail: {
      code: "FORBIDDEN_OWNER",
      message: "Media is owned by another user.",
    },
  };

  const error = await captureApiError(new Response(JSON.stringify(payload), { status: 403 }));

  assert.ok(error instanceof ApiError);
  assert.equal(error.message, "Media is owned by another user.");
  assert.equal(error.status, 403);
  assert.equal(error.code, "FORBIDDEN_OWNER");
  assert.deepEqual(error.payload, payload);
});

test("FastAPI validation errors become a useful query input message", async () => {
  installStorage();
  const payload = {
    detail: [
      {
        type: "missing",
        loc: ["body", "file"],
        msg: "Field required",
        input: null,
      },
    ],
  };

  const error = await captureApiError(
    new Response(JSON.stringify(payload), { status: 422 })
  );

  assert.ok(error instanceof ApiError);
  assert.equal(error.status, 422);
  assert.equal(
    error.message,
    "Check the selected file, tags, or thumbnail reference and try again."
  );
});

test("non-JSON server failure becomes a controlled ApiError", async () => {
  installStorage();

  const error = await captureApiError(new Response("upstream unavailable", { status: 503 }));

  assert.ok(error instanceof ApiError);
  assert.equal(error.message, "API request failed: 503");
  assert.equal(error.status, 503);
  assert.equal(error.code, null);
  assert.equal(error.payload, "upstream unavailable");
});

test("401 response clears locally stored tokens before rejecting", async () => {
  installStorage();
  localStorage.setItem("pacificBioArchive.tokens", JSON.stringify({ id_token: "expired" }));

  const error = await captureApiError(new Response(JSON.stringify({
    detail: { code: "NOT_AUTHENTICATED", message: "Sign in again." },
  }), { status: 401 }));

  assert.ok(error instanceof ApiError);
  assert.equal(error.status, 401);
  assert.equal(localStorage.getItem("pacificBioArchive.tokens"), null);
});

test("duplicate upload decisions use the structured backend code only", () => {
  assert.equal(isDuplicateFileError(new ApiError("checksum conflict", {
    status: 409,
    code: "DUPLICATE_FILE",
    payload: { code: "DUPLICATE_FILE" },
  })), true);
  assert.equal(isDuplicateFileError(new Error("DUPLICATE_FILE appeared in prose")), false);
});

test("configured API bases remove trailing slashes before paths are appended", () => {
  assert.equal(typeof cognitoConfig.normalizeApiBaseUrl, "function");
  assert.equal(
    cognitoConfig.normalizeApiBaseUrl(
      "https://2dd2aqb32j.execute-api.ap-southeast-2.amazonaws.com/dev///"
    ),
    "https://2dd2aqb32j.execute-api.ap-southeast-2.amazonaws.com/dev"
  );
});

test("default API requests use the complete dev-stage cloud URL", async () => {
  installStorage();
  let requestedUrl = null;
  globalThis.fetch = async (url) => {
    requestedUrl = String(url);
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  await apiRequest("/stage-contract");

  assert.equal(
    requestedUrl,
    "https://2dd2aqb32j.execute-api.ap-southeast-2.amazonaws.com/dev/stage-contract"
  );
});
