"""Entry point: runs the Celery worker and beats scheduler together.

Usage (in separate processes or combined):
  python -m legislation_worker.worker
"""

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)

from .celery_app import app  # noqa: E402 — must come after logging setup

if __name__ == "__main__":
    mode = os.environ.get("WORKER_MODE", "worker")

    if mode == "beat":
        app.start(argv=["celery", "beat", "--loglevel=info"])
    else:
        app.start(
            argv=[
                "celery",
                "worker",
                "--loglevel=info",
                "--concurrency=2",
                "--queues=celery",
            ]
        )
