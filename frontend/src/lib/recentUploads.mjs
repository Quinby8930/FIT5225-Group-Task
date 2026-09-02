const STORAGE_KEY = "pacificBioArchive.recentUploads";
const MAX_RECENT_UPLOADS = 10;
const ACTIVE_STATUSES = new Set(["pending_upload", "processing"]);
const KNOWN_STATUSES = new Set([
  "pending_upload",
  "processing",
  "completed",
  "failed",
  "deleting",
]);

function safeString(value, maximum = 256) {
  if (typeof value !== "string") return "";
  const text = value.trim();
  if (!text || text.length > maximum || /[\u0000-\u001f\u007f]/u.test(text)) return "";
  return text;
}

function safeTags(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const rows = Object.entries(value);
  if (rows.length > 64) return {};
  const tags = {};
  for (const [rawSpecies, rawCount] of rows) {
    const species = safeString(rawSpecies, 128);
    if (!species || !Number.isSafeInteger(rawCount) || rawCount <= 0) continue;
    tags[species] = rawCount;
  }
  return Object.fromEntries(Object.entries(tags).sort(([left], [right]) => left.localeCompare(right)));
}

function safeDetections(value) {
  if (!Array.isArray(value) || value.length > 1000) return [];
  return value.flatMap((detection) => {
    const species = safeString(detection?.species, 128);
    const confidence = detection?.confidence;
    if (
      !species
      || typeof confidence !== "number"
      || !Number.isFinite(confidence)
      || confidence < 0
      || confidence > 1
    ) return [];
    return [{ species, confidence }];
  });
}

function normalizeUpload(value, fallback = {}) {
  const fileId = safeString(value?.file_id, 128);
  const filename = safeString(value?.filename, 255) || safeString(fallback.filename, 255);
  const fileType = ["image", "video"].includes(value?.file_type)
    ? value.file_type
    : fallback.file_type;
  const status = KNOWN_STATUSES.has(value?.status) ? value.status : "";
  if (!fileId || !filename || !["image", "video"].includes(fileType) || !status) return null;

  const completed = status === "completed";
  const failed = status === "failed";
  return {
    file_id: fileId,
    filename,
    file_type: fileType,
    status,
    tags: completed ? safeTags(value?.tags) : {},
    detections: completed ? safeDetections(value?.detections) : [],
    model_version: completed ? safeString(value?.model_version, 128) : "",
    error_code: failed ? safeString(value?.error_code, 128) : "",
    message: failed ? safeString(value?.message, 240) : "",
    upload_time: safeString(value?.upload_time, 64) || safeString(fallback.upload_time, 64),
  };
}

function normalizedUploads(uploads) {
  if (!Array.isArray(uploads)) return [];
  const unique = [];
  const seen = new Set();
  for (const value of uploads) {
    const upload = normalizeUpload(value);
    if (!upload || seen.has(upload.file_id)) continue;
    seen.add(upload.file_id);
    unique.push(upload);
    if (unique.length === MAX_RECENT_UPLOADS) break;
  }
  return unique;
}

export function loadRecentUploads(storage, subject) {
  const safeSubject = safeString(subject, 256);
  if (!safeSubject || typeof storage?.getItem !== "function") return [];
  try {
    const payload = JSON.parse(storage.getItem(STORAGE_KEY) || "null");
    if (payload?.subject !== safeSubject) return [];
    return normalizedUploads(payload.uploads);
  } catch {
    return [];
  }
}

export function saveRecentUploads(storage, subject, uploads) {
  const safeSubject = safeString(subject, 256);
  if (!safeSubject || typeof storage?.setItem !== "function") return;
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify({
      subject: safeSubject,
      uploads: normalizedUploads(uploads),
    }));
  } catch {
    // Upload tracking is an enhancement; unavailable browser storage must not
    // break the upload itself.
  }
}

export function rememberRecentUpload(uploads, receipt) {
  const normalized = normalizeUpload(receipt);
  const current = normalizedUploads(uploads);
  if (!normalized) return current;
  return [
    normalized,
    ...current.filter((item) => item.file_id !== normalized.file_id),
  ].slice(0, MAX_RECENT_UPLOADS);
}

export function mergeUploadStatus(uploads, statusPayload) {
  const current = normalizedUploads(uploads);
  const existing = current.find((item) => item.file_id === statusPayload?.file_id);
  if (!existing) return current;
  const normalized = normalizeUpload(statusPayload, existing);
  if (!normalized) return current;
  return current.map((item) => item.file_id === normalized.file_id ? normalized : item);
}

export function pendingUploadIds(uploads) {
  return normalizedUploads(uploads)
    .filter((upload) => ACTIVE_STATUSES.has(upload.status))
    .map((upload) => upload.file_id);
}

export function uploadStatusView(upload) {
  const labels = {
    pending_upload: "Upload queued",
    processing: "Processing",
    completed: "Completed",
    failed: "Processing failed",
    deleting: "Deleting",
  };
  return {
    label: labels[upload?.status] || "Status unavailable",
    tagRows: Object.entries(upload?.tags || {}).map(
      ([species, count]) => `${species} × ${count}`,
    ),
    detectionRows: (upload?.detections || []).map(
      ({ species, confidence }) => (
        `${species} — model score ${(confidence * 100).toFixed(2)}%`
      ),
    ),
    modelVersion: safeString(upload?.model_version, 128),
    failure: upload?.status === "failed"
      ? safeString(upload?.message, 240) || "Processing failed."
      : "",
  };
}
