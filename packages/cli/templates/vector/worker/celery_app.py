"""Celery application configuration."""

import os
import sys
import importlib.util

from celery import Celery

# Ensure parent directory is in path for imports
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)


def _import_module(name: str, filepath: str):
    """Import a module directly from its file path."""
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Import config directly to avoid __init__.py chain
_config = _import_module("_config", os.path.join(_parent_dir, "config.py"))
settings = _config.settings

# Create Celery app
celery_app = Celery(
    "vector_worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["worker.tasks"],
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task execution settings
    task_acks_late=True,  # Acknowledge after task completes
    task_reject_on_worker_lost=True,  # Requeue if worker dies
    task_time_limit=600,  # 10 minute hard limit
    task_soft_time_limit=540,  # 9 minute soft limit (allows cleanup)

    # Worker settings
    worker_prefetch_multiplier=1,  # Fetch one task at a time
    worker_concurrency=2,  # 2 concurrent tasks per worker

    # Result backend settings
    result_expires=86400,  # Results expire after 24 hours

    # Retry settings
    task_default_retry_delay=60,  # 1 minute retry delay
    task_max_retries=3,

    # Queue settings
    task_default_queue="vector_ingestion",
    task_queues={
        "vector_ingestion": {
            "exchange": "vector",
            "routing_key": "vector.ingestion",
        }
    },
)


# Task routing
celery_app.conf.task_routes = {
    "worker.tasks.process_ingestion": {"queue": "vector_ingestion"},
}
