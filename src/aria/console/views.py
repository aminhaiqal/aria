import json
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from aria.browser.models import (
    BrowserCapture,
    BrowserNetworkExchange,
    SourceAdmissionAssessment,
)
from aria.comparisons.models import (
    ComparisonItem,
    ComparisonReview,
    ComparisonSummary,
    DocumentComparison,
    ReviewedChangePublication,
)
from aria.comparisons.reviews import record_comparison_review
from aria.console.forms import (
    AdmissionAssessmentForm,
    AdmissionPromotionForm,
    ComparisonReviewForm,
    ImpactReviewForm,
    PublicationConfirmationForm,
    ResourceRetirementForm,
    StaticAdmissionAssessmentForm,
)
from aria.console.operations import (
    ConsoleOperationError,
    promote_source_admission,
    promote_static_source_admission,
    publish_reviewed_comparison,
    publish_reviewed_impact,
    queue_admission_capture,
    queue_comparison_summary,
    queue_endpoint_poll,
    queue_orchestration_retry,
    queue_resource_poll,
    queue_static_source_pilot,
    record_admission_assessment,
    record_impact_decision,
    record_static_admission_assessment,
    retire_resource,
)
from aria.discovery.models import MonitoredResource, ResourceRun, SourceRun
from aria.events.models import AuditEvent, OutboxEvent, PipelineEvent
from aria.impacts.models import ImpactReview, RegulatoryImpact
from aria.orchestration.models import ChangeOrchestration
from aria.orchestration.services import comparison_review_state
from aria.quality.models import DocumentQualityAssessment
from aria.reliability.confidence import collect_source_confidence_report
from aria.reliability.models import SourceReliabilityAssessment
from aria.reliability.repair import build_source_repair_plan
from aria.reliability.soak import collect_autonomous_cycle_acceptance
from aria.sources.models import SourceEndpoint

staff_required = user_passes_test(
    lambda user: user.is_active and user.is_staff,
    login_url="console:login",
)

ASSETS = {
    "console.css": ("text/css; charset=utf-8", "console.css"),
    "console.js": ("text/javascript; charset=utf-8", "console.js"),
}


class StaffLoginView(LoginView):
    template_name = "console/login.html"
    redirect_authenticated_user = False

    def form_valid(self, form):
        user = form.get_user()
        if not user.is_active or not user.is_staff:
            form.add_error(None, "This console requires an active staff account.")
            return self.form_invalid(form)
        return super().form_valid(form)


@require_GET
def console_asset(request, asset_name):
    asset = ASSETS.get(asset_name)
    if asset is None:
        raise Http404
    content_type, filename = asset
    path = Path(__file__).with_name("static") / filename
    response = FileResponse(path.open("rb"), content_type=content_type)
    response["Cache-Control"] = "public, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _page(request, queryset, *, page_size=25):
    page = Paginator(queryset, page_size).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return page, urlencode(query, doseq=True)


def _base_context(*, title, section, eyebrow="Operations"):
    return {"page_title": title, "active_section": section, "eyebrow": eyebrow}


