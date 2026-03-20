"""Celery application factory."""

import logging

from celery import Celery
from celery.signals import worker_ready

from .config import REDIS_URL

logger = logging.getLogger(__name__)

app = Celery(
    "legislation_worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["legislation_worker.tasks"],
)

app.config_from_object("legislation_worker.celeryconfig")


@worker_ready.connect
def _on_worker_ready(sender, **kwargs):
    """Ensure pgvector schema exists when a Celery worker process starts."""
    try:
        from .vector_store import ensure_schema
        ensure_schema()
        logger.info("pgvector schema ready (worker startup)")
    except Exception as exc:
        logger.warning("Could not initialise pgvector schema on worker startup: %s", exc)
