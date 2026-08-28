import { cognitoConfig } from "./cognitoConfig.js";

const TOKEN_STORAGE_KEY = "pacificBioArchive.tokens";
const PKCE_STORAGE_KEY = "pacificBioArchive.pkce";
const callbackExchanges = new Map();

function safeJson(value) {
  if (typeof value !== "string") return null;
  try { return JSON.parse(value); } catch { return null; }
}

export function decodeJwtSegment(segment) {
  if (typeof segment !== "string" || !segment || !/^[A-Za-z0-9_-]+$/.test(segment)) return null;
  try {
    const normalized = segment.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
    const decoded = Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
    const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(decoded));
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch { return null; }
}

function emitAuthChange(reason) {
  if (typeof globalThis.dispatchEvent !== "function") return;
  const event = typeof CustomEvent === "function"
    ? new CustomEvent("pacificBioArchive:auth", { detail: { reason } })
    : Object.assign(new Event("pacificBioArchive:auth"), { detail: { reason } });
  globalThis.dispatchEvent(event);
}

function base64UrlEncode(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });

  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function randomString(byteLength = 32) {
  const values = new Uint8Array(byteLength);
  crypto.getRandomValues(values);
  return base64UrlEncode(values);
}

async function sha256(value) {
  const encoded = new TextEncoder().encode(value);
  return crypto.subtle.digest("SHA-256", encoded);
}

async function buildAuthorizationUrl(pathname, identityProvider, config) {
  const codeVerifier = randomString(64);
  const codeChallenge = base64UrlEncode(await sha256(codeVerifier));
  const state = randomString(32);

  sessionStorage.setItem(
    PKCE_STORAGE_KEY,
    JSON.stringify({ codeVerifier, state })
  );

  const params = new URLSearchParams({
    client_id: config.clientId,
    response_type: "code",
    scope: config.scopes.join(" "),
    redirect_uri: config.redirectSignIn,
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
    state,
  });

  if (identityProvider) {
    params.set("identity_provider", identityProvider);
  }

  return `${config.domain}${pathname}?${params.toString()}`;
}

export function buildLoginUrl(identityProvider, config = cognitoConfig) {
  return buildAuthorizationUrl("/oauth2/authorize", identityProvider, config);
}

export function buildSignUpUrl(config = cognitoConfig) {
  return buildAuthorizationUrl("/signup", undefined, config);
}

export async function signIn() {
  window.location.assign(await buildLoginUrl());
}

export async function signUp() {
  window.location.assign(await buildSignUpUrl());
}

export async function signInWithProvider(providerName) {
  window.location.assign(await buildLoginUrl(providerName));
}

export async function signInWithGoogle() {
  return signInWithProvider(cognitoConfig.externalProviders.google);
}

export function buildLogoutUrl() {
  clearTokens();

  const params = new URLSearchParams({
    client_id: cognitoConfig.clientId,
    logout_uri: cognitoConfig.redirectSignOut,
  });

  return `${cognitoConfig.domain}/logout?${params.toString()}`;
}

export function signOut() {
  window.location.assign(buildLogoutUrl());
}

export async function handleAuthCallback(
  callbackUrl = window.location.href,
  config = cognitoConfig
) {
  const url = new URL(callbackUrl);
  const code = url.searchParams.get("code");
  const returnedState = url.searchParams.get("state");
  const error = url.searchParams.get("error");

  if (error) {
    throw new Error(`${error}: ${url.searchParams.get("error_description")}`);
  }

  if (!code) {
    throw new Error("Cognito callback did not include an authorization code.");
  }

  const existingExchange = callbackExchanges.get(code);
  if (existingExchange) {
    if (existingExchange.state !== returnedState) {
      throw new Error("Invalid Cognito callback state. Please sign in again.");
    }
    return existingExchange.promise;
  }

  const rawPkce = sessionStorage.getItem(PKCE_STORAGE_KEY);
  const pkce = safeJson(rawPkce) || {};
  if (rawPkce !== null && !safeJson(rawPkce)) sessionStorage.removeItem(PKCE_STORAGE_KEY);
  if (!pkce.codeVerifier || pkce.state !== returnedState) {
    throw new Error("Invalid Cognito callback state. Please sign in again.");
  }

  const exchange = Promise.resolve().then(async () => {
    const body = new URLSearchParams({
      grant_type: "authorization_code",
      client_id: config.clientId,
      code,
      redirect_uri: config.redirectSignIn,
      code_verifier: pkce.codeVerifier,
    });

    const response = await fetch(`${config.domain}/oauth2/token`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body,
    });

    if (!response.ok) {
      const message = await response.text();
      throw new Error(`Token exchange failed: ${message}`);
    }

    const tokens = await response.json();
    saveTokens(tokens);
    sessionStorage.removeItem(PKCE_STORAGE_KEY);
    return tokens;
  });
  const retryableExchange = exchange.catch((error) => {
    callbackExchanges.delete(code);
    throw error;
  });
  callbackExchanges.set(code, { state: returnedState, promise: retryableExchange });
  return retryableExchange;
}

export function saveTokens(tokens) {
  localStorage.setItem(TOKEN_STORAGE_KEY, JSON.stringify(tokens));
  emitAuthChange("authenticated");
}

export function getTokens() {
  const tokens = readStoredTokens();
  if (!tokens || typeof tokens !== "object" || Array.isArray(tokens)) {
    if (localStorage.getItem(TOKEN_STORAGE_KEY) !== null) clearTokens("invalid");
    return null;
  }
  return tokens;
}

export function readStoredTokens() { return safeJson(localStorage.getItem(TOKEN_STORAGE_KEY)); }

export function clearTokens(reason = "signed_out") {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
  sessionStorage.removeItem(PKCE_STORAGE_KEY);
  emitAuthChange(reason);
}

export function getAuthorizationHeader() {
  const tokens = getTokens();
  if (!tokens?.id_token) {
    return {};
  }

  return {
    Authorization: `Bearer ${tokens.id_token}`,
  };
}

export function parseJwt(token) {
  if (typeof token !== "string") return null;
  const parts = token.split(".");
  if (parts.length !== 3 || !parts[2] || !/^[A-Za-z0-9_-]+$/.test(parts[2])) return null;
  const header = decodeJwtSegment(parts[0]);
  const payload = decodeJwtSegment(parts[1]);
  return header && payload ? payload : null;
}

export function inspectAuthSession(tokens, now = Date.now()) {
  if (!tokens || typeof tokens !== "object" || Array.isArray(tokens) || typeof tokens.id_token !== "string") return { user: null, reason: "invalid", shouldClear: Boolean(tokens) };
  const user = parseJwt(tokens.id_token);
  if (!user) return { user: null, reason: "invalid", shouldClear: true };
  if (!Number.isFinite(user.exp) || user.exp <= 0) return { user: null, reason: "invalid", shouldClear: true };
  if (user.exp * 1000 <= now) return { user: null, reason: "expired", shouldClear: true };
  return { user, reason: null, shouldClear: false };
}

export function inspectStoredAuthSession(now = Date.now()) {
  const raw = localStorage.getItem(TOKEN_STORAGE_KEY);
  if (raw === null) return { user: null, reason: null, shouldClear: false };
  const tokens = safeJson(raw);
  if (!tokens || typeof tokens !== "object" || Array.isArray(tokens)) return { user: null, reason: "invalid", shouldClear: true };
  return inspectAuthSession(tokens, now);
}

export function getCurrentUser() {
  const tokens = getTokens();
  const inspected = inspectAuthSession(tokens);
  if (inspected.shouldClear) clearTokens(inspected.reason);
  return inspected.user;
}
