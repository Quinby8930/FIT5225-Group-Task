"""Cognito authentication dependency for the Query API."""

from __future__ import annotations

import os

from fastapi import HTTPException, Request, status

USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "ap-southeast-2_1hGEJyYO7")
REGION = os.getenv("AWS_REGION", "ap-southeast-2")
CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "65dgspco2djehpbpunc13t2oml")


def build_get_current_user():
    """Return a FastAPI dependency that resolves the authenticated Cognito subject."""

    def get_current_user(request: Request) -> str:
        event = getattr(request.app.state, "lambda_event", None)
        if event is not None:
            claims = (
                event.get("requestContext", {})
                .get("authorizer", {})
                .get("jwt", {})
                .get("claims", {})
            )
            subject = claims.get("sub")
            if subject:
                return subject
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "no sub in authorizer claims",
            )

        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        token = authorization.removeprefix("Bearer ").strip()
        try:
            import cognitojwt  # local development only; API Gateway verifies Lambda requests

            claims = cognitojwt.decode(
                token,
                REGION,
                USER_POOL_ID,
                app_client_id=CLIENT_ID,
            )
        except Exception as exc:  # noqa: BLE001 - every verification failure is unauthorized
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc
        return claims["sub"]

    return get_current_user
