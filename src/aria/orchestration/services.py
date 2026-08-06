import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from aria.artifacts.models import ArtifactDerivative, ArtifactObservation
from aria.artifacts.storage import ArtifactStorageError, get_artifact_store
from aria.comparisons.anchors import project_version_anchors
from aria.comparisons.lineage import project_version_lineage
from aria.comparisons.models import (
    ComparisonItem,
    ComparisonReview,
    ComparisonSummary,
    VersionLineageAssessment,
)
from aria.comparisons.services import compare_document_versions, eligible_comparison_pairs
from aria.documents.models import VersionEvidence
from aria.events.services import record_pipeline_event
from aria.extraction.models import ExtractionRun
from aria.extraction.services import RetryableExtractionError, extract_artifact
from aria.knowledge.services import project_document_version
from aria.orchestration.models import ChangeOrchestration, OrchestrationStepAttempt
from aria.quality.models import DocumentQualityAssessment
from aria.quality.services import QualityAssessmentInProgress, assess_collection_quality


class OrchestrationError(RuntimeError):
    pass


class RetryableOrchestrationError(OrchestrationError):
    pass


class PermanentOrchestrationError(OrchestrationError):
    pass


class OrchestrationInProgress(RetryableOrchestrationError):
    pass


TERMINAL_STATUSES = frozenset(
    {
        ChangeOrchestration.Status.NO_CONTENT_CHANGE,
        ChangeOrchestration.Status.QUALITY_REVIEW_REQUIRED,
        ChangeOrchestration.Status.LINEAGE_REJECTED,
        ChangeOrchestration.Status.REVIEW_REQUIRED,
        ChangeOrchestration.Status.SUMMARY_PENDING,
        ChangeOrchestration.Status.COMPLETED,
    }
)


def _canonical_hash(value: dict) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def orchestration_idempotency_key(observation: ArtifactObservation) -> str:
    return _canonical_hash(
        {
            "strategy": "artifact_change_orchestration_v1",
            "artifact_observation_id": str(observation.id),
            "artifact_sha256": observation.raw_artifact.sha256,
        }
    )


@transaction.atomic
def ensure_change_orchestration(
    observation: ArtifactObservation,
) -> tuple[ChangeOrchestration, bool]:
    if not observation.content_changed:
        raise ValueError("Unchanged artifact observations do not require orchestration.")
    orchestration, created = ChangeOrchestration.objects.get_or_create(
        artifact_observation=observation,
        defaults={
            "source_artifact": observation.raw_artifact,
            "idempotency_key": orchestration_idempotency_key(observation),
        },
    )
    if created:
        orchestration.full_clean()
        record_pipeline_event(
            event_type="artifact.change_orchestration.created",
            aggregate_type="change_orchestration",
            aggregate_id=orchestration.id,
            payload={
                "artifact_observation_id": str(observation.id),
                "artifact_id": str(observation.raw_artifact_id),
                "artifact_sha256": observation.raw_artifact.sha256,
            },
        )
    return orchestration, created


def _record_step(
    orchestration: ChangeOrchestration,
    *,
    stage: str,
    outcome: str,
    input_payload: dict,
    started_at,
    output: dict | None = None,
    error: Exception | None = None,
) -> OrchestrationStepAttempt:
    maximum = (
        OrchestrationStepAttempt.objects.filter(orchestration=orchestration, stage=stage).aggregate(
            maximum=Max("attempt_number")
        )["maximum"]
        or 0
    )
    return OrchestrationStepAttempt.objects.create(
        orchestration=orchestration,
        stage=stage,
        attempt_number=maximum + 1,
        outcome=outcome,
        input_hash=_canonical_hash(input_payload),
        output=output or {},
        error_code=type(error).__name__ if error else "",
        error_message=str(error)[:4000] if error else "",
        started_at=started_at,
    )


def _heartbeat(orchestration: ChangeOrchestration, stage: str) -> None:
    now = timezone.now()
    ChangeOrchestration.objects.filter(pk=orchestration.pk).update(
        current_stage=stage,
        heartbeat_at=now,
        updated_at=now,
    )
    orchestration.current_stage = stage
    orchestration.heartbeat_at = now


