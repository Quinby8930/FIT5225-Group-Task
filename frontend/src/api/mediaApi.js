import { apiConfig } from "../auth/cognitoConfig.js";
import { apiRequest } from "./apiClient.js";

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

export function displayUrlForKey(key) {
  if (!key) {
    return "";
  }
  if (/^https?:\/\//i.test(key)) {
    return key;
  }
  if (!apiConfig.assetBaseUrl) {
    return "";
  }
  return `${apiConfig.assetBaseUrl.replace(/\/$/, "")}/${key.replace(/^\//, "")}`;
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

export async function uploadMedia(file) {
  const checksum = await computeSha256Base64(file);
  const reservation = await requestUploadUrl(file, checksum);
  await putUpload(reservation.upload_url, file, reservation.required_headers);
  return { ...reservation, checksum };
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

export function subscribeToSpecies(userId, species) {
  return apiRequest("/notifications/subscribe", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, species }),
  });
}

export function unsubscribeFromSpecies(userId, species) {
  const params = new URLSearchParams({ user_id: userId, species });
  return apiRequest(`/notifications/subscribe?${params.toString()}`, {
    method: "DELETE",
  });
}

export function listSubscriptions(userId) {
  const params = new URLSearchParams({ user_id: userId });
  return apiRequest(`/notifications/subscriptions?${params.toString()}`);
}

export function listNotifications(userId) {
  const params = new URLSearchParams({ user_id: userId });
  return apiRequest(`/notifications?${params.toString()}`);
}
