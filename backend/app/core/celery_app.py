from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "anchor",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.crawl_tasks",
        "app.tasks.process_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Beat schedule: crawl all active bloggers every N hours
celery_app.conf.beat_schedule = {
    "crawl-all-active-bloggers": {
        "task": "app.tasks.crawl_tasks.crawl_all_active_bloggers",
        "schedule": crontab(minute=0, hour=f"*/{settings.CRAWL_INTERVAL_HOURS}"),
    },
}
