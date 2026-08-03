import logging
import random

from celery import shared_task

from aria.discovery.models import DiscoveredCandidate, SourceRun
from aria.fetching.client import (
    PermanentFetchError,
    RetryableFetchError,
    UnsafeTargetError,
    get_default_http_client,
)
from aria.fetching.models import FetchAttempt
from aria.fetching.services import (
    begin_fetch_attempt,
    complete_fetch,
    complete_not_modified,
    fail_fetch_attempt,
    latest_conditional_headers,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="aria.fetching.tasks.fetch_candidate",
    acks_late=True,
    max_retries=5,
)
def fetch_candidate(self, candidate_id: str, source_run_id: str) -> None:
    candidate = DiscoveredCandidate.objects.select_related("endpoint").get(pk=candidate_id)
    source_run = SourceRun.objects.get(pk=source_run_id)
    if FetchAttempt.objects.filter(
        candidate=candidate,
        source_run=source_run,
        status__in=(FetchAttempt.Status.SUCCEEDED, FetchAttempt.Status.NOT_MODIFIED),
    ).exists():
        logger.info(
            "Skipping completed candidate fetch %s for run %s",
            candidate.id,
            source_run.id,
        )
        return

    conditional_headers = latest_conditional_headers(candidate)
    attempt = begin_fetch_attempt(
        candidate,
        source_run,
        request_headers=conditional_headers,
    )
    client = get_default_http_client()
    try:
        response = client.fetch(
            candidate.canonical_url or candidate.discovered_url,
            allowed_domains=candidate.endpoint.allowed_domains,
            headers=conditional_headers,
        )
        if response.status_code == 304:
            complete_not_modified(attempt, response)
        else:
            complete_fetch(attempt, response)
    except UnsafeTargetError as error:
        fail_fetch_attempt(
            attempt,
            status=FetchAttempt.Status.QUARANTINED,
            pipeline_state=DiscoveredCandidate.PipelineState.QUARANTINED,
            error_code=type(error).__name__,
            error_message=str(error),
        )
    except PermanentFetchError as error:
        fail_fetch_attempt(
            attempt,
            status=FetchAttempt.Status.PERMANENT_FAILURE,
            pipeline_state=DiscoveredCandidate.PipelineState.PERMANENT_FAILURE,
            error_code=type(error).__name__,
            error_message=str(error),
        )
    except Exception as error:
        logger.exception("Candidate fetch %s failed", candidate.id)
        if not isinstance(error, RetryableFetchError):
            error = RetryableFetchError(str(error))
        exhausted = self.request.retries >= self.max_retries
        fail_fetch_attempt(
            attempt,
            status=(
                FetchAttempt.Status.PERMANENT_FAILURE
                if exhausted
                else FetchAttempt.Status.RETRYABLE_FAILURE
            ),
            pipeline_state=(
                DiscoveredCandidate.PipelineState.PERMANENT_FAILURE
                if exhausted
                else DiscoveredCandidate.PipelineState.RETRYABLE_FAILURE
            ),
            error_code=type(error).__name__,
            error_message=str(error),
        )
        if exhausted:
            raise
        raise self.retry(
            exc=error,
            countdown=min(300, 2 ** (self.request.retries + 1)) + random.uniform(0, 1),
        ) from error
    finally:
        client.close()
