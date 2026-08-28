function comparablePath(pathname) {
  const value = String(pathname || "/");
  return value.length > 1 ? value.replace(/\/+$/, "") : value;
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