@staff_required
def dashboard(request):
    active_work_statuses = (
        ChangeOrchestration.Status.PENDING,
        ChangeOrchestration.Status.QUEUED,
        ChangeOrchestration.Status.RUNNING,
        ChangeOrchestration.Status.WAITING_OCR,
        ChangeOrchestration.Status.SUMMARY_PENDING,
    )
    active_source_statuses = (SourceRun.Status.PENDING, SourceRun.Status.RUNNING)
    latest_reliability = list(
        SourceReliabilityAssessment.objects.select_related("endpoint")
        .order_by("endpoint_id", "-assessed_at", "-id")
        .distinct("endpoint_id")
    )
    reliability_alerts = sum(
        assessment.status
        in {
            SourceReliabilityAssessment.Status.WARNING,
            SourceReliabilityAssessment.Status.CRITICAL,
        }
        for assessment in latest_reliability
    )
    context = {
        **_base_context(title="Operational overview", section="dashboard"),
        "healthy_sources": SourceEndpoint.objects.filter(
            is_enabled=True,
            health_state=SourceEndpoint.HealthState.HEALTHY,
        ).count(),
        "enabled_sources": SourceEndpoint.objects.filter(is_enabled=True).count(),
        "unhealthy_resources": MonitoredResource.objects.filter(
            is_enabled=True,
            health_state__in=(
                MonitoredResource.HealthState.DEGRADED,
                MonitoredResource.HealthState.UNHEALTHY,
            ),
        ).count(),
        "reliability_alerts": reliability_alerts,
        "active_workflows": (
            ChangeOrchestration.objects.filter(status__in=active_work_statuses).count()
            + SourceRun.objects.filter(status__in=active_source_statuses).count()
        ),
        "review_queue": ChangeOrchestration.objects.filter(
            status=ChangeOrchestration.Status.REVIEW_REQUIRED
        ).count(),
        "failed_workflows": ChangeOrchestration.objects.filter(
            status__in=(
                ChangeOrchestration.Status.FAILED,
                ChangeOrchestration.Status.SUMMARY_FAILED,
            )
        ).count(),
        "failed_outbox": OutboxEvent.objects.filter(status=OutboxEvent.Status.FAILED).count(),
        "recent_workflows": ChangeOrchestration.objects.select_related(
            "artifact_observation",
            "source_artifact",
            "comparison__identity",
        )[:8],
        "recent_source_runs": SourceRun.objects.select_related("endpoint")[:8],
        "recent_events": PipelineEvent.objects.all()[:8],
        "recent_reliability": sorted(
            latest_reliability,
            key=lambda assessment: assessment.assessed_at,
            reverse=True,
        )[:6],
    }
    return render(request, "console/dashboard.html", context)


@staff_required
def source_list(request):
    health = request.GET.get("health", "")
    query = request.GET.get("q", "").strip()
    latest_reliability = SourceReliabilityAssessment.objects.filter(
        endpoint_id=OuterRef("pk")
    ).order_by("-assessed_at", "-id")
    endpoints = (
        SourceEndpoint.objects.select_related(
            "collection",
            "collection__authority",
        )
        .annotate(
            reliability_status=Subquery(latest_reliability.values("status")[:1]),
            reliability_assessed_at=Subquery(latest_reliability.values("assessed_at")[:1]),
            resource_count=Count("monitored_resources", distinct=True),
            active_run_count=Count(
                "source_runs",
                filter=Q(
                    source_runs__status__in=(SourceRun.Status.PENDING, SourceRun.Status.RUNNING)
                ),
                distinct=True,
            ),
        )
        .order_by("collection__authority__name", "collection__name", "name")
    )
    if health in SourceEndpoint.HealthState.values:
        endpoints = endpoints.filter(health_state=health)
    if query:
        endpoints = endpoints.filter(
            Q(name__icontains=query)
            | Q(collection__name__icontains=query)
            | Q(collection__authority__name__icontains=query)
            | Q(discovery_url__icontains=query)
        )
    page, page_query = _page(request, endpoints)
    return render(
        request,
        "console/source_list.html",
        {
            **_base_context(title="Official sources", section="sources"),
            "page": page,
            "page_query": page_query,
            "selected_health": health,
            "query": query,
            "health_choices": SourceEndpoint.HealthState.choices,
        },
    )


@staff_required
def source_confidence(request):
    return render(
        request,
        "console/source_confidence.html",
        {
            **_base_context(
                title="Source confidence",
                section="confidence",
                eyebrow="Evidence coverage",
            ),
            "rows": collect_source_confidence_report(),
            "soak": collect_autonomous_cycle_acceptance(),
        },
    )


@staff_required
def admission_list(request):
    endpoints = SourceEndpoint.objects.filter(
        connector_type=SourceEndpoint.ConnectorType.JAVASCRIPT_LISTING
    ).select_related("collection", "collection__authority")
    rows = []
    for endpoint in endpoints:
        rows.append(
            {
                "endpoint": endpoint,
                "snapshot": endpoint.source_pack_snapshots.first(),
                "assessment": endpoint.admission_assessments.first(),
                "promotion": endpoint.admission_promotions.first(),
                "capture_count": endpoint.browser_captures.count(),
            }
        )
    return render(
        request,
        "console/admission_list.html",
        {
            **_base_context(
                title="Source admissions",
                section="admissions",
                eyebrow="Controlled onboarding",
            ),
            "rows": rows,
        },
    )


