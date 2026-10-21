import os

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    create_refresh_token,
    verify_firebase_token,
    verify_token,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Local demo access without a Google account. Off by default: every guest
# shares one user id, so they can see each other's runs.
ALLOW_GUEST_LOGIN = os.getenv("ALLOW_GUEST_LOGIN", "").lower() in ("1", "true", "yes")
GUEST_TOKEN = "local-guest"
GUEST_USER_ID = "local_guest_user"

class FirebaseTokenRequest(BaseModel):
    firebase_token: str

class RefreshRequest(BaseModel):
    refresh_token: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TOKEN_EXPIRE_MINUTES * 60

@router.post("/google", response_model=TokenResponse)
async def google_login(req: FirebaseTokenRequest):
    if req.firebase_token == GUEST_TOKEN:
        if not ALLOW_GUEST_LOGIN:
            raise HTTPException(403, "Guest login is disabled on this server")
        log.info("auth.guest_login")
        return TokenResponse(
            access_token=create_access_token(GUEST_USER_ID, "guest@local"),
            refresh_token=create_refresh_token(GUEST_USER_ID),
        )

    try:
        claims = await run_in_threadpool(verify_firebase_token, req.firebase_token)
    except Exception as e:
        # Don't echo verification details back to the client.
        log.warning("auth.firebase_token_rejected", error_type=type(e).__name__, error=str(e))
        raise HTTPException(401, "Invalid Firebase token")

    user_id = claims["sub"]
    log.info("auth.login", user_id=user_id)
    return TokenResponse(
        access_token=create_access_token(user_id, claims.get("email", "")),
        refresh_token=create_refresh_token(user_id),
    )

@router.post("/refresh")
async def refresh_token(req: RefreshRequest):
    payload = verify_token(req.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(401, "Invalid refresh token")

    new_access = create_access_token(payload["sub"], "")
    return {
        "access_token": new_access,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }
