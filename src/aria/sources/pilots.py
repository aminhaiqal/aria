from django.core.exceptions import ValidationError
from django.db import transaction

from aria.discovery.models import SourceRun
from aria.discovery.services import create_source_run
from aria.discovery.tasks import execute_source_run
from aria.events.services import record_audit_event
from aria.sources.models import SourceEndpoint, SourcePackSnapshot


class SourcePilotError(RuntimeError):
    pass


def resolve_installed_source(selector: str) -> SourceEndpoint | None:
    try:
        endpoint = SourceEndpoint.objects.filter(pk=selector).first()
    except (ValidationError, ValueError):
        endpoint = None
    if endpoint is not None:
        return endpoint
    snapshot = (
        SourcePackSnapshot.objects.filter(pack_slug=selector)
        .select_related("endpoint")
        .order_by("-pack_version", "-applied_at")
        .first()
    )
    return snapshot.endpoint if snapshot else None


@transaction.atomic
def queue_disabled_source_pilot(
    endpoint: SourceEndpoint,
    *,
    actor_type: str,
    actor_identifier: str,
) -> SourceRun:
    endpoint = SourceEndpoint.objects.select_for_update().get(pk=endpoint.pk)
    if (
        endpoint.connector_type != SourceEndpoint.ConnectorType.HTML_LISTING
        or endpoint.requires_javascript
    ):
        raise SourcePilotError("Static pilots require a non-JavaScript HTML listing source.")
    if endpoint.is_enabled:
        raise SourcePilotError("Use the normal source poll for an enabled endpoint.")
    if endpoint.next_poll_at is not None:
        raise SourcePilotError("A disabled pilot cannot have a scheduled poll.")
    snapshot = endpoint.source_pack_snapshots.order_by("-applied_at", "-id").first()
    if snapshot is None:
        raise SourcePilotError("Install a validated source pack before running this pilot.")
    overlap = SourceRun.objects.filter(
        endpoint=endpoint,
        status__in=(SourceRun.Status.PENDING, SourceRun.Status.RUNNING),
        resource_run__isnull=True,
    ).exists()
    if overlap:
        raise SourcePilotError("This source pilot already has active work.")
    source_run, _ = create_source_run(endpoint, trigger=SourceRun.Trigger.MANUAL)
    record_audit_event(
        action="source.pilot.queued",
        target_type="source_endpoint",
        target_id=endpoint.id,
        actor_type=actor_type,
        actor_identifier=actor_identifier,
        details={
            "source_run_id": str(source_run.id),
            "source_pack_snapshot_id": str(snapshot.id),
            "source_pack_checksum": snapshot.checksum,
            "connector_configuration_version": source_run.connector_configuration_version,
        },
    )
    transaction.on_commit(lambda: execute_source_run.delay(str(source_run.id)))
    return source_run
