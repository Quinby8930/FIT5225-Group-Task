export function parseTagCounts(value) {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .reduce((tags, item) => {
      const [rawName, rawCount] = item.split(/[:=]/).map((part) => part.trim());
      if (!rawName) {
        return tags;
      }
      const count = Number.parseInt(rawCount || "1", 10);
      tags[rawName.toLowerCase()] = Number.isFinite(count) && count > 0 ? count : 1;
      return tags;
    }, {});
}

export function hasTagCounts(tagMap) {
  return Boolean(
    tagMap
    && typeof tagMap === "object"
    && !Array.isArray(tagMap)
    && Object.entries(tagMap).some(([name, count]) => (
      typeof name === "string"
      && name.trim().length > 0
      && Number.isInteger(count)
      && count > 0
    ))
  );
}

export function parseSpeciesList(value) {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
}

export function parseKeyList(value) {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}
