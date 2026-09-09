from celery import shared_task

from aria.health.metrics import record_pipeline_heartbeat


@shared_task(name="aria.health.tasks.record_pipeline_heartbeat")
def heartbeat_scheduler_worker() -> str:
    return record_pipeline_heartbeat().isoformat()
