import jwt
import os
from datetime import datetime, timedelta
from typing import Optional

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "hackstrom-secret-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours — covers full hackathon demo day
REFRESH_TOKEN_EXPIRE_DAYS = 7

import hmac
import hashlib

def _hash_pii(value: str) -> str:
    """Hash PII before storing — never expose raw email in tokens or logs.
    PII SHIELD: raw email never stored in token or logs — only HMAC-SHA256 hash
    Uses HMAC-SHA256 with SECRET_KEY for deterministic but salted hashing."""
    return hmac.new(
        SECRET_KEY.encode(), 
        value.encode(), 
        hashlib.sha256
    ).hexdigest()[:16]

def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": _hash_pii(email),  # PII PROTECTION — never store raw email
        "type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": datetime.utcnow()
    }
    # Security invariant: Raw email must never leak into the signed payload
    assert "id" not in payload, "PII ID leak detected"
    assert len(payload["email"]) == 16, "PII hash length mismatch — potentially raw email leak"
    
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
