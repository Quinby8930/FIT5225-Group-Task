export const VIDEO_DETECTION_NOTE = (
  "Sampled-frame detections are model evidence, not counts of individual animals."
);

export function detectionDetailsForMedia(fileType, detections) {
  if (fileType !== "video") {
    return {
      rows: detections.map(({ species, confidence }) => ({
        species,
        confidence,
        label: `${species} — model score ${(confidence * 100).toFixed(2)}%`,
      })),
      note: null,
    };
  }

  const grouped = new Map();
  for (const { species, confidence } of detections) {
    const current = grouped.get(species);
    if (current) {
      current.occurrences += 1;
      current.confidence = Math.max(current.confidence, confidence);
    } else {
      grouped.set(species, { species, confidence, occurrences: 1 });
    }
  }

  const rows = [...grouped.values()].map(({ species, confidence, occurrences }) => ({
    species,
    confidence,
    occurrences,
    label: `${species} — ${occurrences} sampled-frame detection${occurrences === 1 ? "" : "s"}, max model score ${(confidence * 100).toFixed(2)}%`,
  }));
  return {
    rows,
    note: rows.length > 0 ? VIDEO_DETECTION_NOTE : null,
  };
}