@staff_required
def admission_detail(request, endpoint_id):
    endpoint = get_object_or_404(
        SourceEndpoint.objects.select_related("collection", "collection__authority"),
        pk=endpoint_id,
        connector_type=SourceEndpoint.ConnectorType.JAVASCRIPT_LISTING,
    )
    snapshot = endpoint.source_pack_snapshots.first()
    assessments = endpoint.admission_assessments.select_related("source_pack_snapshot")[:10]
    latest_assessment = assessments[0] if assessments else None
    captures = endpoint.browser_captures.select_related(
        "source_run", "original_artifact", "rendered_artifact", "rendered_derivative"
    ).prefetch_related("network_exchanges")[:10]
    latest_capture = captures[0] if captures else None
    candidate_urls = []
    if latest_capture:
        candidate_urls = list(
            latest_capture.source_run.candidate_observations.values_list(
                "candidate__canonical_url", flat=True
            ).order_by("candidate__canonical_url")
        )
    network_exchanges = (
        BrowserNetworkExchange.objects.filter(capture__endpoint=endpoint)
        .select_related("capture")
        .order_by("-occurred_at", "-id")[:30]
    )
    active_capture = endpoint.source_runs.filter(
        status__in=(SourceRun.Status.PENDING, SourceRun.Status.RUNNING),
        resource_run__isnull=True,
    ).first()
    repair_plan = build_source_repair_plan(endpoint)
    promotion_form = AdmissionPromotionForm(
        initial={"assessment_id": latest_assessment.id if latest_assessment else None}
    )
    return render(
        request,
        "console/admission_detail.html",
        {
            **_base_context(
                title=endpoint.name,
                section="admissions",
                eyebrow="Admission workbench",
            ),
            "endpoint": endpoint,
            "snapshot": snapshot,
            "assessments": assessments,
            "latest_assessment": latest_assessment,
            "captures": captures,
            "candidate_urls": candidate_urls,
            "network_exchanges": network_exchanges,
            "active_capture": active_capture,
            "repair_plan": repair_plan,
            "assessment_form": AdmissionAssessmentForm(initial={"required_captures": 2}),
            "promotion_form": promotion_form,
        },
    )


@staff_required
@require_POST
def admission_capture(request, endpoint_id):
    endpoint = get_object_or_404(SourceEndpoint, pk=endpoint_id)
    try:
        run = queue_admission_capture(endpoint, user=request.user)
    except ConsoleOperationError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Bounded admission capture queued as run {run.id}.")
    return redirect("console:admission-detail", endpoint_id=endpoint.id)


@staff_required
@require_POST
def admission_assess(request, endpoint_id):
    endpoint = get_object_or_404(SourceEndpoint, pk=endpoint_id)
    form = AdmissionAssessmentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a valid capture requirement.")
    else:
        try:
            assessment, created = record_admission_assessment(
                endpoint,
                required_captures=form.cleaned_data["required_captures"],
                user=request.user,
            )
        except ConsoleOperationError as error:
            messages.error(request, str(error))
        else:
            action = "recorded" if created else "unchanged"
            messages.success(request, f"Admission assessment {action}: {assessment.status}.")
    return redirect("console:admission-detail", endpoint_id=endpoint.id)


@staff_required
@require_POST
def admission_promote(request, endpoint_id):
    endpoint = get_object_or_404(SourceEndpoint, pk=endpoint_id)
    form = AdmissionPromotionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Promotion requires the exact PROMOTE confirmation.")
    else:
        assessment = get_object_or_404(
            SourceAdmissionAssessment,
            pk=form.cleaned_data["assessment_id"],
            endpoint=endpoint,
        )
        try:
            promotion = promote_source_admission(endpoint, assessment, user=request.user)
        except ConsoleOperationError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request,
                f"Source promoted; next bounded poll is {promotion.next_poll_at}.",
            )
    return redirect("console:admission-detail", endpoint_id=endpoint.id)


