from uuid import UUID

from django.db import transaction

from aria.events.models import AuditEvent, OutboxEvent, PipelineEvent


@transaction.atomic
def record_pipeline_event(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    payload: dict | None = None,
) -> PipelineEvent:
    event = PipelineEvent.objects.create(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload or {},
    )
    OutboxEvent.objects.create(
        pipeline_event=event,
        topic=event_type,
        payload={
            "event_id": str(event.id),
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": str(aggregate_id),
            "occurred_at": event.occurred_at.isoformat(),
            "data": payload or {},
        },
    )
    return event


def record_audit_event(
    *,
    action: str,
    target_type: str,
    target_id: UUID,
    details: dict | None = None,
    actor_type: str = "system",
    actor_identifier: str = "",
) -> AuditEvent:
    return AuditEvent.objects.create(
        action=action,
        actor_type=actor_type,
        actor_identifier=actor_identifier,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
    )
