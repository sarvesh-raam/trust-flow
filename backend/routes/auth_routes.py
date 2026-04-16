from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from firebase_admin import auth as firebase_auth

from auth import create_access_token, create_refresh_token, verify_token

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class FirebaseTokenRequest(BaseModel):
    firebase_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 1800


@router.post("/google", response_model=TokenResponse)
async def google_login(req: FirebaseTokenRequest):
    from firebase_client import db as firebase_db

    # Judge-friendly fallback: allow a local guest session explicitly and keep
    # the original hackathon bypass when Firebase Admin is not configured.
    if req.firebase_token == "local-guest" or firebase_db is None:
        user_id = "local_guest_user" if req.firebase_token == "local-guest" else "hackstrom_demo_user"
        email = "guest@local" if req.firebase_token == "local-guest" else "demo@hackstrom26.srm"
        print(f"HACKATHON BYPASS: Issued mock JWT for {email}")
        return TokenResponse(
            access_token=create_access_token(user_id, email),
            refresh_token=create_refresh_token(user_id),
        )

    try:
        decoded_token = firebase_auth.verify_id_token(req.firebase_token)

        user_id = decoded_token.get("uid")
        email = decoded_token.get("email", "")

        if not user_id:
            raise HTTPException(401, "Could not extract user UID from Firebase token")

        import hashlib

        email_hash = hashlib.sha256(email.encode()).hexdigest()[:8]
        print(f"Login success: user={user_id[:8]}... email_pref={email_hash}")

        return TokenResponse(
            access_token=create_access_token(user_id, email),
            refresh_token=create_refresh_token(user_id),
        )
    except Exception as exc:
        print(f"Firebase token verification failed: {type(exc).__name__}")
        raise HTTPException(401, "Invalid Firebase token")


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token_endpoint(req: RefreshRequest):
    """Validate refresh token and issue a fresh access token."""
    payload = verify_token(req.refresh_token)

    if not payload or payload.get("type") != "refresh":
        raise HTTPException(401, "Invalid or expired refresh token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "Refresh token missing subject")

    new_access = create_access_token(user_id, "")

    return TokenResponse(
        access_token=new_access,
        refresh_token=req.refresh_token,
        expires_in=1800,
    )
