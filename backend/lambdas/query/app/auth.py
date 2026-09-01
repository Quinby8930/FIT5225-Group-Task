"""Cognito authentication dependency for the Query API."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request, status

USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "ap-southeast-2_1hGEJyYO7")
REGION = os.getenv("AWS_REGION", "ap-southeast-2")
CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "65dgspco2djehpbpunc13t2oml")


@dataclass(frozen=True)
class VerifiedEmailIdentity:
    """Trusted Cognito identity for the opt-in per-user email path."""

    sub: str
    email: str = field(repr=False)


def _claims_from_request(request: Request) -> dict[str, Any]:
    event = getattr(request.app.state, "lambda_event", None)
    if event is not None:
        claims = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("jwt", {})
            .get("claims", {})
        )
        return claims if isinstance(claims, dict) else {}

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
    return claims if isinstance(claims, dict) else {}


def verified_email_identity(request: Request) -> VerifiedEmailIdentity:
    """Require an ID token containing a verified, non-empty email claim.

    The email is intentionally retained only in memory for the SNS Subscribe
    call.  Error details never reveal whether a particular email was present.
    """

    claims = _claims_from_request(request)
    subject = claims.get("sub")
    email = claims.get("email")
    email_verified = claims.get("email_verified")
    if not (
        isinstance(subject, str)
        and subject.strip()
        and isinstance(email, str)
        and email.strip()
        and (email_verified is True or email_verified == "true")
        and claims.get("token_use") == "id"
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "VERIFIED_EMAIL_REQUIRED",
                "message": "a verified email identity is required",
            },
        )
    return VerifiedEmailIdentity(sub=subject.strip(), email=email.strip())


def build_get_current_user():
    """Return a FastAPI dependency that resolves the authenticated Cognito subject."""

    def get_current_user(request: Request) -> str:
        claims = _claims_from_request(request)
        subject = claims.get("sub")
        if isinstance(subject, str) and subject:
            return subject
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "no sub in authorizer claims",
        )

    return get_current_user