@staff_required
def source_detail(request, endpoint_id):
    endpoint = get_object_or_404(
        SourceEndpoint.objects.select_related("collection", "collection__authority"),
        pk=endpoint_id,
    )
    resources = endpoint.monitored_resources.select_related("parent").order_by(
        "resource_type", "title", "url"
    )
    endpoint_poll_blocked = endpoint.source_runs.filter(
        status__in=(SourceRun.Status.PENDING, SourceRun.Status.RUNNING),
        resource_run__isnull=True,
    ).exists()
    runs = endpoint.source_runs.select_related("resource_run")[:20]
    observations = endpoint.endpoint_observations.select_related("source_run")[:10]
    browser_captures = BrowserCapture.objects.filter(endpoint=endpoint).select_related(
        "source_run",
        "original_artifact",
        "rendered_artifact",
    )[:10]
    reliability_assessments = endpoint.reliability_assessments.select_related(
        "source_run", "previous_assessment"
    )[:10]
    latest_reliability = reliability_assessments[0] if reliability_assessments else None
    source_pack_snapshot = endpoint.source_pack_snapshots.first()
    latest_static_assessment = (
        endpoint.admission_assessments.filter(
            admission_profile=SourceAdmissionAssessment.Profile.STATIC_LISTING
        )
        .select_related("source_pack_snapshot")
        .first()
    )
    return render(
        request,
        "console/source_detail.html",
        {
            **_base_context(title=endpoint.name, section="sources", eyebrow="Official source"),
            "endpoint": endpoint,
            "resources": resources,
            "runs": runs,
            "observations": observations,
            "browser_captures": browser_captures,
            "latest_reliability": latest_reliability,
            "reliability_assessments": reliability_assessments,
            "endpoint_poll_blocked": endpoint_poll_blocked,
            "source_pack_snapshot": source_pack_snapshot,
            "latest_static_assessment": latest_static_assessment,
            "static_assessment_form": StaticAdmissionAssessmentForm(initial={"required_runs": 2}),
            "static_promotion_form": AdmissionPromotionForm(
                initial={
                    "assessment_id": (
                        latest_static_assessment.id if latest_static_assessment else None
                    )
                }
            ),
        },
    )


@staff_required
@require_POST
def source_poll(request, endpoint_id):
    endpoint = get_object_or_404(SourceEndpoint, pk=endpoint_id)
    try:
        run = queue_endpoint_poll(endpoint, user=request.user)
    except ConsoleOperationError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Source check queued as run {run.id}.")
    return redirect("console:source-detail", endpoint_id=endpoint.id)


@staff_required
@require_POST
def static_source_pilot(request, endpoint_id):
    endpoint = get_object_or_404(SourceEndpoint, pk=endpoint_id)
    try:
        run = queue_static_source_pilot(endpoint, user=request.user)
    except ConsoleOperationError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Disabled source pilot queued as run {run.id}.")
    return redirect("console:source-detail", endpoint_id=endpoint.id)


@staff_required
@require_POST
def static_source_assess(request, endpoint_id):
    endpoint = get_object_or_404(SourceEndpoint, pk=endpoint_id)
    form = StaticAdmissionAssessmentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a valid run requirement.")
    else:
        try:
            assessment, created = record_static_admission_assessment(
                endpoint,
                required_runs=form.cleaned_data["required_runs"],
                user=request.user,
            )
        except ConsoleOperationError as error:
            messages.error(request, str(error))
        else:
            action = "recorded" if created else "unchanged"
            messages.success(request, f"Static admission {action}: {assessment.status}.")
    return redirect("console:source-detail", endpoint_id=endpoint.id)


@staff_required
@require_POST
def static_source_promote(request, endpoint_id):
    endpoint = get_object_or_404(SourceEndpoint, pk=endpoint_id)
    form = AdmissionPromotionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Promotion requires the exact PROMOTE confirmation.")
    else:
        assessment = get_object_or_404(
            SourceAdmissionAssessment,
            pk=form.cleaned_data["assessment_id"],
            endpoint=endpoint,
            admission_profile=SourceAdmissionAssessment.Profile.STATIC_LISTING,
        )
        try:
            promotion = promote_static_source_admission(
                endpoint,
                assessment,
                user=request.user,
            )
        except ConsoleOperationError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request,
                f"Static source promoted; next bounded poll is {promotion.next_poll_at}.",
            )
    return redirect("console:source-detail", endpoint_id=endpoint.id)


