from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.db import transaction

from aria.comparisons.models import ComparisonItem, ComparisonReview
from aria.events.services import record_audit_event


@transaction.atomic
def record_comparison_review(
    item: ComparisonItem,
    *,
    decision: str,
    reviewer: AbstractBaseUser,
    rationale: str = "",
) -> ComparisonReview:
    if item.change_type == ComparisonItem.ChangeType.UNCHANGED:
        raise ValidationError("Unchanged alignments do not require review.")
    if decision not in ComparisonReview.Decision.values:
        raise ValidationError("Unsupported comparison review decision.")
    locked_item = ComparisonItem.objects.select_for_update().get(pk=item.pk)
    previous = locked_item.reviews.order_by("-created_at", "-id").first()
    review = ComparisonReview.objects.create(
        comparison_item=locked_item,
        decision=decision,
        rationale=rationale,
        reviewer=reviewer,
        previous_review=previous,
    )
    record_audit_event(
        action="comparison.review_recorded",
        target_type="comparison_item",
        target_id=item.id,
        actor_type="user",
        actor_identifier=str(reviewer.pk),
        details={
            "review_id": str(review.id),
            "decision": decision,
            "previous_review_id": str(previous.id) if previous else None,
        },
    )
    return review
