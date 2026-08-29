import { apiRequest } from "./apiClient.js";

const UPLOAD_RULES = new Map([
  ["image/jpeg", { kind: "image", maxBytes: 12_582_912, error: "Images must be 12 MiB or smaller." }],
  ["image/png", { kind: "image", maxBytes: 12_582_912, error: "Images must be 12 MiB or smaller." }],
  ["image/webp", { kind: "image", maxBytes: 12_582_912, error: "Images must be 12 MiB or smaller." }],
  ["video/mp4", { kind: "video", maxBytes: 262_144_000, error: "Videos must be 250 MiB or smaller." }],
  ["video/quicktime", { kind: "video", maxBytes: 262_144_000, error: "Videos must be 250 MiB or smaller." }],
]);

function base64FromBuffer(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary);
}

export async function computeSha256Base64(file) {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return base64FromBuffer(digest);
}

export function mediaKind(contentType) {
  return contentType.startsWith("video/") ? "video" : "image";
}

export function validateUploadFile(file) {
  const rule = UPLOAD_RULES.get(file?.type);
  if (!rule) {
    throw new Error("Choose a JPEG, PNG, WebP, MP4, or QuickTime file.");
  }
  if (!Number.isSafeInteger(file?.size) || file.size < 0) {
    throw new Error("The selected file size is invalid.");
  }
  if (file.size > rule.maxBytes) throw new Error(rule.error);
  return { kind: rule.kind, maxBytes: rule.maxBytes };
}

export async function requestUploadUrl(file, checksumSha256) {
  return apiRequest("/upload-url", {
    method: "POST",
    body: JSON.stringify({
      filename: file.name,
      content_type: file.type || "application/octet-stream",
      size_bytes: file.size,
      checksum_sha256: checksumSha256,
    }),
  });
}

export async function putUpload(uploadUrl, file, requiredHeaders = {}) {
  const response = await fetch(uploadUrl, {
    method: "PUT",
    headers: requiredHeaders,
    body: file,
  });
  if (!response.ok) {
    throw new Error(`S3 upload failed: ${response.status}`);
  }
}

export async function uploadMedia(file, { onStage } = {}) {
  validateUploadFile(file);
  const reportStage = (stage) => {
    if (typeof onStage === "function") onStage(stage);
  };
  reportStage("hashing");
  const checksum = await computeSha256Base64(file);
  reportStage("requesting");
  const reservation = await requestUploadUrl(file, checksum);
  reportStage("uploading");
  await putUpload(reservation.upload_url, file, reservation.required_headers);
  reportStage("queued");
  return { ...reservation, checksum };
}

export function requestAssetUrls(keys) {
  return apiRequest("/asset-urls", {
    method: "POST",
    body: JSON.stringify({ keys }),
  });
}

export function queryByTags(tags) {
  return apiRequest("/query/by-tags", {
    method: "POST",
    body: JSON.stringify({ tags }),
  });
}

export function queryBySpecies(species) {
  return apiRequest("/query/by-species", {
    method: "POST",
    body: JSON.stringify({ species }),
  });
}

export function queryByThumbnail(key) {
  const params = new URLSearchParams({ key });
  return apiRequest(`/query/by-thumbnail?${params.toString()}`);
}

export function queryByFile(file) {
  const body = new FormData();
  body.append("file", file);
  return apiRequest("/query/by-file", {
    method: "POST",
    body,
  });
}

export function editTags(keys, tags, operation) {
  return apiRequest("/tags/edit", {
    method: "POST",
    body: JSON.stringify({ keys, tags, operation }),
  });
}

export function deleteFiles(keys) {
  return apiRequest("/files/delete", {
    method: "POST",
    body: JSON.stringify({ keys }),
  });
}

export function subscribeToSpecies(species) {
  return apiRequest("/notifications/subscribe", {
    method: "POST",
    body: JSON.stringify({ species }),
  });
}

export function unsubscribeFromSpecies(species) {
  const params = new URLSearchParams({ species });
  return apiRequest(`/notifications/subscribe?${params.toString()}`, {
    method: "DELETE",
  });
}

export function listSubscriptions() {
  return apiRequest("/notifications/subscriptions");
}

export function listNotifications() {
  return apiRequest("/notifications");
}