def _execute_stage(
    orchestration: ChangeOrchestration,
    *,
    stage: str,
    input_payload: dict,
    operation,
):
    _heartbeat(orchestration, stage)
    started_at = timezone.now()
    try:
        value, output = operation()
    except Exception as error:
        _record_step(
            orchestration,
            stage=stage,
            outcome=OrchestrationStepAttempt.Outcome.FAILED,
            input_payload=input_payload,
            started_at=started_at,
            error=error,
        )
        raise
    _record_step(
        orchestration,
        stage=stage,
        outcome=OrchestrationStepAttempt.Outcome.COMPLETED,
        input_payload=input_payload,
        started_at=started_at,
        output=output,
    )
    return value


@transaction.atomic
def _claim(orchestration: ChangeOrchestration) -> tuple[ChangeOrchestration, bool]:
    locked = (
        ChangeOrchestration.objects.select_for_update()
        .select_related("artifact_observation", "source_artifact")
        .get(pk=orchestration.pk)
    )
    if locked.status in TERMINAL_STATUSES:
        return locked, False
    if locked.status == ChangeOrchestration.Status.WAITING_OCR:
        evidence_exists = VersionEvidence.objects.filter(
            artifact_observation=locked.artifact_observation
        ).exists()
        if not evidence_exists:
            return locked, False
    stale_before = timezone.now() - timedelta(minutes=settings.ORCHESTRATION_STALE_AFTER_MINUTES)
    if (
        locked.status == ChangeOrchestration.Status.RUNNING
        and locked.heartbeat_at
        and locked.heartbeat_at > stale_before
    ):
        raise OrchestrationInProgress("This artifact orchestration is already running.")
    now = timezone.now()
    locked.status = ChangeOrchestration.Status.RUNNING
    locked.started_at = locked.started_at or now
    locked.heartbeat_at = now
    locked.finished_at = None
    locked.error_code = ""
    locked.error_message = ""
    locked.save(
        update_fields=(
            "status",
            "started_at",
            "heartbeat_at",
            "finished_at",
            "error_code",
            "error_message",
            "updated_at",
        )
    )
    return locked, True


def _finish(
    orchestration: ChangeOrchestration,
    *,
    status: str,
    stage: str,
    payload: dict | None = None,
) -> ChangeOrchestration:
    now = timezone.now()
    ChangeOrchestration.objects.filter(pk=orchestration.pk).update(
        status=status,
        current_stage=stage,
        heartbeat_at=now,
        finished_at=now,
        updated_at=now,
    )
    orchestration.refresh_from_db()
    record_pipeline_event(
        event_type=f"artifact.change_orchestration.{status}",
        aggregate_type="change_orchestration",
        aggregate_id=orchestration.id,
        payload={
            "artifact_observation_id": str(orchestration.artifact_observation_id),
            "document_version_id": str(orchestration.document_version_id or ""),
            "comparison_id": str(orchestration.comparison_id or ""),
            **(payload or {}),
        },
    )
    return orchestration


def _fail(orchestration: ChangeOrchestration, error: Exception) -> None:
    now = timezone.now()
    ChangeOrchestration.objects.filter(pk=orchestration.pk).update(
        status=ChangeOrchestration.Status.FAILED,
        heartbeat_at=now,
        finished_at=now,
        error_code=type(error).__name__,
        error_message=str(error)[:4000],
        updated_at=now,
    )
    record_pipeline_event(
        event_type="artifact.change_orchestration.failed",
        aggregate_type="change_orchestration",
        aggregate_id=orchestration.id,
        payload={"error_code": type(error).__name__, "error_message": str(error)[:4000]},
    )


def _verify_artifact(orchestration: ChangeOrchestration):
    artifact = orchestration.source_artifact
    store = get_artifact_store(artifact.storage_backend)
    content = store.read(artifact.storage_key)
    if len(content) != artifact.byte_size:
        raise ArtifactStorageError("Stored artifact byte size does not match its record.")
    if hashlib.sha256(content).hexdigest() != artifact.sha256:
        raise ArtifactStorageError("Stored artifact failed SHA-256 verification.")
    return artifact, {
        "artifact_id": str(artifact.id),
        "sha256": artifact.sha256,
        "byte_size": artifact.byte_size,
        "storage_backend": artifact.storage_backend,
    }