@staff_required
def resource_detail(request, resource_id):
    resource = get_object_or_404(
        MonitoredResource.objects.select_related(
            "endpoint", "endpoint__collection", "endpoint__collection__authority", "parent"
        ),
        pk=resource_id,
    )
    runs = resource.runs.select_related("source_run")[:20]
    observations = resource.observations.prefetch_related("link_observations")[:10]
    structure_incidents = resource.structure_incidents.select_related("resource_run")[:10]
    latest_observation = observations[0] if observations else None
    active_run = resource.runs.filter(
        status__in=(ResourceRun.Status.PENDING, ResourceRun.Status.RUNNING)
    ).first()
    return render(
        request,
        "console/resource_detail.html",
        {
            **_base_context(
                title=resource.title or resource.get_resource_type_display(),
                section="sources",
                eyebrow="Monitored resource",
            ),
            "resource": resource,
            "runs": runs,
            "observations": observations,
            "structure_incidents": structure_incidents,
            "latest_observation": latest_observation,
            "active_run": active_run,
            "retirement_form": ResourceRetirementForm(),
        },
    )


@staff_required
@require_POST
def resource_poll(request, resource_id):
    resource = get_object_or_404(MonitoredResource, pk=resource_id)
    try:
        run = queue_resource_poll(resource, user=request.user)
    except ConsoleOperationError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Resource check queued as run {run.id}.")
    return redirect("console:resource-detail", resource_id=resource.id)


@staff_required
@require_POST
def resource_retire(request, resource_id):
    resource = get_object_or_404(MonitoredResource, pk=resource_id)
    form = ResourceRetirementForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Retirement requires a reason and the exact RETIRE confirmation.")
    else:
        try:
            _, created = retire_resource(
                resource,
                reason=form.cleaned_data["reason"],
                user=request.user,
            )
        except ConsoleOperationError as error:
            messages.error(request, str(error))
        else:
            if created:
                messages.success(request, "Resource retired; its evidence history was preserved.")
            else:
                messages.info(
                    request,
                    "This resource was already retired; no duplicate event was created.",
                )
    return redirect("console:resource-detail", resource_id=resource.id)


@staff_required
def orchestration_list(request):
    status = request.GET.get("status", "")
    query = request.GET.get("q", "").strip()
    workflows = ChangeOrchestration.objects.select_related(
        "artifact_observation",
        "source_artifact",
        "document_version__identity",
        "comparison__identity",
    )
    if status in ChangeOrchestration.Status.values:
        workflows = workflows.filter(status=status)
    if query:
        filters = (
            Q(source_artifact__sha256__icontains=query)
            | Q(artifact_observation__final_url__icontains=query)
            | Q(error_code__icontains=query)
            | Q(document_version__identity__canonical_title__icontains=query)
        )
        try:
            filters |= Q(id=UUID(query))
        except ValueError:
            pass
        workflows = workflows.filter(filters)
    page, page_query = _page(request, workflows)
    return render(
        request,
        "console/orchestration_list.html",
        {
            **_base_context(title="Change workflows", section="workflows"),
            "page": page,
            "page_query": page_query,
            "selected_status": status,
            "query": query,
            "status_choices": ChangeOrchestration.Status.choices,
        },
    )


@staff_required
def orchestration_detail(request, orchestration_id):
    workflow = get_object_or_404(
        ChangeOrchestration.objects.select_related(
            "artifact_observation__candidate__endpoint",
            "source_artifact",
            "extraction_run",
            "document_version__identity",
            "quality_run",
            "lineage_assessment",
            "comparison__identity",
            "summary",
        ).prefetch_related("step_attempts"),
        pk=orchestration_id,
    )
    quality_assessment = None
    if workflow.quality_run_id and workflow.document_version_id:
        quality_assessment = (
            DocumentQualityAssessment.objects.filter(
                quality_run=workflow.quality_run,
                document_version=workflow.document_version,
            )
            .prefetch_related("findings")
            .first()
        )
    retryable = workflow.status in (
        ChangeOrchestration.Status.FAILED,
        ChangeOrchestration.Status.SUMMARY_FAILED,
    )
    if workflow.status == ChangeOrchestration.Status.WAITING_OCR:
        retryable = hasattr(workflow.artifact_observation, "document_version_evidence")
    return render(
        request,
        "console/orchestration_detail.html",
        {
            **_base_context(
                title="Workflow trace",
                section="workflows",
                eyebrow="Changed artifact",
            ),
            "workflow": workflow,
            "quality_assessment": quality_assessment,
            "retryable": retryable,
        },
    )


