# Google External Login Setup

Member A owns this setup. Do not commit Google client secrets, AWS access keys,
or screenshots that expose private credentials.

## Current Cognito values

```text
User pool: ap-southeast-2_1hGEJyYO7
App client: PacificBioArchive-SPA
App client ID: 65dgspco2djehpbpunc13t2oml
Cognito domain: https://ap-southeast-21hgejyy07.auth.ap-southeast-2.amazoncognito.com
App callback URL: http://localhost:3000/callback
App sign-out URL: http://localhost:3000/logout
```

## Google OAuth client

Create a Google OAuth web client and use this Authorized redirect URI:

```text
https://ap-southeast-21hgejyy07.auth.ap-southeast-2.amazoncognito.com/oauth2/idpresponse
```

Keep the Google client ID and client secret outside the repository.

## Cognito identity provider

In the Cognito user pool, add Google as a social/external identity provider.
Use the Google client ID and client secret from the previous step.

Recommended Google scopes:

```text
openid email profile
```

Map at least these attributes:

```text
email -> email
given_name -> given_name
family_name -> family_name
```

## App client update

In the `PacificBioArchive-SPA` app client, enable both providers:

```text
Cognito user pool
Google
```

Keep the OAuth flow as authorization code grant with PKCE. Keep these scopes:

```text
openid email profile
```

The frontend can use either the normal Hosted UI login or a direct Google
redirect. The direct Google redirect is implemented with:

```js
signInWithGoogle()
```

## Evidence to capture

1. Google provider enabled in Cognito.
2. App client identity providers showing `Cognito user pool` and `Google`.
3. Hosted UI showing the Google login option, or the app's `Sign in with Google`
   button redirecting to Google.
4. Successful Google login redirecting back to `http://localhost:3000/callback`.
5. A protected API returning `401` without a token.
6. The same protected API returning `200` with the Google user's Cognito token.
