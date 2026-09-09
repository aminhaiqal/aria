from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.db import transaction

from aria.comparisons.models import (
    ComparisonItem,
    ComparisonReview,
    DocumentComparison,
    ReviewedChangePublication,
)
from aria.events.services import record_pipeline_event


@dataclass(frozen=True)
class ChangePublicationSummary:
    candidate_count: int
    published_count: int
    skipped_count: int
    publication_ids: tuple[str, ...]


def _anchor_payload(item: ComparisonItem, side: str) -> dict | None:
    anchor = item.before_anchor if side == "before" else item.after_anchor
    if anchor is None:
        return None
    return {
        "anchor_id": str(anchor.id),
        "canonical_key": anchor.canonical_key,
        "section_id": str(anchor.normalized_section_id),
        "artifact_id": str(anchor.source_artifact_id),
        "artifact_sha256": anchor.source_artifact.sha256,
        "source_locator": anchor.source_locator,
        "text_sha256": anchor.text_sha256,
    }


@transaction.atomic
def publish_confirmed_comparison_changes(
    comparison: DocumentComparison,
    *,
    publisher: AbstractBaseUser | None = None,
) -> ChangePublicationSummary:
    items = list(
        comparison.items.exclude(change_type=ComparisonItem.ChangeType.UNCHANGED).select_related(
            "before_anchor__source_artifact",
            "after_anchor__source_artifact",
        )
    )
    publications = []
    candidate_count = 0
    for item in items:
        review = item.reviews.order_by("-created_at", "-id").first()
        if review is None or review.decision != ComparisonReview.Decision.CONFIRMED:
            continue
        candidate_count += 1
        existing = ReviewedChangePublication.objects.filter(confirmation_review=review).first()
        if existing is not None:
            publications.append((existing, False))
            continue
        if settings.REQUIRE_SEPARATE_PUBLISHER and (
            publisher is None or review.reviewer_id == publisher.pk
        ):
            raise ValidationError(
                "A different authenticated publisher must release confirmed textual changes."
            )
        event = record_pipeline_event(
            event_type="regulatory.textual_change.confirmed",
            aggregate_type="comparison_item",
            aggregate_id=item.id,
            payload={
                "comparison_id": str(comparison.id),
                "comparison_item_id": str(item.id),
                "confirmation_review_id": str(review.id),
                "identity": {
                    "id": str(comparison.identity_id),
                    "title": comparison.identity.canonical_title,
                    "canonical_url": comparison.identity.canonical_url,
                },
                "before_version": {
                    "id": str(comparison.before_version_id),
                    "content_sha256": comparison.before_version.normalized_content_sha256,
                },
                "after_version": {
                    "id": str(comparison.after_version_id),
                    "content_sha256": comparison.after_version.normalized_content_sha256,
                },
                "change_type": item.change_type,
                "before": _anchor_payload(item, "before"),
                "after": _anchor_payload(item, "after"),
                "review": {
                    "reviewer_id": str(review.reviewer_id),
                    "rationale": review.rationale,
                    "reviewed_at": review.created_at.isoformat(),
                },
                "publisher_id": str(publisher.pk) if publisher is not None else "",
                "legal_effect_assessed": False,
            },
        )
        publication = ReviewedChangePublication.objects.create(
            comparison_item=item,
            confirmation_review=review,
            pipeline_event=event,
            published_by=publisher,
        )
        publications.append((publication, True))
    return ChangePublicationSummary(
        candidate_count=candidate_count,
        published_count=sum(created for _, created in publications),
        skipped_count=sum(not created for _, created in publications),
        publication_ids=tuple(str(publication.id) for publication, _ in publications),
    )
