export const cognitoConfig = {
  region: "ap-southeast-2",
  userPoolId: "ap-southeast-2_1hGEJyYO7",
  clientId: "65dgspco2djehpbpunc13t2oml",
  domain: "https://ap-southeast-21hgejyy07.auth.ap-southeast-2.amazoncognito.com",
  redirectSignIn: "http://localhost:3000/callback",
  redirectSignOut: "http://localhost:3000/logout",
  scopes: ["openid", "email", "profile"],
  externalProviders: {
    google: "Google",
  },
};

export const apiConfig = {
  baseUrl: "https://2dd2aqb32j.execute-api.ap-southeast-2.amazonaws.com",
};