@staff_required
@require_POST
def orchestration_retry(request, orchestration_id):
    workflow = get_object_or_404(ChangeOrchestration, pk=orchestration_id)
    try:
        queue_orchestration_retry(workflow, user=request.user)
    except ConsoleOperationError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Workflow retry queued.")
    return redirect("console:orchestration-detail", orchestration_id=workflow.id)


def _comparison_rows(comparisons):
    rows = []
    for comparison in comparisons:
        state = _console_review_state(comparison)
        rows.append(
            {
                "comparison": comparison,
                "review_state": state,
                "latest_summary": comparison.summaries.order_by("-created_at").first(),
            }
        )
    return rows


def _console_review_state(comparison):
    state = comparison_review_state(comparison)
    decisions = []
    for item in comparison.items.exclude(change_type=ComparisonItem.ChangeType.UNCHANGED):
        latest = item.reviews.order_by("-created_at", "-id").first()
        decisions.append(latest.decision if latest else None)
    return {
        **state,
        "rejected_count": decisions.count(ComparisonReview.Decision.REJECTED),
        "needs_context_count": decisions.count(ComparisonReview.Decision.NEEDS_CONTEXT),
    }


@staff_required
def comparison_list(request):
    review = request.GET.get("review", "")
    query = request.GET.get("q", "").strip()
    comparisons = DocumentComparison.objects.select_related(
        "identity", "before_version", "after_version"
    ).prefetch_related("items__reviews", "summaries")
    if query:
        comparisons = comparisons.filter(
            Q(identity__canonical_title__icontains=query)
            | Q(identity__canonical_url__icontains=query)
            | Q(comparison_track_key__icontains=query)
        )
    if review == "pending":
        comparisons = comparisons.filter(change_orchestrations__status="review_required")
    elif review == "summary_failed":
        comparisons = comparisons.filter(change_orchestrations__status="summary_failed")
    page, page_query = _page(request, comparisons.distinct())
    return render(
        request,
        "console/comparison_list.html",
        {
            **_base_context(title="Comparison review", section="comparisons"),
            "page": page,
            "page_query": page_query,
            "rows": _comparison_rows(page.object_list),
            "selected_review": review,
            "query": query,
        },
    )


@staff_required
def comparison_detail(request, comparison_id):
    comparison = get_object_or_404(
        DocumentComparison.objects.select_related(
            "identity",
            "identity__collection__authority",
            "before_version",
            "after_version",
        ).prefetch_related(
            "items__before_anchor__source_artifact",
            "items__after_anchor__source_artifact",
            "items__reviews__reviewer",
            "items__reviewed_publications",
            "summaries",
            "change_orchestrations",
        ),
        pk=comparison_id,
    )
    item_rows = []
    for item in comparison.items.all():
        current_review = item.reviews.order_by("-created_at", "-id").first()
        item_rows.append(
            {
                "item": item,
                "current_review": current_review,
                "review_form": ComparisonReviewForm(
                    auto_id=f"id_{item.id}_%s",
                    initial={
                        "decision": current_review.decision if current_review else "",
                        "rationale": current_review.rationale if current_review else "",
                    },
                ),
                "publication": item.reviewed_publications.order_by("-created_at").first(),
            }
        )
    review_state = _console_review_state(comparison)
    latest_summary = comparison.summaries.order_by("-created_at").first()
    summary_in_flight = comparison.change_orchestrations.filter(
        status=ChangeOrchestration.Status.SUMMARY_PENDING
    ).exists() or bool(
        latest_summary
        and latest_summary.status
        in (ComparisonSummary.Status.PENDING, ComparisonSummary.Status.RUNNING)
    )
    published_count = ReviewedChangePublication.objects.filter(
        comparison_item__comparison=comparison
    ).count()
    return render(
        request,
        "console/comparison_detail.html",
        {
            **_base_context(
                title=comparison.identity.canonical_title or "Untitled publication",
                section="comparisons",
                eyebrow="Evidence review",
            ),
            "comparison": comparison,
            "item_rows": item_rows,
            "review_state": review_state,
            "review_percent": round(
                review_state["reviewed_count"] * 100 / review_state["item_count"]
            )
            if review_state["item_count"]
            else 100,
            "latest_summary": latest_summary,
            "summary_enabled": bool(settings.OPENAI_API_KEY),
            "summary_in_flight": summary_in_flight,
            "published_count": published_count,
            "publication_form": PublicationConfirmationForm(),
        },
    )


