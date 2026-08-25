import { cognitoConfig } from "./cognitoConfig.js";

const TOKEN_STORAGE_KEY = "pacificBioArchive.tokens";
const PKCE_STORAGE_KEY = "pacificBioArchive.pkce";

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

export async function buildLoginUrl(identityProvider) {
  const codeVerifier = randomString(64);
  const codeChallenge = base64UrlEncode(await sha256(codeVerifier));
  const state = randomString(32);

  sessionStorage.setItem(
    PKCE_STORAGE_KEY,
    JSON.stringify({ codeVerifier, state })
  );

  const params = new URLSearchParams({
    client_id: cognitoConfig.clientId,
    response_type: "code",
    scope: cognitoConfig.scopes.join(" "),
    redirect_uri: cognitoConfig.redirectSignIn,
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
    state,
  });

  if (identityProvider) {
    params.set("identity_provider", identityProvider);
  }

  return `${cognitoConfig.domain}/oauth2/authorize?${params.toString()}`;
}

export async function buildSignUpUrl() {
  const url = new URL(`${cognitoConfig.domain}/signup`);
  url.searchParams.set("client_id", cognitoConfig.clientId);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", cognitoConfig.scopes.join(" "));
  url.searchParams.set("redirect_uri", cognitoConfig.redirectSignIn);
  return url.toString();
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

export async function handleAuthCallback(callbackUrl = window.location.href) {
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

  const pkce = JSON.parse(sessionStorage.getItem(PKCE_STORAGE_KEY) || "{}");
  if (!pkce.codeVerifier || pkce.state !== returnedState) {
    throw new Error("Invalid Cognito callback state. Please sign in again.");
  }

  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: cognitoConfig.clientId,
    code,
    redirect_uri: cognitoConfig.redirectSignIn,
    code_verifier: pkce.codeVerifier,
  });

  const response = await fetch(`${cognitoConfig.domain}/oauth2/token`, {
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
  sessionStorage.removeItem(PKCE_STORAGE_KEY);
  saveTokens(tokens);
  return tokens;
}

export function saveTokens(tokens) {
  localStorage.setItem(TOKEN_STORAGE_KEY, JSON.stringify(tokens));
}

export function getTokens() {
  return JSON.parse(localStorage.getItem(TOKEN_STORAGE_KEY) || "null");
}

export function clearTokens() {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
  sessionStorage.removeItem(PKCE_STORAGE_KEY);
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
  const [, payload] = token.split(".");
  if (!payload) {
    return null;
  }

  const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
  const decoded = atob(normalized);
  return JSON.parse(
    decodeURIComponent(
      decoded
        .split("")
        .map((char) => `%${char.charCodeAt(0).toString(16).padStart(2, "0")}`)
        .join("")
    )
  );
}

export function getCurrentUser() {
  const tokens = getTokens();
  if (!tokens?.id_token) {
    return null;
  }

  const user = parseJwt(tokens.id_token);
  if (!user?.exp || user.exp * 1000 <= Date.now()) {
    clearTokens();
    return null;
  }
  return user;
}
