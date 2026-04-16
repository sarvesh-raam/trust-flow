from celery import Celery
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

celery_app = Celery(
    "hackstrom",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,           # Atomicity — task only acked after completion
    worker_prefetch_multiplier=1,  # One task per worker at a time
    task_reject_on_worker_lost=True,  # Idempotency — requeue if worker dies
)