@staff_required
@require_POST
def comparison_item_review(request, item_id):
    item = get_object_or_404(
        ComparisonItem.objects.select_related("comparison"),
        pk=item_id,
    )
    form = ComparisonReviewForm(request.POST)
    if item.change_type == ComparisonItem.ChangeType.UNCHANGED:
        messages.error(request, "Unchanged alignments do not accept review decisions.")
    elif not form.is_valid():
        messages.error(
            request,
            "Review was not recorded: " + json.dumps(form.errors.get_json_data()),
        )
    else:
        record_comparison_review(
            item,
            decision=form.cleaned_data["decision"],
            rationale=form.cleaned_data["rationale"],
            reviewer=request.user,
        )
        messages.success(request, "Append-only review decision recorded.")
    target = reverse("console:comparison-detail", args=[item.comparison_id])
    return HttpResponseRedirect(f"{target}#item-{item.id}")


@staff_required
@require_POST
def comparison_summarize(request, comparison_id):
    comparison = get_object_or_404(DocumentComparison, pk=comparison_id)
    try:
        queue_comparison_summary(comparison, user=request.user)
    except ConsoleOperationError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Structured GPT summary queued.")
    return redirect("console:comparison-detail", comparison_id=comparison.id)


@staff_required
@require_POST
def comparison_publish(request, comparison_id):
    comparison = get_object_or_404(DocumentComparison, pk=comparison_id)
    form = PublicationConfirmationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Publication not confirmed. Type PUBLISH exactly.")
    else:
        try:
            result = publish_reviewed_comparison(comparison, user=request.user)
        except ConsoleOperationError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request,
                f"Published {result.published_count} reviewed change(s); "
                f"{result.skipped_count} already existed.",
            )
    return redirect("console:comparison-detail", comparison_id=comparison.id)


@staff_required
def impact_list(request):
    state = request.GET.get("state", "")
    query = request.GET.get("q", "").strip()
    latest_decision = (
        ImpactReview.objects.filter(impact=OuterRef("pk"))
        .order_by("-created_at", "-id")
        .values("decision")[:1]
    )
    impacts = (
        RegulatoryImpact.objects.select_related(
            "comparison_item__comparison__identity__collection__authority",
            "confirmation_review",
            "generation__taxonomy",
        )
        .prefetch_related("targets__term", "reviews")
        .annotate(current_decision=Subquery(latest_decision))
    )
    if query:
        impacts = impacts.filter(
            Q(title__icontains=query)
            | Q(statement__icontains=query)
            | Q(comparison_item__comparison__identity__canonical_title__icontains=query)
            | Q(targets__term__label__icontains=query)
        )
    if state == "review_required":
        impacts = impacts.filter(current_decision__isnull=True)
    elif state in ImpactReview.Decision.values:
        impacts = impacts.filter(current_decision=state)
    page, page_query = _page(request, impacts.distinct())
    return render(
        request,
        "console/impact_list.html",
        {
            **_base_context(
                title="Business impact review",
                section="impacts",
                eyebrow="Evidence-bound applicability",
            ),
            "page": page,
            "page_query": page_query,
            "selected_state": state,
            "query": query,
            "decision_choices": ImpactReview.Decision.choices,
        },
    )


def _impact_review_initial(impact, current_review):
    if current_review:
        targets = current_review.reviewed_targets.all()
        title = current_review.reviewed_title
        statement = current_review.reviewed_statement
        effective_date = current_review.reviewed_effective_date_text
    else:
        targets = impact.targets.all()
        title = impact.title
        statement = impact.statement
        effective_date = impact.effective_date_text
    return {
        "decision": current_review.decision if current_review else "",
        "title": title,
        "statement": statement,
        "effective_date_text": effective_date,
        "included_terms": [
            target.term_id
            for target in targets
            if target.disposition == "included"
        ],
        "excluded_terms": [
            target.term_id
            for target in targets
            if target.disposition == "excluded"
        ],
        "rationale": current_review.rationale if current_review else "",
    }


