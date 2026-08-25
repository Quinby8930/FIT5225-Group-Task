const env = import.meta.env || {};

export const cognitoConfig = {
  region: env.VITE_COGNITO_REGION || "ap-southeast-2",
  userPoolId: env.VITE_COGNITO_USER_POOL_ID || "ap-southeast-2_1hGEJyYO7",
  clientId: env.VITE_COGNITO_CLIENT_ID || "65dgspco2djehpbpunc13t2oml",
  domain:
    env.VITE_COGNITO_DOMAIN ||
    "https://ap-southeast-21hgejyyo7.auth.ap-southeast-2.amazoncognito.com",
  redirectSignIn:
    env.VITE_COGNITO_REDIRECT_SIGN_IN ||
    "http://localhost:3000/callback",
  redirectSignOut:
    env.VITE_COGNITO_REDIRECT_SIGN_OUT ||
    "http://localhost:3000/logout",
  scopes: ["openid", "email", "profile"],
  externalProviders: {
    google: "Google",
  },
};

export const apiConfig = {
  baseUrl:
    env.VITE_API_BASE_URL ||
    "https://2dd2aqb32j.execute-api.ap-southeast-2.amazonaws.com",
};
