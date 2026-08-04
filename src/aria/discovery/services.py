import hashlib
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from aria.discovery.connectors import CandidateData
from aria.discovery.models import (
    CandidateObservation,
    DiscoveredCandidate,
    EndpointObservation,
    SourceRun,
)
from aria.events.services import record_audit_event, record_pipeline_event
from aria.fetching.client import FetchResponse
from aria.sources.models import SourceEndpoint

MONITOR_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-length",
        "content-type",
        "date",
        "etag",
        "expires",
        "last-modified",
        "retry-after",
    }
)


def endpoint_monitor_cursor(endpoint: SourceEndpoint) -> dict | None:
    observation = endpoint.endpoint_observations.order_by("-checked_at", "-id").first()
    if observation is None:
        return None
    return {
        "strategy": "http_conditional_v1",
        "endpoint_observation_id": str(observation.id),
        "source_run_id": str(observation.source_run_id),
        "content_sha256": observation.content_sha256,
        "etag": observation.etag,
        "last_modified": observation.last_modified,
    }


def conditional_headers_from_cursor(cursor: dict | None) -> dict[str, str]:
    if not cursor or cursor.get("strategy") != "http_conditional_v1":
        return {}
    headers: dict[str, str] = {}
    if cursor.get("etag"):
        headers["If-None-Match"] = str(cursor["etag"])
    if cursor.get("last_modified"):
        headers["If-Modified-Since"] = str(cursor["last_modified"])
    return headers


@transaction.atomic
def create_source_run(
    endpoint: SourceEndpoint,
    *,
    trigger: str,
    idempotency_key: str | None = None,
) -> tuple[SourceRun, bool]:
    key = idempotency_key or f"{trigger}:{endpoint.id}:{uuid.uuid4()}"
    source_run, created = SourceRun.objects.get_or_create(
        idempotency_key=key,
        defaults={
            "endpoint": endpoint,
            "trigger": trigger,
            "connector_configuration_version": endpoint.connector_configuration_version,
            "cursor_before": endpoint_monitor_cursor(endpoint),
        },
    )
    if created:
        record_pipeline_event(
            event_type="source.run.created",
            aggregate_type="source_run",
            aggregate_id=source_run.id,
            payload={"endpoint_id": str(endpoint.id), "trigger": trigger},
        )
    return source_run, created


@transaction.atomic
def schedule_due_source_runs() -> list[SourceRun]:
    now = timezone.now()
    endpoints = list(
        SourceEndpoint.objects.select_for_update(skip_locked=True)
        .filter(
            is_enabled=True,
            collection__is_enabled=True,
            collection__authority__is_enabled=True,
        )
        .filter(Q(next_poll_at__isnull=True) | Q(next_poll_at__lte=now))
        .order_by("next_poll_at")
    )
    created_runs: list[SourceRun] = []
    for endpoint in endpoints:
        scheduled_for = endpoint.next_poll_at or now
        source_run, created = create_source_run(
            endpoint,
            trigger=SourceRun.Trigger.SCHEDULED,
            idempotency_key=f"scheduled:{endpoint.id}:{scheduled_for.isoformat()}",
        )
        endpoint.next_poll_at = now + timedelta(minutes=endpoint.polling_interval_minutes)
        endpoint.save(update_fields=("next_poll_at", "updated_at"))
        if created:
            created_runs.append(source_run)
    return created_runs


@transaction.atomic
def mark_source_run_started(source_run: SourceRun) -> SourceRun:
    source_run.status = SourceRun.Status.RUNNING
    source_run.started_at = timezone.now()
    source_run.error_code = ""
    source_run.error_message = ""
    source_run.save(
        update_fields=("status", "started_at", "error_code", "error_message", "updated_at")
    )
    record_pipeline_event(
        event_type="source.run.started",
        aggregate_type="source_run",
        aggregate_id=source_run.id,
        payload={"endpoint_id": str(source_run.endpoint_id)},
    )
    return source_run


@transaction.atomic
def mark_source_run_completed(source_run: SourceRun, cursor_after: dict | None = None) -> SourceRun:
    now = timezone.now()
    observation = EndpointObservation.objects.filter(source_run=source_run).first()
    if cursor_after is None and observation is not None:
        cursor_after = endpoint_monitor_cursor(source_run.endpoint)
    source_run.status = SourceRun.Status.COMPLETED
    source_run.finished_at = now
    source_run.cursor_after = cursor_after
    source_run.save(update_fields=("status", "finished_at", "cursor_after", "updated_at"))
    endpoint = SourceEndpoint.objects.select_for_update().get(pk=source_run.endpoint_id)
    endpoint.last_checked_at = observation.checked_at if observation is not None else now
    if observation is not None and observation.outcome == EndpointObservation.Outcome.CHANGED:
        endpoint.last_changed_at = observation.checked_at
    endpoint.last_successful_run_at = now
    endpoint.consecutive_failures = 0
    endpoint.health_state = SourceEndpoint.HealthState.HEALTHY
    endpoint.save(
        update_fields=(
            "last_checked_at",
            "last_changed_at",
            "last_successful_run_at",
            "consecutive_failures",
            "health_state",
            "updated_at",
        )
    )
    record_pipeline_event(
        event_type="source.run.completed",
        aggregate_type="source_run",
        aggregate_id=source_run.id,
        payload={
            "candidate_count": source_run.discovered_candidate_count,
            "monitor_outcome": observation.outcome if observation is not None else "",
        },
    )
    return source_run


