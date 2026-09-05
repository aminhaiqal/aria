from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.db import transaction

from aria.comparisons.models import ComparisonReview
from aria.events.services import record_audit_event
from aria.impacts.models import (
    ApplicabilityTerm,
    ImpactReview,
    ImpactReviewTarget,
    ImpactTarget,
    RegulatoryImpact,
)


def _ensure_source_confirmation_is_current(impact: RegulatoryImpact) -> None:
    latest = impact.comparison_item.reviews.order_by("-created_at", "-id").first()
    if (
        latest is None
        or latest.id != impact.confirmation_review_id
        or latest.decision != ComparisonReview.Decision.CONFIRMED
    ):
        raise ValidationError(
            "The source textual-change confirmation is no longer current; regenerate the impact."
        )


def _candidate_target_specs(impact: RegulatoryImpact) -> list[dict]:
    return [
        {
            "term": target.term,
            "disposition": target.disposition,
            "rationale": target.rationale,
            "source_target": target,
        }
        for target in impact.targets.select_related("term").order_by(
            "term__dimension", "term__code"
        )
    ]


def _validate_target_specs(impact: RegulatoryImpact, specs: list[dict]) -> None:
    terms = [spec.get("term") for spec in specs]
    if any(not isinstance(term, ApplicabilityTerm) for term in terms):
        raise ValidationError("Every reviewed target must be a controlled applicability term.")
    term_ids = [term.id for term in terms]
    if len(term_ids) != len(set(term_ids)):
        raise ValidationError("Reviewed applicability targets cannot contain duplicates.")
    taxonomy_id = impact.generation.taxonomy_id if impact.generation_id else None
    if taxonomy_id is None and impact.targets.exists():
        taxonomy_id = impact.targets.select_related("term").first().term.taxonomy_id
    for spec in specs:
        term = spec.get("term")
        if taxonomy_id and term.taxonomy_id != taxonomy_id:
            raise ValidationError("Reviewed targets must use the candidate taxonomy version.")
        if spec.get("disposition") not in ImpactTarget.Disposition.values:
            raise ValidationError("A reviewed target has an unsupported disposition.")
        if not str(spec.get("rationale", "")).strip():
            raise ValidationError("Every reviewed target requires a rationale.")


@transaction.atomic
def record_impact_review(
    impact: RegulatoryImpact,
    *,
    decision: str,
    reviewer: AbstractBaseUser,
    rationale: str = "",
    amended_title: str = "",
    amended_statement: str = "",
    amended_effective_date_text: str = "",
    target_specs: list[dict] | None = None,
) -> ImpactReview:
    # Lock only the impact row. ``generation`` is nullable, and PostgreSQL does
    # not allow ``FOR UPDATE`` across the nullable side of the outer join that
    # ``select_related`` would produce here.
    impact = RegulatoryImpact.objects.select_for_update().get(pk=impact.pk)
    _ensure_source_confirmation_is_current(impact)
    if decision not in ImpactReview.Decision.values:
        raise ValidationError("Unsupported impact review decision.")
    rationale = rationale.strip()
    if decision == ImpactReview.Decision.AMENDED:
        reviewed_title = amended_title.strip()
        reviewed_statement = amended_statement.strip()
        reviewed_effective_date = amended_effective_date_text.strip()
        if target_specs is None:
            raise ValidationError("An amended decision requires explicit applicability targets.")
        specs = target_specs
    else:
        reviewed_title = impact.title
        reviewed_statement = impact.statement
        reviewed_effective_date = impact.effective_date_text
        specs = _candidate_target_specs(impact)
    _validate_target_specs(impact, specs)
    if decision in (ImpactReview.Decision.APPROVED, ImpactReview.Decision.AMENDED) and not specs:
        raise ValidationError("Approved impacts require at least one applicability target.")

    previous = impact.reviews.order_by("-created_at", "-id").first()
    review = ImpactReview(
        impact=impact,
        decision=decision,
        reviewed_title=reviewed_title,
        reviewed_statement=reviewed_statement,
        reviewed_effective_date_text=reviewed_effective_date,
        rationale=rationale,
        reviewer=reviewer,
        previous_review=previous,
    )
    review.full_clean()
    review.save()
    for spec in specs:
        reviewed_target = ImpactReviewTarget(
            impact_review=review,
            term=spec["term"],
            disposition=spec["disposition"],
            rationale=str(spec["rationale"]).strip(),
            source_target=spec.get("source_target"),
        )
        reviewed_target.full_clean()
        reviewed_target.save()
    record_audit_event(
        action="impact.review_recorded",
        target_type="regulatory_impact",
        target_id=impact.id,
        actor_type="user",
        actor_identifier=str(reviewer.pk),
        details={
            "review_id": str(review.id),
            "decision": decision,
            "previous_review_id": str(previous.id) if previous else None,
            "confirmation_review_id": str(impact.confirmation_review_id),
            "reviewed_target_ids": [
                str(target.id) for target in review.reviewed_targets.order_by("id")
            ],
            "legal_effect_assessed": False,
        },
    )
    return review
