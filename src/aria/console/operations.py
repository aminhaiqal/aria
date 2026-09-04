from dataclasses import asdict

from django.db import transaction

from aria.browser.admission import assess_browser_admission, promote_admitted_source
from aria.browser.models import SourceAdmissionAssessment, SourceAdmissionPromotion
from aria.comparisons.models import ComparisonSummary, DocumentComparison
from aria.comparisons.publications import publish_confirmed_comparison_changes
from aria.comparisons.tasks import summarize_comparison
from aria.discovery.models import MonitoredResource, ResourceRun, SourceRun
from aria.discovery.services import (
    create_resource_run,
    create_source_run,
    retire_monitored_resource,
)
from aria.discovery.tasks import execute_resource_run, execute_source_run
from aria.events.services import record_audit_event
from aria.orchestration.models import ChangeOrchestration
from aria.orchestration.services import comparison_review_state, prepare_orchestration_retry
from aria.orchestration.tasks import process_change_orchestration
from aria.sources.admission import assess_static_admission, promote_static_source
from aria.sources.models import SourceEndpoint
from aria.sources.pilots import SourcePilotError, queue_disabled_source_pilot


class ConsoleOperationError(RuntimeError):
    pass


def _actor(user) -> str:
    return str(user.pk)


@transaction.atomic
def queue_endpoint_poll(endpoint: SourceEndpoint, *, user) -> SourceRun:
    endpoint = SourceEndpoint.objects.select_for_update().get(pk=endpoint.pk)
    if not endpoint.is_enabled:
        raise ConsoleOperationError("This source endpoint is disabled.")
    overlap = SourceRun.objects.filter(
        endpoint=endpoint,
        status__in=(SourceRun.Status.PENDING, SourceRun.Status.RUNNING),
        resource_run__isnull=True,
    ).exists()
    if overlap:
        raise ConsoleOperationError("This source endpoint already has active work.")
    source_run, _ = create_source_run(endpoint, trigger=SourceRun.Trigger.MANUAL)
    record_audit_event(
        action="console.source_poll_queued",
        target_type="source_endpoint",
        target_id=endpoint.id,
        actor_type="user",
        actor_identifier=_actor(user),
        details={"source_run_id": str(source_run.id)},
    )
    transaction.on_commit(lambda: execute_source_run.delay(str(source_run.id)))
    return source_run


@transaction.atomic
def queue_admission_capture(endpoint: SourceEndpoint, *, user) -> SourceRun:
    endpoint = SourceEndpoint.objects.select_for_update().get(pk=endpoint.pk)
    if endpoint.connector_type != SourceEndpoint.ConnectorType.JAVASCRIPT_LISTING:
        raise ConsoleOperationError("Admission captures require a JavaScript listing source.")
    if endpoint.is_enabled:
        raise ConsoleOperationError("Use the normal source poll for an enabled endpoint.")
    if not endpoint.source_pack_snapshots.exists():
        raise ConsoleOperationError("Install a validated source pack before capturing this pilot.")
    overlap = SourceRun.objects.filter(
        endpoint=endpoint,
        status__in=(SourceRun.Status.PENDING, SourceRun.Status.RUNNING),
        resource_run__isnull=True,
    ).exists()
    if overlap:
        raise ConsoleOperationError("This pilot already has an active capture.")
    source_run, _ = create_source_run(endpoint, trigger=SourceRun.Trigger.MANUAL)
    record_audit_event(
        action="console.admission_capture_queued",
        target_type="source_endpoint",
        target_id=endpoint.id,
        actor_type="user",
        actor_identifier=_actor(user),
        details={"source_run_id": str(source_run.id)},
    )
    transaction.on_commit(lambda: execute_source_run.delay(str(source_run.id)))
    return source_run


@transaction.atomic
def record_admission_assessment(
    endpoint: SourceEndpoint,
    *,
    required_captures: int,
    user,
) -> tuple[SourceAdmissionAssessment, bool]:
    endpoint = SourceEndpoint.objects.select_for_update().get(pk=endpoint.pk)
    if endpoint.connector_type != SourceEndpoint.ConnectorType.JAVASCRIPT_LISTING:
        raise ConsoleOperationError("Admission assessment requires a JavaScript listing source.")
    try:
        assessment, created = assess_browser_admission(
            endpoint,
            required_captures=required_captures,
        )
    except ValueError as error:
        raise ConsoleOperationError(str(error)) from error
    record_audit_event(
        action="console.admission_assessed",
        target_type="source_endpoint",
        target_id=endpoint.id,
        actor_type="user",
        actor_identifier=_actor(user),
        details={
            "assessment_id": str(assessment.id),
            "report_signature": assessment.report_signature,
            "created": created,
        },
    )
    return assessment, created


@transaction.atomic
def promote_source_admission(
    endpoint: SourceEndpoint,
    assessment: SourceAdmissionAssessment,
    *,
    user,
) -> SourceAdmissionPromotion:
    try:
        return promote_admitted_source(
            endpoint,
            assessment,
            actor_identifier=_actor(user),
        )
    except ValueError as error:
        raise ConsoleOperationError(str(error)) from error


def queue_static_source_pilot(endpoint: SourceEndpoint, *, user) -> SourceRun:
    if endpoint.connector_type != SourceEndpoint.ConnectorType.HTML_LISTING:
        raise ConsoleOperationError("Static pilots require an HTML listing source.")
    if endpoint.requires_javascript:
        raise ConsoleOperationError("JavaScript sources require a browser admission capture.")
    try:
        return queue_disabled_source_pilot(
            endpoint,
            actor_type="user",
            actor_identifier=_actor(user),
        )
    except SourcePilotError as error:
        raise ConsoleOperationError(str(error)) from error


