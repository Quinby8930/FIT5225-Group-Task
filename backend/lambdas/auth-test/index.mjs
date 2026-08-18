export const handler = async (event) => {
  const claims = event.requestContext?.authorizer?.jwt?.claims || {};

  return {
    statusCode: 200,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "http://localhost:3000",
      "Access-Control-Allow-Headers": "Authorization,Content-Type",
      "Access-Control-Allow-Methods": "GET,OPTIONS",
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
