import { apiConfig } from "../auth/cognitoConfig";
import { getAuthorizationHeader } from "../auth/cognitoAuth";

export async function apiRequest(path, options = {}) {
  const response = await fetch(`${apiConfig.baseUrl}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...getAuthorizationHeader(),
      ...(options.headers || {}),
    },
  });

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw new Error(data?.message || `API request failed: ${response.status}`);
  }

  return data;
}

export function getAuthTest() {
  return apiRequest("/auth-test");
}
