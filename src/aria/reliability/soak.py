from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from aria.browser.models import SourceAdmissionPromotion
from aria.discovery.models import SourceRun
from aria.fetching.models import FetchAttempt
from aria.reliability.models import SourceReliabilityAssessment
from aria.sources.models import SourceEndpoint


def _gate(code: str, passed: bool, detail: str) -> dict:
    return {"code": code, "passed": passed, "detail": detail}


def _promotion_acceptance(
    endpoint: SourceEndpoint,
    *,
    promotion: SourceAdmissionPromotion | None,
    assessed_at,
) -> dict:
    snapshot = endpoint.source_pack_snapshots.first()
    baseline_at = promotion.promoted_at if promotion else snapshot.applied_at
    first_poll_due_at = promotion.next_poll_at if promotion else endpoint.next_poll_at
    run = (
        SourceRun.objects.filter(
            endpoint=endpoint,
            trigger=SourceRun.Trigger.SCHEDULED,
            created_at__gte=baseline_at,
            resource_run__isnull=True,
        )
        .order_by("created_at", "id")
        .first()
    )
    base = {
        "endpoint_id": str(endpoint.id),
        "endpoint_name": endpoint.name,
        "authority": endpoint.collection.authority.name,
        "promotion_id": str(promotion.id) if promotion else "",
        "promoted_at": promotion.promoted_at if promotion else None,
        "acceptance_baseline_at": baseline_at,
        "first_poll_due_at": first_poll_due_at,
        "scheduled_run": None,
        "gates": [],
    }
    if run is None:
        if first_poll_due_at is None:
            base.update(
                {
                    "status": "failed",
                    "detail": "The admitted source has no autonomous poll scheduled.",
                }
            )
            return base
        overdue_at = first_poll_due_at + timedelta(
            minutes=settings.SOURCE_FRESHNESS_GRACE_MINUTES
        )
        base.update(
            {
                "status": "pending" if assessed_at <= overdue_at else "failed",
                "detail": (
                    "Awaiting the first autonomous scheduled run."
                    if assessed_at <= overdue_at
                    else "The first autonomous run is overdue beyond the freshness grace period."
                ),
            }
        )
        return base

    base["scheduled_run"] = {
        "id": str(run.id),
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "candidate_count": run.discovered_candidate_count,
    }
    if run.status in {SourceRun.Status.PENDING, SourceRun.Status.RUNNING}:
        base.update({"status": "pending", "detail": "The first scheduled run is in progress."})
        return base
    if run.status != SourceRun.Status.COMPLETED:
        base.update(
            {
                "status": "failed",
                "detail": f"The first scheduled run ended with status {run.status}.",
                "gates": [
                    _gate(
                        "scheduled_run_completed",
                        False,
                        run.error_message or run.error_code or run.status,
                    )
                ],
            }
        )
        return base

    candidate_count = run.candidate_observations.count()
    artifact_count = run.artifact_observations.values("candidate_id").distinct().count()
    failed_fetch_count = run.fetch_attempts.exclude(
        status__in=(FetchAttempt.Status.SUCCEEDED, FetchAttempt.Status.NOT_MODIFIED)
    ).count()
    unchanged_count = run.artifact_observations.filter(content_changed=False).count()
    changed_count = run.artifact_observations.filter(content_changed=True).count()
    reliability = (
        SourceReliabilityAssessment.objects.filter(source_run=run)
        .order_by("-assessed_at", "-id")
        .first()
    )
    gates = [
        _gate("scheduled_run_completed", True, f"Run {run.id} completed autonomously."),
        _gate(
            "candidate_observation_coverage",
            candidate_count == run.discovered_candidate_count and candidate_count > 0,
            (
                f"Observed {candidate_count} of {run.discovered_candidate_count} "
                "discovered candidates."
            ),
        ),
        _gate(
            "artifact_observation_coverage",
            artifact_count == candidate_count,
            f"Recorded artifact evidence for {artifact_count} of {candidate_count} candidates.",
        ),
        _gate(
            "bounded_fetches_succeeded",
            failed_fetch_count == 0,
            f"Found {failed_fetch_count} failed or quarantined fetch attempts.",
        ),
        _gate(
            "next_poll_advanced",
            bool(
                endpoint.next_poll_at
                and run.started_at
                and endpoint.next_poll_at > run.started_at
            ),
            f"Next poll is {endpoint.next_poll_at}.",
        ),
        _gate(
            "pipeline_reliability_healthy",
            bool(reliability and reliability.status == SourceReliabilityAssessment.Status.HEALTHY),
            (
                f"Reliability is {reliability.status}."
                if reliability
                else "No reliability assessment exists for the scheduled run yet."
            ),
        ),
    ]
    still_in_pipeline_grace = bool(
        run.finished_at
        and assessed_at
        <= run.finished_at + timedelta(minutes=settings.SOURCE_PIPELINE_GRACE_MINUTES)
    )
    failed_gates = [gate for gate in gates if not gate["passed"]]
    status = "passed"
    detail = (
        f"Accepted with {unchanged_count} unchanged and {changed_count} changed "
        "artifact observations."
    )
    if failed_gates:
        only_reliability_pending = {gate["code"] for gate in failed_gates} == {
            "pipeline_reliability_healthy"
        }
        if only_reliability_pending and still_in_pipeline_grace:
            status = "pending"
            detail = "The scheduled run completed; downstream reliability is inside pipeline grace."
        else:
            status = "failed"
            detail = f"{len(failed_gates)} autonomous acceptance gate(s) failed."
    base.update(
        {
            "status": status,
            "detail": detail,
            "gates": gates,
            "artifact_changes": {"changed": changed_count, "unchanged": unchanged_count},
            "reliability_assessment_id": str(reliability.id) if reliability else "",
        }
    )
    return base


def collect_autonomous_cycle_acceptance(*, assessed_at=None) -> dict:
    now = assessed_at or timezone.now()
    promotions = list(
        SourceAdmissionPromotion.objects.select_related(
            "endpoint",
            "endpoint__collection",
            "endpoint__collection__authority",
        )
        .filter(endpoint__is_enabled=True)
        .order_by("endpoint_id", "-promoted_at", "-id")
        .distinct("endpoint_id")
    )
    promotion_by_endpoint = {promotion.endpoint_id: promotion for promotion in promotions}
    endpoint_ids = set(promotion_by_endpoint)
    endpoint_ids.update(
        SourceEndpoint.objects.filter(
            is_enabled=True,
            requires_javascript=True,
            source_pack_snapshots__isnull=False,
        ).values_list("id", flat=True)
    )
    endpoints = SourceEndpoint.objects.filter(id__in=endpoint_ids).select_related(
        "collection",
        "collection__authority",
    ).prefetch_related("source_pack_snapshots")
    sources = [
        _promotion_acceptance(
            endpoint,
            promotion=promotion_by_endpoint.get(endpoint.id),
            assessed_at=now,
        )
        for endpoint in endpoints
    ]
    if not sources:
        status = "not_applicable"
    elif all(source["status"] == "passed" for source in sources):
        status = "passed"
    elif any(source["status"] == "failed" for source in sources):
        status = "failed"
    else:
        status = "pending"
    return {
        "status": status,
        "assessed_at": now,
        "source_count": len(sources),
        "passed_count": sum(source["status"] == "passed" for source in sources),
        "pending_count": sum(source["status"] == "pending" for source in sources),
        "failed_count": sum(source["status"] == "failed" for source in sources),
        "sources": sources,
    }