@transaction.atomic
def record_static_admission_assessment(
    endpoint: SourceEndpoint,
    *,
    required_runs: int,
    user,
) -> tuple[SourceAdmissionAssessment, bool]:
    endpoint = SourceEndpoint.objects.select_for_update().get(pk=endpoint.pk)
    try:
        assessment, created = assess_static_admission(endpoint, required_runs=required_runs)
    except ValueError as error:
        raise ConsoleOperationError(str(error)) from error
    record_audit_event(
        action="console.static_admission_assessed",
        target_type="source_endpoint",
        target_id=endpoint.id,
        actor_type="user",
        actor_identifier=_actor(user),
        details={
            "assessment_id": str(assessment.id),
            "report_signature": assessment.report_signature,
            "created": created,
        },
    )
    return assessment, created


def promote_static_source_admission(
    endpoint: SourceEndpoint,
    assessment: SourceAdmissionAssessment,
    *,
    user,
) -> SourceAdmissionPromotion:
    try:
        return promote_static_source(
            endpoint,
            assessment,
            actor_type="user",
            actor_identifier=_actor(user),
        )
    except ValueError as error:
        raise ConsoleOperationError(str(error)) from error


@transaction.atomic
def queue_resource_poll(resource: MonitoredResource, *, user) -> ResourceRun:
    resource = (
        MonitoredResource.objects.select_for_update().select_related("endpoint").get(pk=resource.pk)
    )
    if resource.retired_at is not None:
        raise ConsoleOperationError("This resource is retired and cannot be polled.")
    if not resource.is_enabled or not resource.is_approved or not resource.endpoint.is_enabled:
        raise ConsoleOperationError("This resource is not enabled and explicitly approved.")
    overlap = resource.runs.filter(
        status__in=(ResourceRun.Status.PENDING, ResourceRun.Status.RUNNING)
    ).exists()
    if overlap:
        raise ConsoleOperationError("This resource already has active work.")
    resource_run, _ = create_resource_run(resource, trigger=SourceRun.Trigger.MANUAL)
    record_audit_event(
        action="console.resource_poll_queued",
        target_type="monitored_resource",
        target_id=resource.id,
        actor_type="user",
        actor_identifier=_actor(user),
        details={
            "resource_run_id": str(resource_run.id),
            "source_run_id": str(resource_run.source_run_id),
        },
    )
    transaction.on_commit(lambda: execute_resource_run.delay(str(resource_run.id)))
    return resource_run


def retire_resource(
    resource: MonitoredResource, *, reason: str, user
) -> tuple[MonitoredResource, bool]:
    try:
        return retire_monitored_resource(
            resource,
            reason=reason,
            actor_type="user",
            actor_identifier=_actor(user),
        )
    except ValueError as error:
        raise ConsoleOperationError(str(error)) from error


@transaction.atomic
def queue_orchestration_retry(orchestration: ChangeOrchestration, *, user):
    orchestration = ChangeOrchestration.objects.select_for_update().get(pk=orchestration.pk)
    previous_status = orchestration.status
    try:
        orchestration = prepare_orchestration_retry(orchestration)
    except ValueError as error:
        raise ConsoleOperationError(str(error)) from error
    record_audit_event(
        action="console.orchestration_retry_queued",
        target_type="change_orchestration",
        target_id=orchestration.id,
        actor_type="user",
        actor_identifier=_actor(user),
        details={
            "previous_status": previous_status,
            "retry_count": orchestration.retry_count,
        },
    )
    if previous_status == ChangeOrchestration.Status.SUMMARY_FAILED:
        if orchestration.comparison_id is None:
            raise ConsoleOperationError("The failed summary has no comparison.")
        transaction.on_commit(lambda: summarize_comparison.delay(str(orchestration.comparison_id)))
    else:
        transaction.on_commit(lambda: process_change_orchestration.delay(str(orchestration.id)))
    return orchestration


@transaction.atomic
def queue_comparison_summary(comparison: DocumentComparison, *, user) -> dict:
    comparison = DocumentComparison.objects.select_for_update().get(pk=comparison.pk)
    state = comparison_review_state(comparison)
    if not state["complete"] or not state["confirmed_count"]:
        raise ConsoleOperationError(
            "Every change needs a current decision and at least one must be confirmed."
        )
    if comparison.summaries.filter(
        status__in=(ComparisonSummary.Status.PENDING, ComparisonSummary.Status.RUNNING)
    ).exists():
        raise ConsoleOperationError("A summary is already pending or running.")
    if comparison.change_orchestrations.filter(
        status=ChangeOrchestration.Status.SUMMARY_PENDING
    ).exists():
        raise ConsoleOperationError("A summary is already queued by the change workflow.")
    record_audit_event(
        action="console.comparison_summary_queued",
        target_type="document_comparison",
        target_id=comparison.id,
        actor_type="user",
        actor_identifier=_actor(user),
        details=state,
    )
    transaction.on_commit(lambda: summarize_comparison.delay(str(comparison.id)))
    return state


@transaction.atomic
def publish_reviewed_comparison(comparison: DocumentComparison, *, user):
    comparison = DocumentComparison.objects.select_for_update().get(pk=comparison.pk)
    state = comparison_review_state(comparison)
    if not state["complete"] or not state["confirmed_count"]:
        raise ConsoleOperationError(
            "Publication requires complete review and at least one confirmed change."
        )
    result = publish_confirmed_comparison_changes(comparison)
    record_audit_event(
        action="console.reviewed_changes_published",
        target_type="document_comparison",
        target_id=comparison.id,
        actor_type="user",
        actor_identifier=_actor(user),
        details={**state, **asdict(result)},
    )
    return result
