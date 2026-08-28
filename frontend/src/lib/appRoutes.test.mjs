import assert from "node:assert/strict";
import test from "node:test";

import { appConfig } from "../auth/cognitoConfig.js";
import { authRouteForPath, postAuthHomePath } from "./appRoutes.mjs";

test("local routes preserve the configured callback and logout paths", () => {
  assert.equal(authRouteForPath(appConfig.callbackPath, appConfig), "callback");
  assert.equal(authRouteForPath(appConfig.logoutPath, appConfig), "logout");
  assert.equal(postAuthHomePath(appConfig), "/");
});

test("GitHub Pages paths are recognized without falling back to the site root", async () => {
  const pagesConfig = {
    homePath: "/FIT5225-Group-Task/",
    callbackPath: "/FIT5225-Group-Task/callback",
    logoutPath: "/FIT5225-Group-Task/logout",
  };

  assert.equal(authRouteForPath(pagesConfig.callbackPath, pagesConfig), "callback");
  assert.equal(authRouteForPath(pagesConfig.logoutPath, pagesConfig), "logout");
  assert.equal(authRouteForPath("/callback", pagesConfig), null);
  assert.equal(postAuthHomePath(pagesConfig), "/FIT5225-Group-Task/");
});
