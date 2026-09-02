import assert from "node:assert/strict";
import test from "node:test";

import { appConfig } from "../auth/cognitoConfig.js";
import {
  authRouteForPath,
  hashForView,
  postAuthHomePath,
  viewForHash,
  viewUrl,
} from "./appRoutes.mjs";

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

test("workspace views use refresh-safe GitHub Pages hash routes", () => {
  const location = {
    pathname: "/FIT5225-Group-Task/",
    search: "?demo",
  };

  assert.equal(hashForView("home"), "");
  assert.equal(hashForView("explore"), "#/explore");
  assert.equal(hashForView("upload"), "#/upload");
  assert.equal(hashForView("manage"), "#/manage");
  assert.equal(hashForView("notifications"), "#/notifications");
  assert.equal(
    viewUrl(location, "upload"),
    "/FIT5225-Group-Task/?demo#/upload",
  );

  assert.equal(viewForHash("#/explore"), "explore");
  assert.equal(viewForHash("#/upload/"), "upload");
  assert.equal(viewForHash("#/manage"), "manage");
  assert.equal(viewForHash("#/notifications"), "notifications");
  assert.equal(viewForHash(""), "home");
  assert.equal(viewForHash("#/unknown"), "home");
});
