import jwt
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

# Example values that appear in this repo's docs/manifests — signing with any
# of them would let anyone forge a login token.
_PLACEHOLDER_SECRETS = {
    "change-me",
    "hackstrom-secret-change-in-prod",
    "hackstrom-secret-key-2026",
    "your_jwt_secret_key_here",
}
_MIN_SECRET_LENGTH = 32


def _load_secret() -> str:
    """JWT signing key from JWT_SECRET_KEY.

    Unset → a random per-process key (fine for local dev: tokens just stop
    working after a restart and the frontend re-logs in).  A known placeholder
    or a short key → refuse to start rather than issue forgeable tokens.
    """
    secret = os.getenv("JWT_SECRET_KEY", "").strip()
    if not secret:
        log.warning(
            "auth.random_jwt_secret",
            hint="JWT_SECRET_KEY is not set; using a random key for this process only",
        )
        return secrets.token_urlsafe(48)
    if secret in _PLACEHOLDER_SECRETS or len(secret) < _MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"JWT_SECRET_KEY is a placeholder or shorter than {_MIN_SECRET_LENGTH} characters. "
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return secret


SECRET_KEY = _load_secret()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

# Firebase project whose ID tokens we accept (must match the frontend config).
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "trustflow-1b008")


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
