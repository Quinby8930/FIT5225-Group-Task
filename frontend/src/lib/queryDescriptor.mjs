// Query descriptors describe the submitted query that produced a result set.
// They are built from the exact values sent to the API and are safe to
// render: they never contain signed URLs, S3 keys, or query-file contents.

export function speciesDescriptor(input) {
  const value = typeof input === "string" ? input.trim().toLowerCase() : "";
  if (!value) return null;
  return {
    kind: "species",
    chips: [{ label: "Species", value }],
    summary: `Species: ${value}`,
  };
}

export function tagsDescriptor(tagMap) {
  if (!tagMap || typeof tagMap !== "object" || Array.isArray(tagMap)) return null;
  const chips = Object.entries(tagMap)
    .filter(([name, count]) => (
      typeof name === "string"
      && name.trim().length > 0
      && Number.isInteger(count)
      && count > 0
    ))
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, count]) => ({ label: name, value: `≥ ${count}` }));
  if (!chips.length) return null;
  return {
    kind: "tags",
    chips,
    summary: chips.map((chip) => `${chip.label} ${chip.value}`).join(", "),
  };
}

export function fileDescriptor(file) {
  const name = typeof file?.name === "string" ? file.name.trim() : "";
  if (!name) return null;
  return {
    kind: "file",
    chips: [{ label: "Matched by image", value: name }],
    summary: `Matched by image: ${name}`,
  };
}

// Neutral on purpose: never echoes the submitted thumbnail key or any URL.
export function thumbnailDescriptor() {
  return {
    kind: "thumbnail",
    chips: [{ label: "Thumbnail lookup", value: "trusted archive thumbnail" }],
    summary: "Thumbnail lookup",
  };
}

export function beginPendingQuery(state, descriptor) {
  return {
    lastSuccessfulDescriptor: state?.lastSuccessfulDescriptor ?? null,
    pendingDescriptor: descriptor ?? null,
  };
}

export function settleQuerySuccess(state, descriptor) {
  return {
    lastSuccessfulDescriptor: descriptor ?? null,
    pendingDescriptor: null,
  };
}

export function settleQueryFailure(state) {
  return {
    lastSuccessfulDescriptor: state?.lastSuccessfulDescriptor ?? null,
    pendingDescriptor: null,
  };
}

export function clearQueryDescriptors() {
  return { lastSuccessfulDescriptor: null, pendingDescriptor: null };
}
