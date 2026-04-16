"""Firebase Admin SDK initialisation for Hackstrom Track 3.

Exports:
    db              — Firestore client  (google.cloud.firestore.Client)
    storage_bucket  — GCS bucket handle (google.cloud.storage.Bucket)

Both are None when FIREBASE_SERVICE_ACCOUNT_JSON is not configured, so the
rest of the application can guard with `if firebase_client.db:` and continue
using SQLite-only mode without crashing.

Environment variables (set in .env or container env):
    FIREBASE_SERVICE_ACCOUNT_JSON  Base64-encoded service-account JSON blob
    FIREBASE_STORAGE_BUCKET        GCS bucket name, e.g. "myproject.appspot.com"
"""
from __future__ import annotations

import base64
import json
import logging

# dotenv is loaded by main.py before this module is imported; importing
# config here is safe because config.py reads os.getenv() at instantiation time.
from config import settings

log = logging.getLogger(__name__)

db = None              # type: google.cloud.firestore.Client | None
storage_bucket = None  # type: google.cloud.storage.Bucket | None

_SA_B64 = settings.FIREBASE_SERVICE_ACCOUNT_JSON
_BUCKET  = settings.FIREBASE_STORAGE_BUCKET

if not _SA_B64:
    log.warning(
        "firebase_client: FIREBASE_SERVICE_ACCOUNT_JSON is not set — "
        "Firestore and Storage are disabled; SQLite-only mode active."
    )
else:
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore, storage as fb_storage

        # Decode base64 → JSON dict → Firebase credential
        _sa_json: dict = json.loads(base64.b64decode(_SA_B64).decode("utf-8"))
        
        # CLEANING: Ensure the private key has real newlines and no weird trailing spaces.
        if "private_key" in _sa_json:
            _sa_json["private_key"] = _sa_json["private_key"].replace("\\n", "\n").strip()
            
        _cred = credentials.Certificate(_sa_json)

        # Initialise only once (guard against hot-reload double-init)
        if not firebase_admin._apps:
            _init_kwargs: dict = {"credential": _cred}
            if _BUCKET:
                _init_kwargs["storageBucket"] = _BUCKET
            firebase_admin.initialize_app(**_init_kwargs)

        db = firestore.client()
        log.info("firebase_client: Firestore client initialised (project=%s)", _sa_json.get("project_id"))
        
        # Senior Dev connectivity check
        try:
            db.collections()
            log.info("firebase_client: Firestore connectivity check PASSED.")
        except Exception as conn_err:
            log.warning("firebase_client: Firestore unreachable or permission denied: %s", conn_err)

        if _BUCKET:
            storage_bucket = fb_storage.bucket()
            log.info("firebase_client: Storage bucket initialised (%s)", _BUCKET)
        else:
            log.warning(
                "firebase_client: FIREBASE_STORAGE_BUCKET is not set — "
                "Storage bucket is disabled."
            )

    except Exception as exc:
        log.exception("firebase_client: Failed to initialise Firebase Admin SDK: %s", exc)
        # Leave db and storage_bucket as None so the app can still start.
