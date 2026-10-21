import jwt
import os
from datetime import datetime, timedelta
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

_DEFAULT_SECRET = "hackstrom-secret-change-in-prod"
SECRET_KEY = os.getenv("JWT_SECRET_KEY") or _DEFAULT_SECRET
if SECRET_KEY == _DEFAULT_SECRET:
    log.warning("auth.default_jwt_secret", hint="set JWT_SECRET_KEY before deploying")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

# Firebase project whose ID tokens we accept (must match the frontend config).
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "trustflow-45bec")


def _hash_pii(value: str) -> str:
    """Hash PII before storing — never expose raw email in tokens or logs"""
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def verify_firebase_token(token: str) -> dict:
    """Verify a Firebase ID token's signature, issuer, audience and expiry.

    Blocking (fetches Google's public certs, cached by google-auth) — call it
    from a thread. Raises ValueError on any invalid token.
    """
    from google.auth.transport import requests as google_requests  # noqa: PLC0415
    from google.oauth2 import id_token  # noqa: PLC0415

    claims = id_token.verify_firebase_token(
        token, google_requests.Request(), audience=FIREBASE_PROJECT_ID
    )
    if not claims or not claims.get("sub"):
        raise ValueError("token has no subject")
    return claims


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": _hash_pii(email),  # PII PROTECTION — never store raw email
        "type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
