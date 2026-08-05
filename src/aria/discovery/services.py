import hashlib
import uuid
from datetime import timedelta
from pathlib import PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from aria.discovery.connectors import CandidateData
from aria.discovery.models import (
    CandidateObservation,
    DiscoveredCandidate,
    EndpointObservation,
    MonitoredResource,
    ResourceLinkObservation,
    ResourceObservation,
    ResourceRun,
    SourceRun,
)
from aria.discovery.resource_connectors import ResourceLinkData
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


def normalize_resource_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    hostname = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    return urlunsplit((parsed.scheme.lower(), hostname, parsed.path or "/", parsed.query, ""))


def monitored_resource_fingerprint(url: str) -> str:
    return hashlib.sha256(normalize_resource_url(url).encode("utf-8")).hexdigest()


@transaction.atomic
def register_monitored_resource(
    endpoint: SourceEndpoint,
    *,
    resource_type: str,
    url: str,
    title: str = "",
    is_approved: bool = False,
    is_enabled: bool | None = None,
    approval_basis: str = "",
    parent: MonitoredResource | None = None,
    metadata: dict | None = None,
    polling_interval_minutes: int | None = None,
    next_poll_at=None,
) -> tuple[MonitoredResource, bool]:
    normalized_url = normalize_resource_url(url)
    fingerprint = monitored_resource_fingerprint(normalized_url)
    interval = polling_interval_minutes or endpoint.polling_interval_minutes
    enabled = is_approved if is_enabled is None else is_enabled
    SourceEndpoint.objects.select_for_update().get(pk=endpoint.pk)
    exists = MonitoredResource.objects.filter(
        endpoint=endpoint,
        fingerprint=fingerprint,
    ).exists()
    resource_metadata = metadata or {}
    enabled_count = MonitoredResource.objects.filter(endpoint=endpoint, is_enabled=True).count()
    at_capacity = enabled_count >= settings.MONITOR_MAX_ENABLED_RESOURCES_PER_ENDPOINT
    if enabled and not exists and at_capacity:
        enabled = False
        resource_metadata = {**resource_metadata, "capacity_disposition": "disabled_at_cap"}
    if next_poll_at is None:
        stagger_window_seconds = max(1, interval * 60)
        offset_seconds = int(fingerprint[:8], 16) % stagger_window_seconds
        next_poll_at = timezone.now() + timedelta(seconds=offset_seconds)
    resource, created = MonitoredResource.objects.get_or_create(
        endpoint=endpoint,
        fingerprint=fingerprint,
        defaults={
            "parent": parent,
            "resource_type": resource_type,
            "url": normalized_url,
            "title": title[:512],
            "is_approved": is_approved,
            "is_enabled": enabled,
            "approval_basis": approval_basis[:255],
            "polling_interval_minutes": interval,
            "next_poll_at": next_poll_at,
            "metadata": resource_metadata,
        },
    )
    if not created:
        resource.parent = parent or resource.parent
        resource.resource_type = resource_type
        resource.url = normalized_url
        resource.title = title[:512] or resource.title
        resource.is_approved = resource.is_approved or is_approved
        if is_approved and (resource.is_enabled or not at_capacity):
            resource.is_enabled = True
        elif is_enabled is not None:
            resource.is_enabled = is_enabled
        resource.approval_basis = approval_basis[:255] or resource.approval_basis
        resource.polling_interval_minutes = interval
        resource.metadata = {**resource.metadata, **resource_metadata}
        if resource.next_poll_at is None and next_poll_at is not None:
            resource.next_poll_at = next_poll_at
        resource.full_clean()
        resource.save()
    else:
        resource.full_clean()
    if created:
        record_pipeline_event(
            event_type="source.resource.registered",
            aggregate_type="monitored_resource",
            aggregate_id=resource.id,
            payload={
                "endpoint_id": str(endpoint.id),
                "resource_type": resource_type,
                "url": normalized_url,
                "is_approved": is_approved,
            },
        )
    return resource, created


