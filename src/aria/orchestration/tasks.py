from celery import shared_task

from aria.orchestration.models import ChangeOrchestration
from aria.orchestration.services import (
    RetryableOrchestrationError,
    claim_recoverable_orchestrations,
    run_change_orchestration,
)


@shared_task(
    bind=True,
    autoretry_for=(RetryableOrchestrationError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 4},
)
def process_change_orchestration(self, orchestration_id: str) -> str:
    orchestration = ChangeOrchestration.objects.get(pk=orchestration_id)
    return str(run_change_orchestration(orchestration).id)


@shared_task(name="aria.orchestration.tasks.recover_change_orchestrations")
def recover_change_orchestrations() -> dict:
    orchestrations = claim_recoverable_orchestrations()
    for orchestration in orchestrations:
        process_change_orchestration.delay(str(orchestration.id))
    return {
        "queued_count": len(orchestrations),
        "orchestration_ids": [str(item.id) for item in orchestrations],
    }
