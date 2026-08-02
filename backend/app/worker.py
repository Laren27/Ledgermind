# ---------------------------------------------------------------------------
# Logging must be configured BEFORE any `app.*` import.
#
# Identical reasoning to app/main.py, which is the OTHER entrypoint into this
# codebase. The worker and beat containers start at `app.worker:celery_app`
# and never import main.py, so main.py's basicConfig never ran for them:
# every import-time INFO log under app.* fell through to logging.lastResort,
# which is fixed at WARNING, and was discarded silently. The engines'
# module-scope lines (e.g. router's resolved GEMINI_MODEL) were therefore
# visible in the backend container and invisible in the worker, for the same
# code.
#
# force=True so a dependency that installs a root handler cannot turn
# basicConfig into a silent no-op later. Celery installs its own logging on
# worker startup; force=True is what keeps this from being overridden.
#
# DO NOT move this below the app.* imports.
# ---------------------------------------------------------------------------
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)

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