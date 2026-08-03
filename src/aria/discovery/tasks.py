import logging

from celery import shared_task

from aria.discovery.connectors import ConnectorNotRegistered, get_connector
from aria.discovery.models import SourceRun
from aria.discovery.services import (
    mark_source_run_completed,
    mark_source_run_failed,
    mark_source_run_started,
    observe_candidate,
    schedule_due_source_runs,
)
from aria.events.services import record_pipeline_event

logger = logging.getLogger(__name__)


@shared_task(name="aria.discovery.tasks.schedule_due_endpoints")
def schedule_due_endpoints() -> int:
    from aria.discovery.tasks import execute_source_run

    source_runs = schedule_due_source_runs()
    for source_run in source_runs:
        execute_source_run.delay(str(source_run.id))
    return len(source_runs)


@shared_task(
    bind=True,
    name="aria.discovery.tasks.execute_source_run",
    acks_late=True,
    max_retries=5,
)
def execute_source_run(self, source_run_id: str) -> None:
    source_run = SourceRun.objects.select_related("endpoint").get(pk=source_run_id)
    if source_run.status == SourceRun.Status.PENDING:
        mark_source_run_started(source_run)
    elif source_run.status != SourceRun.Status.RUNNING:
        logger.info("Skipping source run %s in state %s", source_run.id, source_run.status)
        return

    try:
        connector = get_connector(source_run.endpoint.connector_type)
        candidates = connector.discover(source_run.endpoint, source_run.cursor_before)
        for candidate_data in candidates:
            observe_candidate(source_run, candidate_data)
        mark_source_run_completed(source_run)
    except ConnectorNotRegistered as error:
        mark_source_run_failed(source_run, code="connector_not_registered", message=str(error))
    except Exception as error:
        logger.exception("Source run %s failed", source_run.id)
        if self.request.retries >= self.max_retries:
            mark_source_run_failed(source_run, code=type(error).__name__, message=str(error))
            raise
        record_pipeline_event(
            event_type="source.run.retry_scheduled",
            aggregate_type="source_run",
            aggregate_id=source_run.id,
            payload={
                "attempt": self.request.retries + 1,
                "error_code": type(error).__name__,
                "error_message": str(error),
            },
        )
        raise self.retry(
            exc=error,
            countdown=min(300, 2 ** (self.request.retries + 1)),
        ) from error