def _extract(orchestration: ChangeOrchestration):
    try:
        run = extract_artifact(orchestration.source_artifact, project_knowledge=False)
    except RetryableExtractionError as error:
        raise RetryableOrchestrationError(str(error)) from error
    ChangeOrchestration.objects.filter(pk=orchestration.pk).update(
        extraction_run=run,
        updated_at=timezone.now(),
    )
    orchestration.extraction_run = run
    if run.status == ExtractionRun.Status.RETRYABLE_FAILURE:
        raise RetryableOrchestrationError(run.error_message or "Extraction requires retry.")
    if run.status == ExtractionRun.Status.PERMANENT_FAILURE:
        raise PermanentOrchestrationError(run.error_message or "Extraction failed permanently.")
    return run, {"extraction_run_id": str(run.id), "status": run.status}


def _existing_extraction(orchestration: ChangeOrchestration):
    evidence = VersionEvidence.objects.select_related("extraction_run").get(
        artifact_observation=orchestration.artifact_observation
    )
    run = evidence.extraction_run
    ChangeOrchestration.objects.filter(pk=orchestration.pk).update(
        extraction_run=run,
        updated_at=timezone.now(),
    )
    orchestration.extraction_run = run
    return run, {
        "extraction_run_id": str(run.id),
        "status": run.status,
        "resumed_from_version_evidence": True,
    }


def _resolve_version(orchestration: ChangeOrchestration):
    try:
        evidence = VersionEvidence.objects.select_related(
            "document_version", "document_version__identity", "extraction_run"
        ).get(artifact_observation=orchestration.artifact_observation)
    except VersionEvidence.DoesNotExist as error:
        raise RetryableOrchestrationError(
            "Extraction completed without version evidence for this observation."
        ) from error
    version = evidence.document_version
    previous_evidence = (
        VersionEvidence.objects.filter(
            document_version__identity=version.identity,
            artifact_observation__retrieved_at__lt=(
                orchestration.artifact_observation.retrieved_at
            ),
        )
        .select_related("document_version", "artifact_observation")
        .order_by("-artifact_observation__retrieved_at", "-created_at")
        .first()
    )
    normalized_content_changed = (
        previous_evidence is None or previous_evidence.document_version_id != version.id
    )
    ChangeOrchestration.objects.filter(pk=orchestration.pk).update(
        extraction_run=evidence.extraction_run,
        document_version=version,
        updated_at=timezone.now(),
    )
    orchestration.extraction_run = evidence.extraction_run
    orchestration.document_version = version
    return version, {
        "document_version_id": str(version.id),
        "identity_id": str(version.identity_id),
        "normalized_content_sha256": version.normalized_content_sha256,
        "normalized_content_changed": normalized_content_changed,
        "previous_document_version_id": (
            str(previous_evidence.document_version_id) if previous_evidence else ""
        ),
    }


def _assess_quality(orchestration: ChangeOrchestration):
    version = orchestration.document_version
    try:
        quality_run = assess_collection_quality(version.identity.collection)
    except QualityAssessmentInProgress as error:
        raise RetryableOrchestrationError(str(error)) from error
    assessment = quality_run.document_assessments.get(document_version=version)
    ChangeOrchestration.objects.filter(pk=orchestration.pk).update(
        quality_run=quality_run,
        updated_at=timezone.now(),
    )
    orchestration.quality_run = quality_run
    return assessment, {
        "quality_run_id": str(quality_run.id),
        "assessment_id": str(assessment.id),
        "outcome": assessment.outcome,
        "score": assessment.score,
        "finding_count": assessment.findings.count(),
    }


def _project_knowledge(orchestration: ChangeOrchestration):
    project_document_version(orchestration.document_version)
    version = orchestration.document_version
    return version, {
        "document_version_id": str(version.id),
        "section_count": version.sections.count(),
        "graph_edge_count": version.graph_edges.count(),
        "local_embedding_count": sum(
            section.embeddings.filter(provider="local_hash").count()
            for section in version.sections.all()
        ),
    }


