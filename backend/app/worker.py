from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "ledgermind",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
)


@celery_app.task(name="tasks.ping")
def ping():
    """Smoke test — verify worker is alive."""
    return {"status": "ok"}