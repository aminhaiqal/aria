from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.db import transaction

from aria.comparisons.models import ComparisonReview
from aria.events.services import record_audit_event, record_pipeline_event
from aria.impacts.models import (
    ImpactReview,
    RegulatoryImpact,
    ReviewedImpactPublication,
)


@dataclass(frozen=True)
class ImpactPublicationResult:
    publication: ReviewedImpactPublication
    created: bool


def _ensure_publishable(impact: RegulatoryImpact, review: ImpactReview) -> None:
    latest_review = impact.reviews.order_by("-created_at", "-id").first()
    if latest_review is None or latest_review.id != review.id:
        raise ValidationError("Only the latest impact review can be published.")
    if review.decision not in (ImpactReview.Decision.APPROVED, ImpactReview.Decision.AMENDED):
        raise ValidationError("Impact publication requires an approved or amended review.")
    latest_source_review = impact.comparison_item.reviews.order_by("-created_at", "-id").first()
    if (
        latest_source_review is None
        or latest_source_review.id != impact.confirmation_review_id
        or latest_source_review.decision != ComparisonReview.Decision.CONFIRMED
    ):
        raise ValidationError("The impact's source confirmation is no longer current.")


def _target_payload(review: ImpactReview) -> list[dict]:
    return [
        {
            "term_id": str(target.term_id),
            "taxonomy_id": str(target.term.taxonomy_id),
            "taxonomy_checksum": target.term.taxonomy.checksum,
            "dimension": target.term.dimension,
            "code": target.term.code,
            "label": target.term.label,
            "disposition": target.disposition,
            "rationale": target.rationale,
        }
        for target in review.reviewed_targets.select_related("term__taxonomy").order_by(
            "term__dimension", "term__code"
        )
    ]


def _evidence_payload(impact: RegulatoryImpact) -> list[dict]:
    return [
        {
            "side": evidence.side,
            "structural_anchor_id": str(evidence.structural_anchor_id),
            "normalized_section_id": str(evidence.normalized_section_id),
            "artifact_id": str(evidence.source_artifact_id),
            "artifact_sha256": evidence.artifact_sha256,
            "anchor_text": evidence.anchor_text,
            "anchor_text_sha256": evidence.anchor_text_sha256,
            "section_text_sha256": evidence.section_text_sha256,
            "source_locator": evidence.source_locator,
        }
        for evidence in impact.evidence_records.order_by("side")
    ]


@transaction.atomic
def publish_reviewed_impact(
    impact_review: ImpactReview,
    *,
    publisher: AbstractBaseUser,
) -> ImpactPublicationResult:
    impact = RegulatoryImpact.objects.select_for_update().select_related(
        "comparison_item__comparison__identity",
        "comparison_item__comparison__before_version",
        "comparison_item__comparison__after_version",
    ).get(pk=impact_review.impact_id)
    review = ImpactReview.objects.select_related("impact", "reviewer").get(pk=impact_review.pk)
    _ensure_publishable(impact, review)
    existing = ReviewedImpactPublication.objects.filter(impact_review=review).first()
    if existing:
        return ImpactPublicationResult(publication=existing, created=False)
    comparison = impact.comparison_item.comparison
    targets = _target_payload(review)
    if not any(target["disposition"] == "included" for target in targets):
        raise ValidationError("A published impact requires an included applicability target.")
    event = record_pipeline_event(
        event_type="regulatory.impact.confirmed",
        aggregate_type="regulatory_impact",
        aggregate_id=impact.id,
        payload={
            "impact_id": str(impact.id),
            "impact_type": impact.impact_type,
            "impact_review_id": str(review.id),
            "review_decision": review.decision,
            "reviewed_title": review.reviewed_title,
            "reviewed_statement": review.reviewed_statement,
            "reviewed_effective_date_text": review.reviewed_effective_date_text,
            "review": {
                "reviewer_id": str(review.reviewer_id),
                "rationale": review.rationale,
                "reviewed_at": review.created_at.isoformat(),
            },
            "identity": {
                "id": str(comparison.identity_id),
                "title": comparison.identity.canonical_title,
                "canonical_url": comparison.identity.canonical_url,
            },
            "comparison": {
                "id": str(comparison.id),
                "comparison_item_id": str(impact.comparison_item_id),
                "change_type": impact.comparison_item.change_type,
                "confirmation_review_id": str(impact.confirmation_review_id),
                "before_version_id": str(comparison.before_version_id),
                "after_version_id": str(comparison.after_version_id),
                "before_content_sha256": comparison.before_version.normalized_content_sha256,
                "after_content_sha256": comparison.after_version.normalized_content_sha256,
            },
            "targets": targets,
            "evidence": _evidence_payload(impact),
            "legal_effect_assessed": False,
        },
    )
    publication = ReviewedImpactPublication(
        impact_review=review,
        pipeline_event=event,
        published_by=publisher,
    )
    publication.full_clean()
    publication.save()
    record_audit_event(
        action="impact.review_published",
        target_type="reviewed_impact_publication",
        target_id=publication.id,
        actor_type="user",
        actor_identifier=str(publisher.pk),
        details={
            "impact_id": str(impact.id),
            "impact_review_id": str(review.id),
            "pipeline_event_id": str(event.id),
            "outbox_topic": event.event_type,
            "legal_effect_assessed": False,
        },
    )
    return ImpactPublicationResult(publication=publication, created=True)