def _project_lineage(orchestration: ChangeOrchestration):
    version = orchestration.document_version
    for identity_version in version.identity.versions.order_by("created_at", "id"):
        project_version_lineage(identity_version)
    assessment, _ = project_version_lineage(version)
    anchor_count, created_anchor_count = project_version_anchors(version)
    ChangeOrchestration.objects.filter(pk=orchestration.pk).update(
        lineage_assessment=assessment,
        updated_at=timezone.now(),
    )
    orchestration.lineage_assessment = assessment
    return assessment, {
        "lineage_assessment_id": str(assessment.id),
        "provenance_status": assessment.provenance_status,
        "representation_kind": assessment.representation_kind,
        "comparison_track_key": assessment.comparison_track_key,
        "anchor_count": anchor_count,
        "created_anchor_count": created_anchor_count,
    }


def _compare(orchestration: ChangeOrchestration):
    version = orchestration.document_version
    pair = next(
        (
            (before, after)
            for before, after in eligible_comparison_pairs(version.identity)
            if after.id == version.id
        ),
        None,
    )
    if pair is None:
        return None, {"eligible_pair": False, "reason": "no_prior_eligible_version"}
    comparison, created = compare_document_versions(*pair)
    ChangeOrchestration.objects.filter(pk=orchestration.pk).update(
        comparison=comparison,
        updated_at=timezone.now(),
    )
    orchestration.comparison = comparison
    material_count = comparison.items.exclude(
        change_type__in=(
            ComparisonItem.ChangeType.UNCHANGED,
            ComparisonItem.ChangeType.FORMAT_ONLY,
        )
    ).count()
    return comparison, {
        "eligible_pair": True,
        "comparison_id": str(comparison.id),
        "created": created,
        "material_change_count": material_count,
        "counts": {
            "added": comparison.added_count,
            "removed": comparison.removed_count,
            "modified": comparison.modified_count,
            "moved": comparison.moved_count,
            "format_only": comparison.format_only_count,
            "ambiguous": comparison.ambiguous_count,
        },
    }


