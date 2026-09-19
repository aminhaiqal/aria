import uuid
from collections import defaultdict

from django.contrib.auth import get_user_model

from aria.events.models import AuditEvent, OutboxEvent
from aria.events.services import record_audit_event
from aria.impacts.models import BusinessProfile, ProfileImpactMatch, ReviewedImpactPublication
from aria.orchestration.models import ChangeOrchestration
from aria.reader.usage import (
    DOCUMENT_VIEWED,
    EVIDENCE_DOWNLOADED,
    PILOT_FEEDBACK_RECORDED,
    SEARCH_COMPLETED,
)
from aria.reliability.confidence import (
    collect_source_confidence_report,
    serialize_source_confidence,
)

NEXT_MILESTONE_SELECTED = "product.next_milestone.selected"
NEXT_MILESTONE_TARGET_ID = uuid.uuid5(uuid.NAMESPACE_URL, "aria:product:phase-5")


def record_next_product_milestone(
    *,
    title: str,
    evidence: str,
    selected_by: str,
) -> AuditEvent:
    title = title.strip()
    evidence = evidence.strip()
    selected_by = selected_by.strip()
    if len(title) < 5 or len(title) > 200:
        raise ValueError("Milestone title must contain 5 to 200 characters.")
    if len(evidence) < 20 or len(evidence) > 2000:
        raise ValueError("Milestone evidence must contain 20 to 2000 characters.")
    if not selected_by:
        raise ValueError("The milestone selector is required.")
    return record_audit_event(
        action=NEXT_MILESTONE_SELECTED,
        target_type="product_phase",
        target_id=NEXT_MILESTONE_TARGET_ID,
        actor_type="operator",
        actor_identifier=selected_by[:255],
        details={"title": title, "evidence": evidence},
    )


def _source_status(source_rows: list[dict]) -> dict:
    enabled = [row for row in source_rows if row["is_enabled"]]
    healthy = [
        row
        for row in enabled
        if row["state"] == "operational"
        and row["reliability_status"] == "healthy"
        and row["evidence_coverage_percent"] == 100
    ]
    return {
        "enabled_count": len(enabled),
        "healthy_count": len(healthy),
        "passed": bool(enabled) and len(healthy) == len(enabled),
        "sources": source_rows,
    }


def _journey_status() -> dict:
    publications = ReviewedImpactPublication.objects.select_related(
        "impact_review",
        "pipeline_event__outbox_event",
    ).prefetch_related("impact_review__profile_matches")
    completed = []
    for publication in publications:
        outbox = publication.pipeline_event.outbox_event
        matched = publication.impact_review.profile_matches.filter(
            outcome=ProfileImpactMatch.Outcome.MATCHED
        ).exists()
        if outbox.status == OutboxEvent.Status.PUBLISHED and matched:
            completed.append(str(publication.id))
    return {
        "publication_count": publications.count(),
        "completed_count": len(completed),
        "completed_publication_ids": completed,
        "passed": bool(completed),
    }


