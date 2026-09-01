import { apiConfig } from "../auth/cognitoConfig.js";
import { clearTokens, getAuthorizationHeader } from "../auth/cognitoAuth.js";

export class ApiError extends Error {
  constructor(message, { status, code = null, payload = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.payload = payload;
  }
}

export function isDuplicateFileError(error) {
  return error instanceof ApiError && error.code === "DUPLICATE_FILE";
}

function parseResponsePayload(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function errorCode(payload) {
  if (typeof payload?.code === "string") return payload.code;
  if (typeof payload?.detail?.code === "string") return payload.detail.code;
  return null;
}

function errorMessage(payload, status) {
  if (typeof payload?.message === "string") return payload.message;
  if (typeof payload?.detail?.message === "string") return payload.detail.message;
  if (typeof payload?.detail === "string") return payload.detail;
  if (status === 422) {
    return "Check the selected file, tags, or thumbnail reference and try again.";
  }
  return `API request failed: ${status}`;
}

export async function apiRequest(path, options = {}) {
  const isFormData =
    typeof FormData !== "undefined" && options.body instanceof FormData;
  const headers = {
    ...getAuthorizationHeader(),
    ...(isFormData ? {} : { "Content-Type": "application/json" }),
    ...(options.headers || {}),
  };

  const response = await fetch(`${apiConfig.baseUrl}${path}`, {
    ...options,
    headers,
  });

  const text = await response.text();
  const data = parseResponsePayload(text);

  if (!response.ok) {
    if (response.status === 401) {
      clearTokens("expired");
    }
    throw new ApiError(errorMessage(data, response.status), {
      status: response.status,
      code: errorCode(data),
      payload: data,
    });
  }

  return data;
}

export function getAuthTest() {
  return apiRequest("/auth-test");
}