def run_change_orchestration(orchestration: ChangeOrchestration) -> ChangeOrchestration:
    orchestration, claimed = _claim(orchestration)
    if not claimed:
        return orchestration
    try:
        _execute_stage(
            orchestration,
            stage=ChangeOrchestration.Stage.ARTIFACT,
            input_payload={
                "artifact_observation_id": str(orchestration.artifact_observation_id),
                "expected_sha256": orchestration.source_artifact.sha256,
            },
            operation=lambda: _verify_artifact(orchestration),
        )
        evidence_ready = VersionEvidence.objects.filter(
            artifact_observation=orchestration.artifact_observation
        ).exists()
        extraction_run = _execute_stage(
            orchestration,
            stage=ChangeOrchestration.Stage.EXTRACTION,
            input_payload={
                "artifact_id": str(orchestration.source_artifact_id),
                "version_evidence_ready": evidence_ready,
            },
            operation=lambda: (
                _existing_extraction(orchestration) if evidence_ready else _extract(orchestration)
            ),
        )
        if extraction_run.status == ExtractionRun.Status.OCR_REQUIRED:
            from aria.ocr.services import ensure_ocr_run
            from aria.ocr.tasks import process_ocr

            ocr_run = ensure_ocr_run(orchestration.source_artifact)
            _record_step(
                orchestration,
                stage=ChangeOrchestration.Stage.EXTRACTION,
                outcome=OrchestrationStepAttempt.Outcome.WAITING,
                input_payload={"extraction_run_id": str(extraction_run.id)},
                started_at=timezone.now(),
                output={"ocr_run_id": str(ocr_run.id)},
            )
            transaction.on_commit(
                lambda: process_ocr.delay(str(ocr_run.id), project_knowledge=False)
            )
            return _finish(
                orchestration,
                status=ChangeOrchestration.Status.WAITING_OCR,
                stage=ChangeOrchestration.Stage.EXTRACTION,
                payload={"ocr_run_id": str(ocr_run.id)},
            )
        version = _execute_stage(
            orchestration,
            stage=ChangeOrchestration.Stage.VERSION,
            input_payload={
                "artifact_observation_id": str(orchestration.artifact_observation_id),
                "extraction_run_id": str(extraction_run.id),
            },
            operation=lambda: _resolve_version(orchestration),
        )
        version_output = (
            orchestration.step_attempts.filter(
                stage=ChangeOrchestration.Stage.VERSION,
                outcome=OrchestrationStepAttempt.Outcome.COMPLETED,
            )
            .latest("finished_at")
            .output
        )
        if not version_output["normalized_content_changed"]:
            return _finish(
                orchestration,
                status=ChangeOrchestration.Status.NO_CONTENT_CHANGE,
                stage=ChangeOrchestration.Stage.COMPLETED,
            )
        assessment = _execute_stage(
            orchestration,
            stage=ChangeOrchestration.Stage.QUALITY,
            input_payload={"document_version_id": str(version.id)},
            operation=lambda: _assess_quality(orchestration),
        )
        if assessment.outcome == DocumentQualityAssessment.Outcome.REVIEW_REQUIRED:
            return _finish(
                orchestration,
                status=ChangeOrchestration.Status.QUALITY_REVIEW_REQUIRED,
                stage=ChangeOrchestration.Stage.QUALITY,
                payload={"quality_assessment_id": str(assessment.id)},
            )
        _execute_stage(
            orchestration,
            stage=ChangeOrchestration.Stage.KNOWLEDGE,
            input_payload={"document_version_id": str(version.id)},
            operation=lambda: _project_knowledge(orchestration),
        )
        lineage = _execute_stage(
            orchestration,
            stage=ChangeOrchestration.Stage.LINEAGE,
            input_payload={"document_version_id": str(version.id)},
            operation=lambda: _project_lineage(orchestration),
        )
        if lineage.provenance_status == VersionLineageAssessment.ProvenanceStatus.QUARANTINED:
            return _finish(
                orchestration,
                status=ChangeOrchestration.Status.LINEAGE_REJECTED,
                stage=ChangeOrchestration.Stage.LINEAGE,
            )
        comparison = _execute_stage(
            orchestration,
            stage=ChangeOrchestration.Stage.COMPARISON,
            input_payload={
                "document_version_id": str(version.id),
                "comparison_track_key": lineage.comparison_track_key,
            },
            operation=lambda: _compare(orchestration),
        )
        if comparison is None:
            return _finish(
                orchestration,
                status=ChangeOrchestration.Status.COMPLETED,
                stage=ChangeOrchestration.Stage.COMPLETED,
                payload={"outcome": "baseline_version"},
            )
        material_count = comparison.items.exclude(
            change_type__in=(
                ComparisonItem.ChangeType.UNCHANGED,
                ComparisonItem.ChangeType.FORMAT_ONLY,
            )
        ).count()
        if material_count:
            return _finish(
                orchestration,
                status=ChangeOrchestration.Status.REVIEW_REQUIRED,
                stage=ChangeOrchestration.Stage.REVIEW,
                payload={"material_change_count": material_count},
            )
        return _finish(
            orchestration,
            status=ChangeOrchestration.Status.COMPLETED,
            stage=ChangeOrchestration.Stage.COMPLETED,
            payload={"outcome": "no_material_change"},
        )
    except (ArtifactStorageError, RetryableOrchestrationError) as error:
        retryable = RetryableOrchestrationError(str(error))
        _fail(orchestration, retryable)
        raise retryable from error
    except Exception as error:
        _fail(orchestration, error)
        raise


def comparison_review_state(comparison) -> dict:
    items = list(
        comparison.items.exclude(change_type=ComparisonItem.ChangeType.UNCHANGED).prefetch_related(
            "reviews"
        )
    )
    decisions = []
    for item in items:
        latest = item.reviews.order_by("-created_at", "-id").first()
        decisions.append(latest.decision if latest else None)
    return {
        "item_count": len(items),
        "reviewed_count": sum(decision is not None for decision in decisions),
        "confirmed_count": sum(
            decision == ComparisonReview.Decision.CONFIRMED for decision in decisions
        ),
        "complete": bool(items) and all(decision is not None for decision in decisions),
    }