def _pilot_status() -> dict:
    user_model = get_user_model()
    users = list(user_model.objects.filter(is_active=True, is_staff=False).order_by("pk"))
    profile_owner_ids = set(
        BusinessProfile.objects.filter(is_active=True).values_list("owner_id", flat=True)
    )
    events_by_user: dict[str, list[AuditEvent]] = defaultdict(list)
    events = AuditEvent.objects.filter(
        action__in=(
            SEARCH_COMPLETED,
            DOCUMENT_VIEWED,
            EVIDENCE_DOWNLOADED,
            PILOT_FEEDBACK_RECORDED,
        )
    ).order_by("occurred_at", "id")
    for event in events:
        events_by_user[event.actor_identifier].append(event)

    engaged = []
    feedback_user_ids = set()
    search_count = 0
    successful_search_count = 0
    document_view_count = 0
    evidence_download_count = 0
    feedback_count = 0
    ratings: dict[str, list[int]] = defaultdict(list)
    for event in events:
        if event.action == SEARCH_COMPLETED:
            search_count += 1
            if int(event.details.get("bounded_result_count", 0)) > 0:
                successful_search_count += 1
        elif event.action == DOCUMENT_VIEWED:
            document_view_count += 1
        elif event.action == EVIDENCE_DOWNLOADED:
            evidence_download_count += 1
        elif event.action == PILOT_FEEDBACK_RECORDED:
            feedback_count += 1
            feedback_user_ids.add(event.actor_identifier)
            rating = event.details.get("rating")
            category = event.details.get("category", "unknown")
            if isinstance(rating, int):
                ratings[category].append(rating)

    for user in users:
        user_events = events_by_user.get(str(user.pk), [])
        actions = {event.action for event in user_events}
        profiled_use = any(
            event.action in (SEARCH_COMPLETED, DOCUMENT_VIEWED)
            and bool(event.details.get("profile_id"))
            for event in user_events
        )
        if (
            user.pk in profile_owner_ids
            and profiled_use
            and EVIDENCE_DOWNLOADED in actions
            and PILOT_FEEDBACK_RECORDED in actions
        ):
            engaged.append(user.get_username())

    return {
        "eligible_user_count": len(users),
        "active_profile_owner_count": len(profile_owner_ids.intersection({u.pk for u in users})),
        "engaged_user_count": len(engaged),
        "engaged_usernames": engaged,
        "search_count": search_count,
        "successful_search_count": successful_search_count,
        "document_view_count": document_view_count,
        "evidence_download_count": evidence_download_count,
        "feedback_count": feedback_count,
        "feedback_user_count": len(feedback_user_ids),
        "average_ratings": {
            category: round(sum(values) / len(values), 2)
            for category, values in sorted(ratings.items())
            if values
        },
        "passed": len(engaged) >= 3,
    }


def _next_milestone_status() -> dict:
    event = (
        AuditEvent.objects.filter(
            action=NEXT_MILESTONE_SELECTED,
            target_type="product_phase",
            target_id=NEXT_MILESTONE_TARGET_ID,
        )
        .order_by("-occurred_at", "-id")
        .first()
    )
    if event is None:
        return {"passed": False, "selection": None}
    return {
        "passed": True,
        "selection": {
            "title": event.details.get("title", ""),
            "evidence": event.details.get("evidence", ""),
            "selected_by": event.actor_identifier,
            "selected_at": event.occurred_at.isoformat(),
            "audit_event_id": str(event.id),
        },
    }


def collect_phase5_status(*, source_rows: list[dict] | None = None) -> dict:
    serialized_sources = (
        source_rows
        if source_rows is not None
        else [serialize_source_confidence(row) for row in collect_source_confidence_report()]
    )
    sources = _source_status(serialized_sources)
    quality_review_count = ChangeOrchestration.objects.filter(
        status=ChangeOrchestration.Status.QUALITY_REVIEW_REQUIRED
    ).count()
    baseline = {
        "sources": sources,
        "quality_review_workflow_count": quality_review_count,
        "passed": sources["passed"] and quality_review_count == 0,
    }
    journey = _journey_status()
    pilot = _pilot_status()
    next_milestone = _next_milestone_status()
    criteria = {
        "production_baseline": baseline["passed"],
        "complete_change_journey": journey["passed"],
        "three_engaged_pilot_users": pilot["passed"],
        "pilot_findings_and_metrics": (
            pilot["feedback_user_count"] >= 3
            and pilot["search_count"] > 0
            and pilot["evidence_download_count"] > 0
        ),
        "next_product_milestone_selected": next_milestone["passed"],
    }
    return {
        "phase": "Phase 5: first product proof",
        "status": "complete" if all(criteria.values()) else "in_progress",
        "criteria": criteria,
        "production_baseline": baseline,
        "change_journey": journey,
        "pilot": pilot,
        "next_product_milestone": next_milestone,
    }
