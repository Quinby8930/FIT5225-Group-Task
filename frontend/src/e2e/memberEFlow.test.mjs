import test from "node:test";
import assert from "node:assert/strict";

function storage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

function makeToken(payload) {
  const encode = (value) =>
    Buffer.from(JSON.stringify(value))
      .toString("base64url")
      .replace(/=/g, "");
  return `${encode({ alg: "none" })}.${encode(payload)}.`;
}

test("member E browser flow calls the agreed upload, query, edit, delete and notification APIs", async () => {
  globalThis.localStorage = storage();
  globalThis.sessionStorage = storage();
  globalThis.btoa = (value) => Buffer.from(value, "binary").toString("base64");
  globalThis.atob = (value) => Buffer.from(value, "base64").toString("binary");
  globalThis.localStorage.setItem(
    "pacificBioArchive.tokens",
    JSON.stringify({
      id_token: makeToken({
        sub: "user-123",
        email: "member-e@example.com",
        exp: Math.floor(Date.now() / 1000) + 3600,
      }),
    })
  );

  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });

    if (String(url).endsWith("/upload-url")) {
      assert.equal(options.headers.Authorization.startsWith("Bearer "), true);
      assert.deepEqual(JSON.parse(options.body), {
        filename: "wombat.jpg",
        content_type: "image/jpeg",
        size_bytes: 5,
        checksum_sha256: "dPgf4WfZm0y0HW0MzagieMrunz4vJdXlo5Nv89zsYNA=",
      });
      return new Response(
        JSON.stringify({
          file_id: "file-1",
          object_key: "originals/user-123/file-1/wombat.jpg",
          upload_url: "https://upload.example.test/presigned",
          required_headers: {
            "Content-Type": "image/jpeg",
            "x-amz-checksum-sha256": "dPgf4WfZm0y0HW0MzagieMrunz4vJdXlo5Nv89zsYNA=",
          },
        }),
        { status: 200 }
      );
    }

    if (String(url) === "https://upload.example.test/presigned") {
      assert.equal(options.method, "PUT");
      assert.equal(options.headers["Content-Type"], "image/jpeg");
      return new Response("", { status: 200 });
    }

    if (String(url).endsWith("/query/by-tags")) {
      assert.deepEqual(JSON.parse(options.body), { tags: { wombat: 1 } });
      return new Response(
        JSON.stringify({ results: ["thumbnails/user-123/file-1/thumbnail.jpg"], count: 1 }),
        { status: 200 }
      );
    }

    if (String(url).endsWith("/asset-urls")) {
      assert.equal(options.headers.Authorization.startsWith("Bearer "), true);
      assert.deepEqual(JSON.parse(options.body), {
        keys: ["thumbnails/user-123/file-1/thumbnail.jpg"],
      });
      return new Response(
        JSON.stringify({
          assets: [{
            key: "thumbnails/user-123/file-1/thumbnail.jpg",
            url: "https://signed.example.test/thumbnail.jpg",
            expires_in: 900,
          }],
        }),
        { status: 200 }
      );
    }

    if (String(url).endsWith("/tags/edit")) {
      assert.deepEqual(JSON.parse(options.body), {
        keys: ["originals/user-123/file-1/wombat.jpg"],
        tags: ["wombat"],
        operation: 1,
      });
      return new Response(JSON.stringify({ updated: 1 }), { status: 200 });
    }

    if (String(url).endsWith("/files/delete")) {
      assert.deepEqual(JSON.parse(options.body), {
        keys: ["originals/user-123/file-1/wombat.jpg"],
      });
      return new Response(
        JSON.stringify({ deleted_db_records: 1, storage_objects_removed: 2 }),
        { status: 200 }
      );
    }

    if (String(url).endsWith("/notifications/subscribe") && options.method === "POST") {
      assert.deepEqual(JSON.parse(options.body), {
        species: "wombat",
      });
      return new Response(
        JSON.stringify({ user_id: "user-123", species: "wombat", subscribed: true }),
        { status: 201 }
      );
    }

    if (String(url).endsWith("/notifications/subscribe?species=wombat")) {
      assert.equal(options.method, "DELETE");
      assert.equal(String(url).includes("user_id"), false);
      return new Response(JSON.stringify({ species: "wombat", subscribed: false }), { status: 200 });
    }

    if (String(url).endsWith("/notifications/subscriptions")) {
      assert.equal(String(url).includes("user_id"), false);
      return new Response(JSON.stringify({ species: ["wombat"] }), { status: 200 });
    }

    if (String(url).endsWith("/notifications")) {
      assert.equal(String(url).includes("user_id"), false);
      return new Response(JSON.stringify({ notifications: [] }), { status: 200 });
    }

    throw new Error(`Unexpected fetch ${url}`);
  };

  const mediaApi = await import("../api/mediaApi.js");
  const file = new File([new Uint8Array([1, 2, 3, 4, 5])], "wombat.jpg", {
    type: "image/jpeg",
  });

  const uploaded = await mediaApi.uploadMedia(file);
  assert.equal(uploaded.file_id, "file-1");
  assert.deepEqual(await mediaApi.queryByTags({ wombat: 1 }), {
    results: ["thumbnails/user-123/file-1/thumbnail.jpg"],
    count: 1,
  });
  assert.deepEqual(
    await mediaApi.requestAssetUrls(["thumbnails/user-123/file-1/thumbnail.jpg"]),
    {
      assets: [{
        key: "thumbnails/user-123/file-1/thumbnail.jpg",
        url: "https://signed.example.test/thumbnail.jpg",
        expires_in: 900,
      }],
    }
  );
  assert.deepEqual(
    await mediaApi.editTags(["originals/user-123/file-1/wombat.jpg"], ["wombat"], 1),
    { updated: 1 }
  );
  assert.deepEqual(await mediaApi.deleteFiles(["originals/user-123/file-1/wombat.jpg"]), {
    deleted_db_records: 1,
    storage_objects_removed: 2,
  });
  assert.deepEqual(await mediaApi.subscribeToSpecies("wombat"), {
    user_id: "user-123",
    species: "wombat",
    subscribed: true,
  });
  assert.deepEqual(await mediaApi.unsubscribeFromSpecies("wombat"), {
    species: "wombat",
    subscribed: false,
  });
  assert.deepEqual(await mediaApi.listSubscriptions(), { species: ["wombat"] });
  assert.deepEqual(await mediaApi.listNotifications(), { notifications: [] });
  assert.equal(calls.length, 10);
});