@transaction.atomic
def queue_summary_after_completed_review(comparison) -> dict:
    state = comparison_review_state(comparison)
    if not state["complete"]:
        return state
    orchestrations = list(
        ChangeOrchestration.objects.select_for_update().filter(
            comparison=comparison,
            status__in=(
                ChangeOrchestration.Status.REVIEW_REQUIRED,
                ChangeOrchestration.Status.SUMMARY_FAILED,
            ),
        )
    )
    if not state["confirmed_count"] or not settings.ORCHESTRATION_AUTO_GPT_SUMMARIES:
        for orchestration in orchestrations:
            _finish(
                orchestration,
                status=ChangeOrchestration.Status.COMPLETED,
                stage=ChangeOrchestration.Stage.COMPLETED,
                payload={"outcome": "review_complete_without_automatic_summary", **state},
            )
        return state
    from aria.comparisons.tasks import summarize_comparison

    for orchestration in orchestrations:
        orchestration.status = ChangeOrchestration.Status.SUMMARY_PENDING
        orchestration.current_stage = ChangeOrchestration.Stage.SUMMARY
        orchestration.finished_at = None
        orchestration.save(update_fields=("status", "current_stage", "finished_at", "updated_at"))
    if orchestrations:
        transaction.on_commit(lambda: summarize_comparison.delay(str(comparison.id)))
    return state


@transaction.atomic
def complete_summary_orchestrations(summary: ComparisonSummary) -> int:
    orchestrations = list(
        ChangeOrchestration.objects.select_for_update().filter(
            comparison=summary.comparison,
            status__in=(
                ChangeOrchestration.Status.SUMMARY_PENDING,
                ChangeOrchestration.Status.SUMMARY_FAILED,
            ),
        )
    )
    for orchestration in orchestrations:
        orchestration.summary = summary
        orchestration.save(update_fields=("summary", "updated_at"))
        _record_step(
            orchestration,
            stage=ChangeOrchestration.Stage.SUMMARY,
            outcome=OrchestrationStepAttempt.Outcome.COMPLETED,
            input_payload={"comparison_id": str(summary.comparison_id)},
            started_at=summary.started_at or summary.created_at,
            output={
                "summary_id": str(summary.id),
                "input_hash": summary.input_hash,
                "model": summary.model,
            },
        )
        _finish(
            orchestration,
            status=ChangeOrchestration.Status.COMPLETED,
            stage=ChangeOrchestration.Stage.COMPLETED,
            payload={"summary_id": str(summary.id)},
        )
    return len(orchestrations)


@transaction.atomic
def mark_summary_orchestrations_failed(comparison, error: Exception) -> int:
    now = timezone.now()
    updated = ChangeOrchestration.objects.filter(
        comparison=comparison,
        status=ChangeOrchestration.Status.SUMMARY_PENDING,
    ).update(
        status=ChangeOrchestration.Status.SUMMARY_FAILED,
        error_code=type(error).__name__,
        error_message=str(error)[:4000],
        finished_at=now,
        updated_at=now,
    )
    return updated


@transaction.atomic
def prepare_orchestration_retry(orchestration: ChangeOrchestration) -> ChangeOrchestration:
    locked = ChangeOrchestration.objects.select_for_update().get(pk=orchestration.pk)
    if locked.status not in (
        ChangeOrchestration.Status.FAILED,
        ChangeOrchestration.Status.SUMMARY_FAILED,
        ChangeOrchestration.Status.WAITING_OCR,
    ):
        raise ValueError(f"Orchestration in status '{locked.status}' is not retryable.")
    waiting_for_ocr = locked.status == ChangeOrchestration.Status.WAITING_OCR
    evidence_ready = VersionEvidence.objects.filter(
        artifact_observation=locked.artifact_observation
    ).exists()
    if waiting_for_ocr and not evidence_ready:
        raise ValueError("OCR-derived version evidence is not ready yet.")
    locked.status = (
        ChangeOrchestration.Status.SUMMARY_PENDING
        if locked.status == ChangeOrchestration.Status.SUMMARY_FAILED
        else ChangeOrchestration.Status.PENDING
    )
    locked.retry_count += 1
    locked.finished_at = None
    locked.error_code = ""
    locked.error_message = ""
    locked.save(
        update_fields=(
            "status",
            "retry_count",
            "finished_at",
            "error_code",
            "error_message",
            "updated_at",
        )
    )
    return locked


