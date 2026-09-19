from dataclasses import dataclass

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from aria.documents.models import DocumentIdentity
from aria.events.services import record_audit_event, record_pipeline_event
from aria.orchestration.models import ChangeOrchestration


@dataclass(frozen=True)
class DuplicateIdentityResolution:
    source_identity: DocumentIdentity
    target_identity: DocumentIdentity
    normalized_content_sha256: str
    completed_workflow_ids: tuple[str, ...]
    retried_workflow_ids: tuple[str, ...]
    created: bool


def queue_change_orchestration_retries(workflow_ids: tuple[str, ...]) -> None:
    from aria.orchestration.tasks import process_change_orchestration

    for workflow_id in workflow_ids:
        process_change_orchestration.delay(workflow_id)


def _current_version(identity: DocumentIdentity):
    return identity.versions.order_by("-created_at", "-id").first()


def validate_duplicate_identity_resolution(
    source_identity: DocumentIdentity,
    target_identity: DocumentIdentity,
) -> str:
    if source_identity.pk == target_identity.pk:
        raise ValueError("Source and target document identities must differ.")
    if source_identity.collection_id != target_identity.collection_id:
        raise ValueError("Duplicate document identities must belong to the same collection.")
    if source_identity.superseded_by_id:
        if source_identity.superseded_by_id == target_identity.pk:
            source_version = _current_version(source_identity)
            return source_version.normalized_content_sha256 if source_version else ""
        raise ValueError("The source document identity is already superseded by another identity.")
    if target_identity.superseded_by_id:
        raise ValueError("The target document identity must remain operational.")
    if source_identity.superseded_identities.exists():
        raise ValueError("A document identity with existing aliases cannot become a new alias.")
    source_version = _current_version(source_identity)
    target_version = _current_version(target_identity)
    if source_version is None or target_version is None:
        raise ValueError("Both document identities must have at least one evidence-backed version.")
    if not source_version.evidence_records.exists() or not target_version.evidence_records.exists():
        raise ValueError("Both current versions must retain immutable source evidence.")
    if source_version.normalized_content_sha256 != target_version.normalized_content_sha256:
        raise ValueError("Current versions must have identical normalized content.")
    return source_version.normalized_content_sha256


@transaction.atomic
def supersede_duplicate_identity(
    source_identity: DocumentIdentity,
    target_identity: DocumentIdentity,
    *,
    reason: str,
    actor_type: str,
    actor_identifier: str,
) -> DuplicateIdentityResolution:
    reason = reason.strip()
    actor_type = actor_type.strip()
    actor_identifier = actor_identifier.strip()
    if len(reason) < 20 or len(reason) > 2000:
        raise ValueError("Duplicate-identity reason must contain 20 to 2000 characters.")
    if not actor_type or not actor_identifier:
        raise ValueError("The resolution actor type and identifier are required.")
    locked = {
        row.pk: row
        for row in DocumentIdentity.objects.select_for_update()
        .filter(pk__in=(source_identity.pk, target_identity.pk))
        .select_related("collection")
    }
    if len(locked) != 2:
        raise ValueError("Both document identities must exist.")
    source_identity = locked[source_identity.pk]
    target_identity = locked[target_identity.pk]
    content_hash = validate_duplicate_identity_resolution(source_identity, target_identity)
    if source_identity.superseded_by_id == target_identity.pk:
        return DuplicateIdentityResolution(
            source_identity=source_identity,
            target_identity=target_identity,
            normalized_content_sha256=content_hash,
            completed_workflow_ids=(),
            retried_workflow_ids=(),
            created=False,
        )

    now = timezone.now()
    source_identity.superseded_by = target_identity
    source_identity.supersession_basis = {
        "strategy": "reviewed_duplicate_content_v1",
        "reason": reason,
        "actor_type": actor_type,
        "actor_identifier": actor_identifier,
        "normalized_content_sha256": content_hash,
        "target_identity_id": str(target_identity.id),
        "recorded_at": now.isoformat(),
    }
    source_identity.save(
        update_fields=("superseded_by", "supersession_basis", "updated_at")
    )

    source_workflows = list(
        ChangeOrchestration.objects.select_for_update().filter(
            document_version__identity=source_identity,
            status=ChangeOrchestration.Status.QUALITY_REVIEW_REQUIRED,
        )
    )
    target_workflows = list(
        ChangeOrchestration.objects.select_for_update().filter(
            document_version__identity=target_identity,
            status=ChangeOrchestration.Status.QUALITY_REVIEW_REQUIRED,
        )
    )
    completed_ids = tuple(str(row.id) for row in source_workflows)
    retried_ids = tuple(str(row.id) for row in target_workflows)
    if source_workflows:
        ChangeOrchestration.objects.filter(pk__in=[row.pk for row in source_workflows]).update(
            status=ChangeOrchestration.Status.COMPLETED,
            current_stage=ChangeOrchestration.Stage.COMPLETED,
            heartbeat_at=now,
            finished_at=now,
            error_code="",
            error_message="",
            updated_at=now,
        )
    if target_workflows:
        ChangeOrchestration.objects.filter(pk__in=[row.pk for row in target_workflows]).update(
            status=ChangeOrchestration.Status.PENDING,
            retry_count=F("retry_count") + 1,
            finished_at=None,
            error_code="",
            error_message="",
            updated_at=now,
        )

    details = {
        "source_identity_id": str(source_identity.id),
        "source_canonical_url": source_identity.canonical_url,
        "target_identity_id": str(target_identity.id),
        "target_canonical_url": target_identity.canonical_url,
        "normalized_content_sha256": content_hash,
        "reason": reason,
        "completed_workflow_ids": completed_ids,
        "retried_workflow_ids": retried_ids,
    }
    record_audit_event(
        action="document.duplicate_identity_superseded",
        target_type="document_identity",
        target_id=source_identity.id,
        actor_type=actor_type,
        actor_identifier=actor_identifier[:255],
        details=details,
    )
    record_pipeline_event(
        event_type="document.duplicate_identity_superseded",
        aggregate_type="document_identity",
        aggregate_id=source_identity.id,
        payload=details,
    )
    for workflow in source_workflows:
        record_pipeline_event(
            event_type="artifact.change_orchestration.completed",
            aggregate_type="change_orchestration",
            aggregate_id=workflow.id,
            payload={
                "outcome": "duplicate_identity_superseded",
                "document_version_id": str(workflow.document_version_id),
                "target_identity_id": str(target_identity.id),
            },
        )

    if target_workflows:
        workflow_ids = tuple(str(row.id) for row in target_workflows)
        transaction.on_commit(lambda: queue_change_orchestration_retries(workflow_ids))

    return DuplicateIdentityResolution(
        source_identity=source_identity,
        target_identity=target_identity,
        normalized_content_sha256=content_hash,
        completed_workflow_ids=completed_ids,
        retried_workflow_ids=retried_ids,
        created=True,
    )
