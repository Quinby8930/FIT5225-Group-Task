import { apiConfig } from "../auth/cognitoConfig.js";
import { clearTokens, getAuthorizationHeader } from "../auth/cognitoAuth.js";

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
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) {
    if (response.status === 401) {
      clearTokens();
    }
    throw new Error(
      data?.message || data?.detail || `API request failed: ${response.status}`
    );
  }

  return data;
}

export function getAuthTest() {
  return apiRequest("/auth-test");
}
