import assert from "node:assert/strict";
import test from "node:test";

async function loadHomeExperience() {
  return import("./homeExperience.mjs").catch(() => ({}));
}

test("carousel navigation wraps in both directions", async () => {
  const { carouselIndexAfter } = await loadHomeExperience();

  assert.equal(carouselIndexAfter?.(0, -1, 3), 2);
  assert.equal(carouselIndexAfter?.(2, 1, 3), 0);
  assert.equal(carouselIndexAfter?.(1, 1, 3), 2);
});

test("carousel navigation safely falls back when the inputs are invalid", async () => {
  const { carouselIndexAfter } = await loadHomeExperience();

  assert.equal(carouselIndexAfter?.(7, 1, 0), 0);
  assert.equal(carouselIndexAfter?.(Number.NaN, 1, 3), 0);
});

test("suggested searches expose only verified model labels", async () => {
  const { SUGGESTED_SPECIES } = await loadHomeExperience();

  assert.deepEqual(
    SUGGESTED_SPECIES?.map(({ query }) => query),
    ["wombat", "dingo", "cassowary"]
  );
  assert.equal(SUGGESTED_SPECIES?.every(({ image, alt, credit }) => image && alt && credit), true);
});

test("habitat slides describe the three assignment ecosystems", async () => {
  const { HABITAT_SLIDES } = await loadHomeExperience();

  assert.deepEqual(
    HABITAT_SLIDES?.map(({ id }) => id),
    ["rainforest", "coast", "desert"]
  );
  assert.equal(HABITAT_SLIDES?.every(({ image, alt, credit }) => image && alt && credit), true);
});