def resource_monitor_cursor(resource: MonitoredResource) -> dict | None:
    observation = resource.observations.order_by("-checked_at", "-id").first()
    if observation is None:
        return None
    return {
        "strategy": "resource_http_conditional_v1",
        "resource_observation_id": str(observation.id),
        "resource_run_id": str(observation.resource_run_id),
        "content_sha256": observation.content_sha256,
        "link_set_sha256": observation.link_set_sha256,
        "etag": observation.etag,
        "last_modified": observation.last_modified,
    }


def resource_conditional_headers(cursor: dict | None) -> dict[str, str]:
    if not cursor or cursor.get("strategy") != "resource_http_conditional_v1":
        return {}
    headers: dict[str, str] = {}
    if cursor.get("etag"):
        headers["If-None-Match"] = str(cursor["etag"])
    if cursor.get("last_modified"):
        headers["If-Modified-Since"] = str(cursor["last_modified"])
    return headers


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
def schedule_due_resource_runs() -> list[ResourceRun]:
    now = timezone.now()
    batch_size = settings.MONITOR_RESOURCE_BATCH_SIZE
    per_endpoint = settings.MONITOR_RESOURCE_BATCH_PER_ENDPOINT
    due = MonitoredResource.objects.filter(
        is_enabled=True,
        is_approved=True,
        next_poll_at__lte=now,
        endpoint__is_enabled=True,
        endpoint__collection__is_enabled=True,
        endpoint__collection__authority__is_enabled=True,
    ).exclude(runs__status__in=(ResourceRun.Status.PENDING, ResourceRun.Status.RUNNING))
    endpoint_ids = list(
        due.order_by("endpoint_id").values_list("endpoint_id", flat=True).distinct()[:batch_size]
    )
    resources_by_endpoint = {
        endpoint_id: list(
            due.select_for_update(skip_locked=True)
            .filter(endpoint_id=endpoint_id)
            .order_by("next_poll_at", "id")[:per_endpoint]
        )
        for endpoint_id in endpoint_ids
    }
    resources: list[MonitoredResource] = []
    for offset in range(per_endpoint):
        for endpoint_id in endpoint_ids:
            endpoint_resources = resources_by_endpoint[endpoint_id]
            if offset < len(endpoint_resources):
                resources.append(endpoint_resources[offset])
    scheduled: list[ResourceRun] = []
    for resource in resources[:batch_size]:
        scheduled_for = resource.next_poll_at
        resource_run, created = create_resource_run(
            resource,
            trigger=SourceRun.Trigger.SCHEDULED,
            idempotency_key=f"resource:scheduled:{resource.id}:{scheduled_for.isoformat()}",
        )
        resource.next_poll_at = now + timedelta(minutes=resource.polling_interval_minutes)
        resource.save(update_fields=("next_poll_at", "updated_at"))
        if created:
            scheduled.append(resource_run)
    return scheduled


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
    if observation is not None:
        endpoint = SourceEndpoint.objects.select_for_update().get(pk=source_run.endpoint_id)
        endpoint.last_checked_at = observation.checked_at
        if observation.outcome == EndpointObservation.Outcome.CHANGED:
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
    SourceEndpoint.objects.select_for_update().get(pk=source_run.endpoint_id)
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
        candidate.save(
            update_fields=(
                "latest_source_run",
                "last_discovered_at",
                "discovered_url",
                "canonical_url",
                "external_identifier",
                "metadata_hints",
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


@transaction.atomic
def create_resource_run(
    resource: MonitoredResource,
    *,
    trigger: str,
    idempotency_key: str | None = None,
) -> tuple[ResourceRun, bool]:
    key = idempotency_key or f"resource:{trigger}:{resource.id}:{uuid.uuid4()}"
    existing = ResourceRun.objects.filter(idempotency_key=key).first()
    if existing is not None:
        return existing, False
    source_run, source_created = create_source_run(
        resource.endpoint,
        trigger=trigger,
        idempotency_key=key,
    )
    if not source_created:
        existing = ResourceRun.objects.filter(source_run=source_run).first()
        if existing is not None:
            return existing, False
    cursor = resource_monitor_cursor(resource)
    resource_run = ResourceRun.objects.create(
        resource=resource,
        source_run=source_run,
        idempotency_key=key,
        cursor_before=cursor,
    )
    record_pipeline_event(
        event_type="source.resource_run.created",
        aggregate_type="resource_run",
        aggregate_id=resource_run.id,
        payload={
            "resource_id": str(resource.id),
            "source_run_id": str(source_run.id),
            "trigger": trigger,
        },
    )
    return resource_run, True


@transaction.atomic
def mark_resource_run_started(resource_run: ResourceRun) -> ResourceRun:
    now = timezone.now()
    resource_run.status = ResourceRun.Status.RUNNING
    resource_run.started_at = now
    resource_run.error_code = ""
    resource_run.error_message = ""
    resource_run.save(
        update_fields=("status", "started_at", "error_code", "error_message", "updated_at")
    )
    if resource_run.source_run.status == SourceRun.Status.PENDING:
        mark_source_run_started(resource_run.source_run)
    record_pipeline_event(
        event_type="source.resource_run.started",
        aggregate_type="resource_run",
        aggregate_id=resource_run.id,
        payload={"resource_id": str(resource_run.resource_id)},
    )
    return resource_run


@transaction.atomic
def mark_resource_run_completed(resource_run: ResourceRun) -> ResourceRun:
    now = timezone.now()
    observation = resource_run.observation
    cursor = resource_monitor_cursor(resource_run.resource)
    resource_run.status = ResourceRun.Status.COMPLETED
    resource_run.finished_at = now
    resource_run.cursor_after = cursor
    resource_run.save(update_fields=("status", "finished_at", "cursor_after", "updated_at"))
    resource = MonitoredResource.objects.select_for_update().get(pk=resource_run.resource_id)
    resource.last_checked_at = observation.checked_at
    if observation.outcome == ResourceObservation.Outcome.CHANGED:
        resource.last_changed_at = observation.checked_at
    resource.last_successful_run_at = now
    resource.consecutive_failures = 0
    resource.health_state = MonitoredResource.HealthState.HEALTHY
    resource.save(
        update_fields=(
            "last_checked_at",
            "last_changed_at",
            "last_successful_run_at",
            "consecutive_failures",
            "health_state",
            "updated_at",
        )
    )
    mark_source_run_completed(resource_run.source_run, cursor_after=cursor)
    record_pipeline_event(
        event_type="source.resource_run.completed",
        aggregate_type="resource_run",
        aggregate_id=resource_run.id,
        payload={
            "resource_id": str(resource.id),
            "outcome": observation.outcome,
            "link_count": observation.link_count,
        },
    )
    return resource_run


@transaction.atomic
def mark_resource_run_failed(
    resource_run: ResourceRun,
    *,
    code: str,
    message: str,
) -> ResourceRun:
    now = timezone.now()
    resource_run.status = ResourceRun.Status.FAILED
    resource_run.finished_at = now
    resource_run.error_code = code
    resource_run.error_message = message
    resource_run.save(
        update_fields=("status", "finished_at", "error_code", "error_message", "updated_at")
    )
    SourceRun.objects.filter(pk=resource_run.source_run_id).update(
        status=SourceRun.Status.FAILED,
        finished_at=now,
        error_code=code,
        error_message=message,
        updated_at=now,
    )
    resource = MonitoredResource.objects.select_for_update().get(pk=resource_run.resource_id)
    resource.last_checked_at = now
    resource.consecutive_failures += 1
    resource.health_state = (
        MonitoredResource.HealthState.UNHEALTHY
        if resource.consecutive_failures >= settings.MONITOR_UNHEALTHY_AFTER_FAILURES
        else MonitoredResource.HealthState.DEGRADED
    )
    resource.save(
        update_fields=(
            "last_checked_at",
            "consecutive_failures",
            "health_state",
            "updated_at",
        )
    )
    record_pipeline_event(
        event_type="source.resource_run.failed",
        aggregate_type="resource_run",
        aggregate_id=resource_run.id,
        payload={
            "resource_id": str(resource.id),
            "error_code": code,
            "error_message": message,
            "consecutive_failures": resource.consecutive_failures,
            "health_state": resource.health_state,
        },
    )
    return resource_run


def _current_resource_links(
    observation: ResourceObservation | None,
) -> dict[str, ResourceLinkData]:
    if observation is None:
        return {}
    return {
        link.fingerprint: ResourceLinkData(
            target_url=link.target_url,
            fingerprint=link.fingerprint,
            title=link.title,
            external_identifier=link.external_identifier,
            relation=link.relation,
            disposition=link.disposition,
            quarantine_reason=link.quarantine_reason,
            metadata=link.metadata,
        )
        for link in observation.link_observations.exclude(
            state=ResourceLinkObservation.State.REMOVED
        )
    }


@transaction.atomic
def record_resource_observation(
    resource_run: ResourceRun,
    response: FetchResponse,
    *,
    links: tuple[ResourceLinkData, ...] | None = None,
    request_headers: dict[str, str] | None = None,
) -> ResourceObservation:
    resource = MonitoredResource.objects.select_for_update().get(pk=resource_run.resource_id)
    previous = (
        ResourceObservation.objects.filter(resource=resource)
        .select_for_update()
        .order_by("-checked_at", "-id")
        .first()
    )
    previous_links = _current_resource_links(previous)
    if response.status_code == 304:
        if previous is None:
            raise ValueError("Received HTTP 304 without a previous resource observation.")
        current_links = previous_links
        outcome = ResourceObservation.Outcome.NOT_MODIFIED
        content_sha256 = previous.content_sha256
        byte_size = previous.byte_size
    else:
        current_links = {link.fingerprint: link for link in links or ()}
        content_sha256 = hashlib.sha256(response.content).hexdigest()
        byte_size = len(response.content)
        outcome = (
            ResourceObservation.Outcome.UNCHANGED
            if previous is not None and previous.content_sha256 == content_sha256
            else ResourceObservation.Outcome.CHANGED
        )
    link_set_sha256 = hashlib.sha256("\n".join(sorted(current_links)).encode("utf-8")).hexdigest()
    response_headers = {
        key.lower(): value
        for key, value in response.headers.items()
        if key.lower() in MONITOR_RESPONSE_HEADERS
    }
    observation = ResourceObservation(
        resource_run=resource_run,
        resource=resource,
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
        link_set_sha256=link_set_sha256,
        link_count=len(current_links),
    )
    observation.full_clean()
    observation.save()

    link_rows: list[ResourceLinkObservation] = []
    for fingerprint, link in current_links.items():
        state = (
            ResourceLinkObservation.State.RETAINED
            if fingerprint in previous_links
            else ResourceLinkObservation.State.ADDED
        )
        link_rows.append(
            ResourceLinkObservation(
                resource_observation=observation,
                target_url=link.target_url,
                fingerprint=fingerprint,
                title=link.title,
                external_identifier=link.external_identifier,
                relation=link.relation,
                state=state,
                disposition=link.disposition,
                quarantine_reason=link.quarantine_reason,
                metadata=link.metadata,
            )
        )
    for fingerprint, link in previous_links.items():
        if fingerprint in current_links:
            continue
        link_rows.append(
            ResourceLinkObservation(
                resource_observation=observation,
                target_url=link.target_url,
                fingerprint=fingerprint,
                title=link.title,
                external_identifier=link.external_identifier,
                relation=link.relation,
                state=ResourceLinkObservation.State.REMOVED,
                disposition=link.disposition,
                quarantine_reason=link.quarantine_reason,
                metadata=link.metadata,
            )
        )
    ResourceLinkObservation.objects.bulk_create(link_rows)
    resource_run.observed_link_count = len(link_rows)
    resource_run.quarantined_link_count = sum(
        link.disposition == ResourceLinkObservation.Disposition.QUARANTINED
        for link in current_links.values()
    )
    resource_run.save(update_fields=("observed_link_count", "quarantined_link_count", "updated_at"))
    record_pipeline_event(
        event_type="source.resource.observed",
        aggregate_type="monitored_resource",
        aggregate_id=resource.id,
        payload={
            "resource_run_id": str(resource_run.id),
            "resource_observation_id": str(observation.id),
            "outcome": outcome,
            "response_status": response.status_code,
            "content_sha256": content_sha256,
            "link_set_sha256": link_set_sha256,
            "link_count": len(current_links),
        },
    )
    return observation


@transaction.atomic
def reconcile_listing_candidates(
    endpoint: SourceEndpoint,
    candidates: tuple[CandidateData, ...],
    *,
    document_extensions: tuple[str, ...],
) -> tuple[CandidateData, ...]:
    fetchable: list[CandidateData] = []
    extensions = {extension.lower() for extension in document_extensions}
    for data in candidates:
        detail_url = str(data.metadata_hints.get("source_detail_page", ""))
        candidate_url = data.canonical_url or data.discovered_url
        suffix = PurePosixPath(urlsplit(candidate_url).path).suffix.lower()
        if detail_url:
            register_monitored_resource(
                endpoint,
                resource_type=MonitoredResource.ResourceType.DETAIL_PAGE,
                url=detail_url,
                title=str(data.metadata_hints.get("title", "")),
                is_approved=True,
                approval_basis="Discovered from the approved official listing",
                metadata={"source_listing": endpoint.discovery_url},
            )
            fetchable.append(data)
        elif suffix in extensions:
            fetchable.append(data)
        else:
            register_monitored_resource(
                endpoint,
                resource_type=MonitoredResource.ResourceType.DETAIL_PAGE,
                url=candidate_url,
                title=str(data.metadata_hints.get("title", "")),
                is_approved=True,
                approval_basis="Discovered from the approved official listing",
                metadata={"source_listing": endpoint.discovery_url},
            )
    return tuple(fetchable)


@transaction.atomic
def reconcile_resource_links(
    resource_run: ResourceRun,
    *,
    detail_path_prefixes: tuple[str, ...] = (),
) -> tuple[DiscoveredCandidate, ...]:
    observation = resource_run.observation
    accepted_candidates: list[DiscoveredCandidate] = []
    current_links = observation.link_observations.exclude(
        state=ResourceLinkObservation.State.REMOVED
    ).order_by("target_url")
    for link in current_links:
        if link.disposition != ResourceLinkObservation.Disposition.ACCEPTED:
            continue
        if link.relation == "entry":
            path = urlsplit(link.target_url).path
            in_scope = not detail_path_prefixes or any(
                path.startswith(prefix) for prefix in detail_path_prefixes
            )
            register_monitored_resource(
                resource_run.resource.endpoint,
                resource_type=MonitoredResource.ResourceType.DETAIL_PAGE,
                url=link.target_url,
                title=link.title,
                is_approved=in_scope,
                is_enabled=in_scope,
                approval_basis=(
                    "Discovered from an approved official feed"
                    if in_scope
                    else "Pending review: official feed entry is outside configured detail scope"
                ),
                parent=resource_run.resource,
                metadata={
                    **link.metadata,
                    "feed_external_identifier": link.external_identifier,
                    "scope_disposition": "approved" if in_scope else "pending_review",
                },
            )
            continue
        fingerprint_basis = f"{resource_run.resource.url}\n{link.target_url}".encode()
        candidate, _ = observe_candidate(
            resource_run.source_run,
            CandidateData(
                discovered_url=link.target_url,
                canonical_url=link.target_url,
                external_identifier=link.external_identifier,
                fingerprint=hashlib.sha256(fingerprint_basis).hexdigest(),
                metadata_hints={
                    **link.metadata,
                    "title": link.title or resource_run.resource.title,
                    "source_listing": resource_run.resource.endpoint.discovery_url,
                    "source_detail_page": resource_run.resource.url,
                    "document_identity_url": resource_run.resource.url,
                    "monitored_resource_id": str(resource_run.resource_id),
                    "resource_observation_id": str(observation.id),
                    "resource_link_state": link.state,
                },
            ),
        )
        accepted_candidates.append(candidate)
    resource_run.accepted_candidate_count = len(accepted_candidates)
    resource_run.save(update_fields=("accepted_candidate_count", "updated_at"))
    return tuple(accepted_candidates)
