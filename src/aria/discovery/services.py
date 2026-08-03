import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from aria.discovery.connectors import CandidateData
from aria.discovery.models import CandidateObservation, DiscoveredCandidate, SourceRun
from aria.events.services import record_audit_event, record_pipeline_event
from aria.sources.models import SourceEndpoint


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
    source_run.status = SourceRun.Status.COMPLETED
    source_run.finished_at = now
    source_run.cursor_after = cursor_after
    source_run.save(update_fields=("status", "finished_at", "cursor_after", "updated_at"))
    SourceEndpoint.objects.filter(pk=source_run.endpoint_id).update(
        last_successful_run_at=now,
        health_state=SourceEndpoint.HealthState.HEALTHY,
        updated_at=now,
    )
    record_pipeline_event(
        event_type="source.run.completed",
        aggregate_type="source_run",
        aggregate_id=source_run.id,
        payload={"candidate_count": source_run.discovered_candidate_count},
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
    SourceEndpoint.objects.filter(pk=source_run.endpoint_id).update(
        health_state=SourceEndpoint.HealthState.UNHEALTHY,
        updated_at=now,
    )
    record_pipeline_event(
        event_type="source.run.failed",
        aggregate_type="source_run",
        aggregate_id=source_run.id,
        payload={"error_code": code, "error_message": message},
    )
    return source_run


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
