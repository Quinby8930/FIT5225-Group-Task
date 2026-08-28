import assert from "node:assert/strict";
import test from "node:test";

import { handler } from "../index.mjs";

function event(origin) {
  return {
    headers: origin ? { Origin: origin } : {},
    requestContext: {
      authorizer: {
        jwt: {
          claims: { sub: "user-1", email: "user@example.com" },
        },
      },
    },
  };
}

test("echoes the requesting local or GitHub Pages origin", async () => {
  for (const origin of ["http://localhost:3000", "https://quinby8930.github.io"]) {
    const response = await handler(event(origin));
    assert.equal(response.headers["Access-Control-Allow-Origin"], origin);
  }
});

test("omits allow-origin for an origin outside the browser allowlist", async () => {
  const response = await handler(event("https://untrusted.example"));

  assert.equal("Access-Control-Allow-Origin" in response.headers, false);
});

test("keeps returning verified Cognito claims", async () => {
  const response = await handler(event("https://quinby8930.github.io"));

  assert.equal(response.statusCode, 200);
  assert.deepEqual(JSON.parse(response.body), {
    message: "Authorized request success",
    userId: "user-1",
    email: "user@example.com",
    givenName: null,
    familyName: null,
    claims: { sub: "user-1", email: "user@example.com" },
  });
});