@staff_required
def impact_detail(request, impact_id):
    impact = get_object_or_404(
        RegulatoryImpact.objects.select_related(
            "comparison_item__comparison__identity__collection__authority",
            "comparison_item__before_anchor__source_artifact",
            "comparison_item__after_anchor__source_artifact",
            "confirmation_review__reviewer",
            "generation__taxonomy",
        ).prefetch_related(
            "evidence_records__structural_anchor",
            "targets__term__taxonomy",
            "reviews__reviewer",
            "reviews__reviewed_targets__term",
        ),
        pk=impact_id,
    )
    current_review = impact.reviews.order_by("-created_at", "-id").first()
    latest_change_review = impact.comparison_item.reviews.order_by("-created_at", "-id").first()
    source_confirmation_current = bool(
        latest_change_review
        and latest_change_review.id == impact.confirmation_review_id
        and latest_change_review.decision == ComparisonReview.Decision.CONFIRMED
    )
    current_publication = (
        current_review.publication
        if current_review and hasattr(current_review, "publication")
        else None
    )
    impact_publishable = bool(
        current_review
        and current_review.decision
        in (ImpactReview.Decision.APPROVED, ImpactReview.Decision.AMENDED)
        and source_confirmation_current
    )
    return render(
        request,
        "console/impact_detail.html",
        {
            **_base_context(
                title=impact.title,
                section="impacts",
                eyebrow="Impact evidence review",
            ),
            "impact": impact,
            "current_review": current_review,
            "review_history": impact.reviews.all(),
            "source_confirmation_current": source_confirmation_current,
            "current_publication": current_publication,
            "impact_publishable": impact_publishable,
            "publication_form": PublicationConfirmationForm(),
            "review_form": ImpactReviewForm(
                impact=impact,
                initial=_impact_review_initial(impact, current_review),
            ),
        },
    )


@staff_required
@require_POST
def impact_review(request, impact_id):
    impact = get_object_or_404(
        RegulatoryImpact.objects.select_related("generation__taxonomy"),
        pk=impact_id,
    )
    form = ImpactReviewForm(request.POST, impact=impact)
    if not form.is_valid():
        messages.error(
            request,
            "Impact review was not recorded: " + json.dumps(form.errors.get_json_data()),
        )
    else:
        try:
            record_impact_decision(
                impact,
                decision=form.cleaned_data["decision"],
                rationale=form.cleaned_data["rationale"],
                title=form.cleaned_data["title"],
                statement=form.cleaned_data["statement"],
                effective_date_text=form.cleaned_data["effective_date_text"],
                included_terms=form.cleaned_data["included_terms"],
                excluded_terms=form.cleaned_data["excluded_terms"],
                user=request.user,
            )
        except ConsoleOperationError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Append-only impact decision recorded.")
    return redirect("console:impact-detail", impact_id=impact.id)


@staff_required
@require_POST
def impact_publish(request, impact_id):
    impact = get_object_or_404(RegulatoryImpact, pk=impact_id)
    form = PublicationConfirmationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Impact publication not confirmed. Type PUBLISH exactly.")
    else:
        current_review = impact.reviews.order_by("-created_at", "-id").first()
        if current_review is None:
            messages.error(request, "This impact has no review to publish.")
        else:
            try:
                publication, created = publish_reviewed_impact(
                    current_review,
                    user=request.user,
                )
            except ConsoleOperationError as error:
                messages.error(request, str(error))
            else:
                outcome = "Published" if created else "Already published"
                messages.success(
                    request,
                    f"{outcome} reviewed impact {publication.id}.",
                )
    return redirect("console:impact-detail", impact_id=impact.id)


@staff_required
def audit_list(request):
    action = request.GET.get("action", "").strip()
    events = AuditEvent.objects.all()
    if action:
        events = events.filter(action__icontains=action)
    page, page_query = _page(request, events, page_size=40)
    return render(
        request,
        "console/audit_list.html",
        {
            **_base_context(title="Audit trail", section="audit"),
            "page": page,
            "page_query": page_query,
            "action": action,
        },
    )
