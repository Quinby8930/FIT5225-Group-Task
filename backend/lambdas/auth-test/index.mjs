const DEFAULT_ALLOWED_ORIGINS = [
  "http://localhost:3000",
  "https://quinby8930.github.io",
];

function configuredAllowedOrigins() {
  return [...new Set([
    ...DEFAULT_ALLOWED_ORIGINS,
    process.env.ALLOWED_ORIGIN,
    ...(process.env.ALLOWED_ORIGINS || "").split(","),
  ].map((origin) => String(origin || "").trim()).filter(Boolean))];
}

function requestOrigin(event) {
  const match = Object.entries(event?.headers || {})
    .find(([name]) => name.toLowerCase() === "origin");
  return typeof match?.[1] === "string" ? match[1] : undefined;
}

export const handler = async (event) => {
  const claims = event.requestContext?.authorizer?.jwt?.claims || {};
  const origin = requestOrigin(event);
  const responseOrigin = configuredAllowedOrigins().includes(origin) ? origin : undefined;

  return {
    statusCode: 200,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Headers": "Authorization,Content-Type",
      "Access-Control-Allow-Methods": "GET,OPTIONS",
      ...(responseOrigin ? { "Access-Control-Allow-Origin": responseOrigin } : {}),
    },
    body: JSON.stringify({
      message: "Authorized request success",
      userId: claims.sub || null,
      email: claims.email || null,
      givenName: claims.given_name || null,
      familyName: claims.family_name || null,
      claims,
    }),
  };
};
