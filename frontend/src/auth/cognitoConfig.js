const env = import.meta.env || {};
const DEFAULT_LOCAL_ORIGIN = "http://localhost:3000";

export function normalizeApiBaseUrl(baseUrl) {
  return baseUrl.replace(/\/+$/, "");
}

export function normalizeBasePath(path = "/") {
  if (!path || path === "./") return "/";
  const withLeadingSlash = path.startsWith("/") ? path : `/${path}`;
  return withLeadingSlash.endsWith("/") ? withLeadingSlash : `${withLeadingSlash}/`;
}

export function joinBasePath(basePath, segment = "") {
  const cleanSegment = String(segment).replace(/^\/+/, "");
  return `${normalizeBasePath(basePath)}${cleanSegment}`;
}

function currentOrigin() {
  return typeof window !== "undefined" && window.location?.origin
    ? window.location.origin
    : DEFAULT_LOCAL_ORIGIN;
}

const basePath = normalizeBasePath(env.BASE_URL || "/");

export const appConfig = {
  basePath,
  homePath: basePath,
  callbackPath: joinBasePath(basePath, "callback"),
  logoutPath: joinBasePath(basePath, "logout"),
};

function browserRedirectUrl(path) {
  return `${currentOrigin()}${path}`;
}

export const cognitoConfig = {
  region: env.VITE_COGNITO_REGION || "ap-southeast-2",
  userPoolId: env.VITE_COGNITO_USER_POOL_ID || "ap-southeast-2_1hGEJyYO7",
  clientId: env.VITE_COGNITO_CLIENT_ID || "65dgspco2djehpbpunc13t2oml",
  domain:
    env.VITE_COGNITO_DOMAIN ||
      "https://ap-southeast-21hgejyyo7.auth.ap-southeast-2.amazoncognito.com",
  redirectSignIn:
    env.VITE_COGNITO_REDIRECT_SIGN_IN ||
    browserRedirectUrl(appConfig.callbackPath),
  redirectSignOut:
    env.VITE_COGNITO_REDIRECT_SIGN_OUT ||
    browserRedirectUrl(appConfig.logoutPath),
  scopes: ["openid", "email", "profile"],
  externalProviders: {
    google: "Google",
  },
};

export const apiConfig = {
  baseUrl: normalizeApiBaseUrl(
    env.VITE_API_BASE_URL ||
      "https://2dd2aqb32j.execute-api.ap-southeast-2.amazonaws.com/dev"
  ),
};
