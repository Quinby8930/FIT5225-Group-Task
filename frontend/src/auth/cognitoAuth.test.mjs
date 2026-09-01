import assert from "node:assert/strict";
import test from "node:test";

import {
  buildLoginUrl,
  buildSignUpUrl,
  clearTokens,
  decodeJwtSegment,
  getCurrentUser,
  getTokens,
  handleAuthCallback,
  parseJwt,
  inspectAuthSession,
  inspectStoredAuthSession,
  saveTokens,
  signInWithGoogle,
} from "./cognitoAuth.js";

const PKCE_STORAGE_KEY = "pacificBioArchive.pkce";
const TEST_CONFIG = {
  clientId: "placeholder-client-id",
  domain: "https://auth.example.test",
  redirectSignIn: "https://app.example.test/callback",
  redirectSignOut: "https://app.example.test/logout",
  scopes: ["openid", "email", "profile"],
  externalProviders: { google: "ExampleProvider" },
};

function storage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

function installBrowserStubs() {
  globalThis.localStorage = storage();
  globalThis.sessionStorage = storage();
  globalThis.btoa = (value) => Buffer.from(value, "binary").toString("base64");
  const events = new EventTarget();
  globalThis.addEventListener = events.addEventListener.bind(events);
  globalThis.removeEventListener = events.removeEventListener.bind(events);
  globalThis.dispatchEvent = events.dispatchEvent.bind(events);
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: {
      getRandomValues(values) {
        values.forEach((_, index) => {
          values[index] = (index + 17) % 256;
        });
        return values;
      },
      subtle: {
        async digest(_algorithm, value) {
          const source = new Uint8Array(value);
          return Uint8Array.from({ length: 32 }, (_, index) => source[index % source.length] ^ 0x5a);
        },
      },
    },
  });
}

function assertPkceAuthorizationUrl(rawUrl, pathname) {
  const url = new URL(rawUrl);
  const transaction = JSON.parse(sessionStorage.getItem(PKCE_STORAGE_KEY));

  assert.equal(url.pathname, pathname);
  assert.equal(url.origin, TEST_CONFIG.domain);
  assert.equal(url.searchParams.get("client_id"), TEST_CONFIG.clientId);
  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("scope"), "openid email profile");
  assert.equal(url.searchParams.get("redirect_uri"), TEST_CONFIG.redirectSignIn);
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
  assert.ok(url.searchParams.get("code_challenge"));
  assert.equal(url.searchParams.get("state"), transaction.state);
  assert.ok(transaction.codeVerifier);
}

test("login and signup create complete PKCE S256 authorization transactions", async () => {
  installBrowserStubs();
  assertPkceAuthorizationUrl(
    await buildLoginUrl(undefined, TEST_CONFIG),
    "/oauth2/authorize"
  );

  installBrowserStubs();
  assertPkceAuthorizationUrl(await buildSignUpUrl(TEST_CONFIG), "/signup");
});

test("Google login sends Cognito the Google identity provider hint", async () => {
  installBrowserStubs();
  let assignedUrl = null;
  globalThis.window = {
    location: {
      assign(url) { assignedUrl = String(url); },
    },
  };

  await signInWithGoogle();

  assert.equal(new URL(assignedUrl).searchParams.get("identity_provider"), "Google");
});

test("concurrent callback handling shares one token exchange per authorization code", async () => {
  installBrowserStubs();
  sessionStorage.setItem(PKCE_STORAGE_KEY, JSON.stringify({
    codeVerifier: "verifier-for-code-1",
    state: "state-1",
  }));
  const tokens = { id_token: "id-token", access_token: "access-token" };
  let requests = 0;
  globalThis.fetch = async (url, options) => {
    requests += 1;
    assert.equal(String(url), `${TEST_CONFIG.domain}/oauth2/token`);
    assert.equal(options.method, "POST");
    assert.equal(options.headers["Content-Type"], "application/x-www-form-urlencoded");
    assert.equal(options.body.get("grant_type"), "authorization_code");
    assert.equal(options.body.get("client_id"), TEST_CONFIG.clientId);
    assert.equal(options.body.get("code"), "code-1");
    assert.equal(options.body.get("redirect_uri"), TEST_CONFIG.redirectSignIn);
    assert.equal(options.body.get("code_verifier"), "verifier-for-code-1");
    await Promise.resolve();
    return new Response(JSON.stringify(tokens), { status: 200 });
  };

  const callbackUrl = `${TEST_CONFIG.redirectSignIn}?code=code-1&state=state-1`;
  const [first, second] = await Promise.all([
    handleAuthCallback(callbackUrl, TEST_CONFIG),
    handleAuthCallback(callbackUrl, TEST_CONFIG),
  ]);

  assert.equal(requests, 1);
  assert.strictEqual(first, second);
  assert.deepEqual(JSON.parse(localStorage.getItem("pacificBioArchive.tokens")), tokens);
  assert.equal(sessionStorage.getItem(PKCE_STORAGE_KEY), null);
});

