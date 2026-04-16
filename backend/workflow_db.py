from sqlmodel import create_engine, Session, SQLModel, select
import os, json
from typing import Optional
from datetime import datetime
from db import UploadRunRow

class _DatabaseManager:
    """Singleton — one engine instance for the entire process lifetime"""
    _instance: Optional['_DatabaseManager'] = None
    _engine = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # Use DATABASE_URL from env or fallback to hackstrom.db (to match db.py)
            db_url = os.getenv("DATABASE_URL", "sqlite:///./data/hackstrom.db")
            cls._instance._engine = create_engine(
                db_url,
                echo=False,
                connect_args={"check_same_thread": False}
            )
            SQLModel.metadata.create_all(cls._instance._engine)
            print(f"[DB] Singleton engine created: {db_url}")
        return cls._instance

    def session(self) -> Session:
        return Session(self._engine)

# Module-level singleton instance
db = _DatabaseManager()

def get_run(run_id: str):
    with db.session() as session:
        statement = select(UploadRunRow).where(UploadRunRow.run_id == run_id)
        return session.exec(statement).first()

def update_run_status(run_id: str, status: str, result: dict = None):
    with db.session() as session:
        statement = select(UploadRunRow).where(UploadRunRow.run_id == run_id)
        run = session.exec(statement).first()
        if run:
            run.status = status
            if result:
                if "declaration" in result:
                    run.declaration_json = json.dumps(result["declaration"])
                if "summary" in result:
                    run.summary = result["summary"]
                if "error" in result:
                    run.error = result["error"]
            run.updated_at = datetime.utcnow()
            session.add(run)
            session.commit()
            session.refresh(run)
