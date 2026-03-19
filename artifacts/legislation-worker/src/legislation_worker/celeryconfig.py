"""Celery configuration."""

from celery.schedules import crontab

from .config import REDIS_URL

broker_url = REDIS_URL
result_backend = REDIS_URL

task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]
timezone = "UTC"
enable_utc = True

worker_prefetch_multiplier = 1
task_acks_late = True

beat_schedule = {
    "sync-legislation-every-24h": {
        "task": "legislation_worker.tasks.sync_legislation",
        "schedule": crontab(minute=0, hour=0),
    },
}