@transaction.atomic
def resume_waiting_orchestrations_for_artifact(raw_artifact) -> int:
    source_artifact_ids = list(
        raw_artifact.derivations_as_output.filter(
            transformation_type=ArtifactDerivative.TransformationType.OCR_SEARCHABLE_PDF
        ).values_list("source_artifact_id", flat=True)
    )
    if not source_artifact_ids:
        return 0
    orchestrations = list(
        ChangeOrchestration.objects.select_for_update().filter(
            source_artifact_id__in=source_artifact_ids,
            status=ChangeOrchestration.Status.WAITING_OCR,
            artifact_observation__document_version_evidence__isnull=False,
        )
    )
    from aria.orchestration.tasks import process_change_orchestration

    for orchestration in orchestrations:
        orchestration.status = ChangeOrchestration.Status.PENDING
        orchestration.retry_count += 1
        orchestration.finished_at = None
        orchestration.save(update_fields=("status", "retry_count", "finished_at", "updated_at"))
        transaction.on_commit(
            lambda orchestration_id=str(orchestration.id): (
                process_change_orchestration.delay(orchestration_id)
            )
        )
    return len(orchestrations)


@transaction.atomic
def claim_recoverable_orchestrations(limit: int | None = None) -> list[ChangeOrchestration]:
    maximum = limit or settings.ORCHESTRATION_RECOVERY_BATCH_SIZE
    stale_before = timezone.now() - timedelta(minutes=settings.ORCHESTRATION_STALE_AFTER_MINUTES)
    candidates = list(
        ChangeOrchestration.objects.select_for_update(skip_locked=True)
        .filter(status=ChangeOrchestration.Status.PENDING)
        .order_by("created_at")[:maximum]
    )
    now = timezone.now()
    for orchestration in candidates:
        orchestration.status = ChangeOrchestration.Status.QUEUED
        orchestration.heartbeat_at = now
        orchestration.save(update_fields=("status", "heartbeat_at", "updated_at"))
    remaining = maximum - len(candidates)
    if remaining:
        ocr_ready = list(
            ChangeOrchestration.objects.select_for_update(skip_locked=True)
            .filter(
                status=ChangeOrchestration.Status.WAITING_OCR,
                artifact_observation__document_version_evidence__isnull=False,
            )
            .order_by("updated_at")[:remaining]
        )
        for orchestration in ocr_ready:
            orchestration.status = ChangeOrchestration.Status.QUEUED
            orchestration.heartbeat_at = now
            orchestration.retry_count += 1
            orchestration.finished_at = None
            orchestration.save(
                update_fields=(
                    "status",
                    "heartbeat_at",
                    "retry_count",
                    "finished_at",
                    "updated_at",
                )
            )
        candidates.extend(ocr_ready)
        remaining -= len(ocr_ready)
    if remaining:
        stale = list(
            ChangeOrchestration.objects.select_for_update(skip_locked=True)
            .filter(
                status__in=(
                    ChangeOrchestration.Status.QUEUED,
                    ChangeOrchestration.Status.RUNNING,
                ),
                heartbeat_at__lt=stale_before,
            )
            .order_by("heartbeat_at")[:remaining]
        )
        for orchestration in stale:
            orchestration.status = ChangeOrchestration.Status.QUEUED
            orchestration.heartbeat_at = now
            orchestration.retry_count += 1
            orchestration.error_code = "stale_orchestration_recovered"
            orchestration.error_message = "The last worker heartbeat exceeded the stale limit."
            orchestration.save(
                update_fields=(
                    "status",
                    "heartbeat_at",
                    "retry_count",
                    "error_code",
                    "error_message",
                    "updated_at",
                )
            )
        candidates.extend(stale)
    return candidates
