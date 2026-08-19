"""Member A — wire the team's real Cognito into ``get_current_user``.

Real team config (from ``成员B和D接收.docx`` / ``成员E接收.docx``):

- User Pool ID : ``ap-southeast-2_1hGEJyYO7``
- Region       : ``ap-southeast-2``
- App Client ID: ``65dgspco2djehpbpunc13t2oml``
- Issuer       : ``https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_1hGEJyYO7``
- JWKS         : ``https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_1hGEJyYO7/.well-known/jwks.json``

Two deployment modes, both returning the authenticated user's Cognito ``sub``
(the stable user id that Member B stores in ``FileRecord.user_id`` on upload):

1. **Lambda (behind API Gateway).** The team's ``CognitoJWTAuthorizer`` has
   *already* verified the token, so we only read ``claims.sub`` from the event:

       event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]

2. **Local (no API Gateway).** We verify the Bearer token ourselves with
   ``cognitojwt`` (add it to ``requirements.txt`` for local dev only).
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request, status

USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "ap-southeast-2_1hGEJyYO7")
REGION = os.getenv("AWS_REGION", "ap-southeast-2")
CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "65dgspco2djehpbpunc13t2oml")


def build_get_current_user():
    """Return a FastAPI dependency returning the current user's Cognito ``sub``."""

    def get_current_user(request: Request) -> str:
        # Mode 1 — Lambda: API Gateway already verified the JWT; read sub from event.
        event = getattr(request.app.state, "lambda_event", None)
        if event is not None:
            claims = (
                event.get("requestContext", {})
                .get("authorizer", {})
                .get("jwt", {})
                .get("claims", {})
            )
            sub = claims.get("sub")
            if sub:
                return sub
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no sub in authorizer claims")

        # Mode 2 — Local: verify the token ourselves against Cognito.
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        token = auth.removeprefix("Bearer ").strip()
        try:
            import cognitojwt  # local-dev only; not needed on Lambda

            claims = cognitojwt.decode(
                token, REGION, USER_POOL_ID, app_client_id=CLIENT_ID
            )
        except Exception as exc:  # noqa: BLE001 — any failure means 401
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc
        return claims["sub"]

    return get_current_user


# ---------------------------------------------------------------------------
# Wiring into app/main.py
# ---------------------------------------------------------------------------
# Replace the placeholder ``get_current_user`` in ``app/main.py`` with:
#
#   get_current_user = build_get_current_user()
#
# ---------------------------------------------------------------------------
# Deploying on Lambda (mangum)
# ---------------------------------------------------------------------------
# Add a ``lambda_function.py`` next to ``app/`` so the FastAPI app runs behind
# API Gateway's HTTP API with the JWT authorizer:
#
#   from mangum import Mangum
#   from app.main import app
#
#   def handler(event, context):
#       app.state.lambda_event = event   # lets get_current_user read claims.sub
#       return Mangum(app)(event, context)
#
# Add ``mangum`` to requirements.txt.
