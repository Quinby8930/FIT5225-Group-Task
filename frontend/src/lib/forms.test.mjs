import test from "node:test";
import assert from "node:assert/strict";
import { parseKeyList, parseSpeciesList, parseTagCounts } from "./forms.js";

test("parses tag counts as lower-case AND query payload", () => {
  assert.deepEqual(parseTagCounts("Dingo:2, wombat=1\nmagpie"), {
    dingo: 2,
    wombat: 1,
    magpie: 1,
  });
});

test("invalid or empty counts fall back to one", () => {
  assert.deepEqual(parseTagCounts("koala:0, fox:nope"), { koala: 1, fox: 1 });
});

test("parses species and key lists", () => {
  assert.deepEqual(parseSpeciesList("Wombat, dingo\nMagpie"), [
    "wombat",
    "dingo",
    "magpie",
  ]);
  assert.deepEqual(parseKeyList("originals/a.jpg,\nthumbnails/b.jpg"), [
    "originals/a.jpg",
    "thumbnails/b.jpg",
  ]);
});
