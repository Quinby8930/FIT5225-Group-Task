function comparablePath(pathname) {
  const value = String(pathname || "/");
  return value.length > 1 ? value.replace(/\/+$/, "") : value;
}

const VIEW_HASHES = Object.freeze({
  home: "",
  explore: "#/explore",
  upload: "#/upload",
  manage: "#/manage",
  notifications: "#/notifications",
});

export function hashForView(view) {
  return VIEW_HASHES[view] ?? "";
}

export function viewForHash(hash) {
  const normalized = String(hash || "").replace(/\/+$/, "").toLowerCase();
  return Object.entries(VIEW_HASHES).find(([, value]) => value === normalized)?.[0]
    || "home";
}

export function viewUrl(location = {}, view = "home") {
  const pathname = String(location.pathname || "/");
  const search = String(location.search || "");
  return `${pathname}${search}${hashForView(view)}`;
}

export function authRouteForPath(pathname, config) {
  const currentPath = comparablePath(pathname);
  if (currentPath === comparablePath(config.callbackPath)) return "callback";
  if (currentPath === comparablePath(config.logoutPath)) return "logout";
  return null;
}

export function postAuthHomePath(config) {
  return config.homePath;
}