test("invalid callback state does not make a token request", async () => {
  installBrowserStubs();
  sessionStorage.setItem(PKCE_STORAGE_KEY, JSON.stringify({
    codeVerifier: "verifier-for-code-2",
    state: "expected-state",
  }));
  let requests = 0;
  globalThis.fetch = async () => {
    requests += 1;
    return new Response("{}", { status: 200 });
  };

  await assert.rejects(
    handleAuthCallback(
      "https://app.example.test/callback?code=code-2&state=wrong-state",
      TEST_CONFIG
    ),
    /Invalid Cognito callback state/
  );
  assert.equal(requests, 0);
});

test("failed token exchange retains PKCE and can retry the same code", async () => {
  installBrowserStubs();
  const transaction = {
    codeVerifier: "verifier-for-code-3",
    state: "state-3",
  };
  sessionStorage.setItem(PKCE_STORAGE_KEY, JSON.stringify(transaction));
  let requests = 0;
  globalThis.fetch = async (_url, options) => {
    requests += 1;
    assert.equal(options.body.get("code_verifier"), transaction.codeVerifier);
    if (requests === 1) {
      return new Response("temporary failure", { status: 503 });
    }
    return new Response(JSON.stringify({ id_token: "retry-token" }), { status: 200 });
  };

  const callbackUrl = `${TEST_CONFIG.redirectSignIn}?code=code-3&state=state-3`;
  await assert.rejects(
    handleAuthCallback(callbackUrl, TEST_CONFIG),
    /Token exchange failed/
  );
  assert.deepEqual(JSON.parse(sessionStorage.getItem(PKCE_STORAGE_KEY)), transaction);

  assert.deepEqual(
    await handleAuthCallback(callbackUrl, TEST_CONFIG),
    { id_token: "retry-token" }
  );
  assert.equal(requests, 2);
  assert.equal(sessionStorage.getItem(PKCE_STORAGE_KEY), null);
});

function tokenFor(payload) {
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${encode({ alg: "none" })}.${encode(payload)}.signature`;
}

test("damaged token and PKCE storage are safe and expired identities clear tokens", () => {
  installBrowserStubs();
  localStorage.setItem("pacificBioArchive.tokens", "not-json");
  sessionStorage.setItem(PKCE_STORAGE_KEY, "not-json");
  assert.equal(getTokens(), null);
  assert.equal(parseJwt("not-a-jwt"), null);
  assert.equal(getCurrentUser(), null);
  assert.equal(localStorage.getItem("pacificBioArchive.tokens"), null);

  saveTokens({ id_token: tokenFor({ sub: "u1", exp: Math.floor(Date.now() / 1000) - 1 }) });
  assert.equal(getCurrentUser(), null);
  assert.equal(localStorage.getItem("pacificBioArchive.tokens"), null);
});

test("token lifecycle emits opaque same-page auth reasons", () => {
  installBrowserStubs();
  const reasons = [];
  const listener = (event) => reasons.push(event.detail.reason);
  globalThis.addEventListener?.("pacificBioArchive:auth", listener);
  saveTokens({ id_token: tokenFor({ sub: "u1", exp: Math.floor(Date.now() / 1000) + 3600 }) });
  clearTokens("expired");
  globalThis.removeEventListener?.("pacificBioArchive:auth", listener);
  assert.deepEqual(reasons, ["authenticated", "expired"]);
});

test("JWT parsing requires valid non-empty header payload and signature segments", () => {
  installBrowserStubs();
  const valid = tokenFor({ sub: "u1", exp: Math.floor(Date.now() / 1000) + 60 });
  assert.ok(decodeJwtSegment(valid.split(".")[0]));
  assert.equal(parseJwt(`.${valid.split(".")[1]}.sig`), null);
  assert.equal(parseJwt(`${valid.split(".")[0]}.${valid.split(".")[1]}.`), null);
  assert.equal(parseJwt(`bad!.${valid.split(".")[1]}.sig`), null);
  assert.equal(parseJwt(`${valid.split(".")[0]}.${valid.split(".")[1]}.bad!`), null);
});

test("inspectAuthSession is read-only and identifies an expired token", () => {
  installBrowserStubs();
  const tokens = { id_token: tokenFor({ sub: "u1", exp: 1 }) };
  const before = localStorage.getItem("pacificBioArchive.tokens");
  assert.deepEqual(inspectAuthSession(tokens, 2_000), { user: null, reason: "expired", shouldClear: true });
  assert.equal(localStorage.getItem("pacificBioArchive.tokens"), before);
});

test("inspectStoredAuthSession distinguishes no token from malformed stored JSON", () => {
  installBrowserStubs();
  assert.deepEqual(inspectStoredAuthSession(), { user: null, reason: null, shouldClear: false });
  localStorage.setItem("pacificBioArchive.tokens", "not-json");
  assert.deepEqual(inspectStoredAuthSession(), { user: null, reason: "invalid", shouldClear: true });
});

test("damaged PKCE storage is removed without an OAuth request", async () => {
  installBrowserStubs();
  sessionStorage.setItem(PKCE_STORAGE_KEY, "broken-json");
  let requests = 0;
  globalThis.fetch = async () => { requests += 1; return new Response("{}", { status: 200 }); };
  await assert.rejects(handleAuthCallback(`${TEST_CONFIG.redirectSignIn}?code=broken&state=s`), /Invalid Cognito callback state/);
  assert.equal(requests, 0);
  assert.equal(sessionStorage.getItem(PKCE_STORAGE_KEY), null);
});
