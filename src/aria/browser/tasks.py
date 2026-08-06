import logging

from celery import shared_task

from aria.browser.connector import ConfiguredJavaScriptListingConnector
from aria.discovery.models import SourceRun
from aria.discovery.services import (
    mark_source_run_failed,
    mark_source_run_started,
)
from aria.discovery.tasks import complete_source_discovery
from aria.events.services import record_pipeline_event
from aria.fetching.client import PermanentFetchError, UnsafeTargetError

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="aria.browser.tasks.execute_browser_source_run",
    acks_late=True,
    max_retries=3,
)
def execute_browser_source_run(self, source_run_id: str) -> None:
    source_run = SourceRun.objects.select_related(
        "endpoint",
        "endpoint__collection",
        "endpoint__collection__authority",
    ).get(pk=source_run_id)
    if source_run.status == SourceRun.Status.PENDING:
        mark_source_run_started(source_run)
    elif source_run.status != SourceRun.Status.RUNNING:
        logger.info("Skipping browser source run %s in state %s", source_run.id, source_run.status)
        return

    try:
        result = ConfiguredJavaScriptListingConnector().discover(source_run)
        complete_source_discovery(source_run, result)
    except (UnsafeTargetError, PermanentFetchError, ValueError) as error:
        logger.exception("Browser source run %s failed permanently", source_run.id)
        mark_source_run_failed(source_run, code=type(error).__name__, message=str(error))
        raise
    except Exception as error:
        logger.exception("Browser source run %s failed", source_run.id)
        if self.request.retries >= self.max_retries:
            mark_source_run_failed(source_run, code=type(error).__name__, message=str(error))
            raise
        record_pipeline_event(
            event_type="browser.source_run.retry_scheduled",
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
            countdown=min(120, 2 ** (self.request.retries + 1)),
        ) from error