@transaction.atomic
def mark_source_run_failed(source_run: SourceRun, *, code: str, message: str) -> SourceRun:
    now = timezone.now()
    source_run.status = SourceRun.Status.FAILED
    source_run.finished_at = now
    source_run.error_code = code
    source_run.error_message = message
    source_run.save(
        update_fields=("status", "finished_at", "error_code", "error_message", "updated_at")
    )
    endpoint = SourceEndpoint.objects.select_for_update().get(pk=source_run.endpoint_id)
    endpoint.last_checked_at = now
    endpoint.consecutive_failures += 1
    endpoint.health_state = (
        SourceEndpoint.HealthState.UNHEALTHY
        if endpoint.consecutive_failures >= settings.MONITOR_UNHEALTHY_AFTER_FAILURES
        else SourceEndpoint.HealthState.DEGRADED
    )
    endpoint.save(
        update_fields=(
            "last_checked_at",
            "consecutive_failures",
            "health_state",
            "updated_at",
        )
    )
    record_pipeline_event(
        event_type="source.run.failed",
        aggregate_type="source_run",
        aggregate_id=source_run.id,
        payload={
            "error_code": code,
            "error_message": message,
            "consecutive_failures": endpoint.consecutive_failures,
            "health_state": endpoint.health_state,
        },
    )
    return source_run


@transaction.atomic
def record_endpoint_observation(
    source_run: SourceRun,
    response: FetchResponse,
    *,
    request_headers: dict[str, str] | None = None,
) -> EndpointObservation:
    previous = (
        EndpointObservation.objects.filter(endpoint=source_run.endpoint)
        .select_for_update()
        .order_by("-checked_at", "-id")
        .first()
    )
    if response.status_code == 304:
        if previous is None:
            raise ValueError("Received HTTP 304 without a previous endpoint observation.")
        outcome = EndpointObservation.Outcome.NOT_MODIFIED
        content_sha256 = previous.content_sha256
        byte_size = previous.byte_size
    else:
        content_sha256 = hashlib.sha256(response.content).hexdigest()
        byte_size = len(response.content)
        outcome = (
            EndpointObservation.Outcome.UNCHANGED
            if previous is not None and previous.content_sha256 == content_sha256
            else EndpointObservation.Outcome.CHANGED
        )
    response_headers = {
        key.lower(): value
        for key, value in response.headers.items()
        if key.lower() in MONITOR_RESPONSE_HEADERS
    }
    observation = EndpointObservation(
        source_run=source_run,
        endpoint=source_run.endpoint,
        previous_observation=previous,
        outcome=outcome,
        requested_url=response.requested_url,
        final_url=response.final_url,
        response_status=response.status_code,
        request_headers=request_headers or {},
        response_headers=response_headers,
        redirect_chain=response.redirect_chain,
        resolved_addresses=response.resolved_addresses,
        byte_size=byte_size,
        content_sha256=content_sha256,
        etag=response_headers.get("etag", previous.etag if previous is not None else ""),
        last_modified=response_headers.get(
            "last-modified", previous.last_modified if previous is not None else ""
        ),
        connector_configuration_version=source_run.connector_configuration_version,
    )
    observation.full_clean()
    observation.save()
    record_pipeline_event(
        event_type="source.endpoint.observed",
        aggregate_type="source_endpoint",
        aggregate_id=source_run.endpoint_id,
        payload={
            "source_run_id": str(source_run.id),
            "endpoint_observation_id": str(observation.id),
            "outcome": observation.outcome,
            "response_status": observation.response_status,
            "content_sha256": observation.content_sha256,
            "byte_size": observation.byte_size,
        },
    )
    return observation


@transaction.atomic
def observe_candidate(
    source_run: SourceRun,
    data: CandidateData,
) -> tuple[DiscoveredCandidate, bool]:
    now = timezone.now()
    candidate, created = DiscoveredCandidate.objects.get_or_create(
        endpoint=source_run.endpoint,
        fingerprint=data.fingerprint,
        defaults={
            "latest_source_run": source_run,
            "discovered_url": data.discovered_url,
            "canonical_url": data.canonical_url,
            "external_identifier": data.external_identifier,
            "metadata_hints": data.metadata_hints,
            "pipeline_state": DiscoveredCandidate.PipelineState.FETCH_PENDING,
            "first_discovered_at": now,
            "last_discovered_at": now,
        },
    )
    if not created:
        candidate.latest_source_run = source_run
        candidate.last_discovered_at = now
        candidate.discovered_url = data.discovered_url
        candidate.canonical_url = data.canonical_url
        candidate.external_identifier = data.external_identifier
        candidate.metadata_hints = data.metadata_hints
        candidate.pipeline_state = DiscoveredCandidate.PipelineState.FETCH_PENDING
        candidate.save(
            update_fields=(
                "latest_source_run",
                "last_discovered_at",
                "discovered_url",
                "canonical_url",
                "external_identifier",
                "metadata_hints",
                "pipeline_state",
                "updated_at",
            )
        )
    _, observation_created = CandidateObservation.objects.get_or_create(
        source_run=source_run,
        candidate=candidate,
    )
    if observation_created:
        SourceRun.objects.filter(pk=source_run.pk).update(
            discovered_candidate_count=F("discovered_candidate_count") + 1
        )
        source_run.refresh_from_db(fields=("discovered_candidate_count",))
    if created:
        record_pipeline_event(
            event_type="candidate.discovered",
            aggregate_type="candidate",
            aggregate_id=candidate.id,
            payload={
                "source_run_id": str(source_run.id),
                "endpoint_id": str(source_run.endpoint_id),
                "url": data.discovered_url,
            },
        )
    return candidate, created


def audit_manual_run(source_run: SourceRun, actor_identifier: str) -> None:
    record_audit_event(
        action="source.run.manual_queued",
        target_type="source_run",
        target_id=source_run.id,
        actor_type="user",
        actor_identifier=actor_identifier,
        details={"endpoint_id": str(source_run.endpoint_id)},
    )
