"""Celery application factory."""

from celery import Celery

from .config import REDIS_URL

app = Celery(
    "legislation_worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["legislation_worker.tasks"],
)

app.config_from_object("legislation_worker.celeryconfig")
