import hashlib
import json
from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.db import transaction

from aria.comparisons.models import ComparisonReview
from aria.events.services import record_audit_event
from aria.impacts.models import (
    ApplicabilityTaxonomy,
    ApplicabilityTerm,
    BusinessProfile,
    BusinessProfileTerm,
    ImpactReview,
    ProfileImpactMatch,
    RegulatoryImpact,
)

MATCH_RULESET = "aria-exact-applicability-v1"
WILDCARD_TERMS = {
    (ApplicabilityTerm.Dimension.SECTOR, "cross-sector"),
    (ApplicabilityTerm.Dimension.SIZE, "any-size"),
}
SINGLE_VALUE_DIMENSIONS = {
    ApplicabilityTerm.Dimension.JURISDICTION,
    ApplicabilityTerm.Dimension.ORGANIZATION_TYPE,
    ApplicabilityTerm.Dimension.SIZE,
}


@dataclass(frozen=True)
class ProfileMatchResult:
    match: ProfileImpactMatch
    created: bool


def _fingerprint(value: dict) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_profile_terms(
    taxonomy: ApplicabilityTaxonomy,
    terms: list[ApplicabilityTerm],
) -> None:
    if not terms:
        raise ValidationError("A business profile requires at least one controlled term.")
    if len({term.id for term in terms}) != len(terms):
        raise ValidationError("A business profile cannot contain duplicate terms.")
    if any(term.taxonomy_id != taxonomy.id for term in terms):
        raise ValidationError("Every business-profile term must use the selected taxonomy version.")
    for dimension in SINGLE_VALUE_DIMENSIONS:
        if sum(term.dimension == dimension for term in terms) > 1:
            raise ValidationError(
                f"A business profile accepts only one {dimension.replace('_', ' ')} term."
            )


@transaction.atomic
def save_business_profile(
    *,
    owner: AbstractBaseUser,
    taxonomy: ApplicabilityTaxonomy,
    name: str,
    terms: list[ApplicabilityTerm],
    notes: str = "",
    is_active: bool = True,
    profile: BusinessProfile | None = None,
) -> BusinessProfile:
    terms = list(terms)
    _validate_profile_terms(taxonomy, terms)
    created = profile is None
    if profile is None:
        profile = BusinessProfile(owner=owner, taxonomy=taxonomy)
    else:
        profile = BusinessProfile.objects.select_for_update().get(pk=profile.pk)
        if profile.owner_id != owner.pk:
            raise ValidationError("Only the profile owner may update this business profile.")
        if profile.taxonomy_id != taxonomy.id:
            raise ValidationError("Create a new profile to move to another taxonomy version.")
    profile.name = name.strip()
    profile.notes = notes.strip()
    profile.is_active = is_active
    profile.full_clean()
    profile.save()
    BusinessProfileTerm.objects.filter(profile=profile).delete()
    for term in sorted(terms, key=lambda value: (value.dimension, value.code)):
        assignment = BusinessProfileTerm(profile=profile, term=term)
        assignment.full_clean()
        assignment.save()
    record_audit_event(
        action="business_profile.created" if created else "business_profile.updated",
        target_type="business_profile",
        target_id=profile.id,
        actor_type="user",
        actor_identifier=str(owner.pk),
        details={
            "taxonomy_id": str(taxonomy.id),
            "taxonomy_checksum": taxonomy.checksum,
            "term_ids": [str(term.id) for term in terms],
            "is_active": is_active,
        },
    )
    return profile


def _ensure_current_review(impact: RegulatoryImpact, review: ImpactReview) -> None:
    latest_impact_review = impact.reviews.order_by("-created_at", "-id").first()
    if latest_impact_review is None or latest_impact_review.id != review.id:
        raise ValidationError("Only the latest impact review can be matched.")
    if review.decision not in (ImpactReview.Decision.APPROVED, ImpactReview.Decision.AMENDED):
        raise ValidationError("Business matching requires an approved or amended impact review.")
    latest_source_review = impact.comparison_item.reviews.order_by("-created_at", "-id").first()
    if (
        latest_source_review is None
        or latest_source_review.id != impact.confirmation_review_id
        or latest_source_review.decision != ComparisonReview.Decision.CONFIRMED
    ):
        raise ValidationError("The impact's source confirmation is no longer current.")


def _term_snapshot(term: ApplicabilityTerm) -> dict:
    return {
        "id": str(term.id),
        "dimension": term.dimension,
        "code": term.code,
        "label": term.label,
    }


def _profile_snapshot(profile: BusinessProfile, terms: list[ApplicabilityTerm]) -> dict:
    return {
        "profile_id": str(profile.id),
        "name": profile.name,
        "notes": profile.notes,
        "is_active": profile.is_active,
        "taxonomy_id": str(profile.taxonomy_id),
        "taxonomy_checksum": profile.taxonomy.checksum,
        "terms": [_term_snapshot(term) for term in terms],
    }


def _review_snapshot(review: ImpactReview) -> dict:
    targets = review.reviewed_targets.select_related("term").order_by(
        "term__dimension", "term__code"
    )
    return {
        "impact_id": str(review.impact_id),
        "impact_review_id": str(review.id),
        "decision": review.decision,
        "title": review.reviewed_title,
        "statement": review.reviewed_statement,
        "effective_date_text": review.reviewed_effective_date_text,
        "targets": [
            {
                **_term_snapshot(target.term),
                "disposition": target.disposition,
                "rationale": target.rationale,
            }
            for target in targets
        ],
    }


def _evaluate(
    profile_terms: list[ApplicabilityTerm],
    review_snapshot: dict,
) -> tuple[str, list[dict], list[dict], list[str], list[str], str]:
    profile_by_dimension: dict[str, set[str]] = {}
    for term in profile_terms:
        profile_by_dimension.setdefault(term.dimension, set()).add(term.code)

    targets = review_snapshot["targets"]
    excluded = [target for target in targets if target["disposition"] == "excluded"]
    included = [target for target in targets if target["disposition"] == "included"]
    excluded_hits = [
        target
        for target in excluded
        if target["code"] in profile_by_dimension.get(target["dimension"], set())
    ]
    matched: list[dict] = []
    unmet: list[str] = []
    unresolved: list[str] = []
    included_by_dimension: dict[str, list[dict]] = {}
    for target in included:
        included_by_dimension.setdefault(target["dimension"], []).append(target)
    for dimension, dimension_targets in included_by_dimension.items():
        wildcard = next(
            (
                target
                for target in dimension_targets
                if (dimension, target["code"]) in WILDCARD_TERMS
            ),
            None,
        )
        if wildcard:
            matched.append(wildcard)
            continue
        profile_codes = profile_by_dimension.get(dimension)
        if not profile_codes:
            unresolved.append(dimension)
            continue
        hits = [target for target in dimension_targets if target["code"] in profile_codes]
        if hits:
            matched.extend(hits)
        else:
            unmet.append(dimension)

    if excluded_hits:
        outcome = ProfileImpactMatch.Outcome.NOT_MATCHED
        explanation = "A reviewed exclusion exactly matches the business profile."
    elif not included_by_dimension:
        outcome = ProfileImpactMatch.Outcome.INSUFFICIENT_CONTEXT
        unresolved = ["included_target"]
        explanation = "The reviewed impact has no included applicability target."
    elif unresolved:
        outcome = ProfileImpactMatch.Outcome.INSUFFICIENT_CONTEXT
        explanation = "The profile is missing a dimension required by the reviewed impact."
    elif unmet:
        outcome = ProfileImpactMatch.Outcome.NOT_MATCHED
        explanation = "The profile does not exactly match every targeted applicability dimension."
    else:
        outcome = ProfileImpactMatch.Outcome.MATCHED
        explanation = "The profile exactly matches every targeted applicability dimension."
    return outcome, matched, excluded_hits, sorted(unmet), sorted(unresolved), explanation


@transaction.atomic
def match_business_profile(
    profile: BusinessProfile,
    impact_review: ImpactReview,
) -> ProfileMatchResult:
    profile = BusinessProfile.objects.select_for_update().select_related("taxonomy").get(
        pk=profile.pk
    )
    impact = RegulatoryImpact.objects.select_for_update().get(pk=impact_review.impact_id)
    review = ImpactReview.objects.select_related("impact").get(pk=impact_review.pk)
    _ensure_current_review(impact, review)
    if not profile.is_active:
        raise ValidationError("Inactive business profiles cannot be matched.")
    profile_terms = list(profile.terms.order_by("dimension", "code"))
    review_snapshot = _review_snapshot(review)
    target_taxonomies = {
        target.term.taxonomy_id
        for target in review.reviewed_targets.select_related("term")
    }
    if target_taxonomies != {profile.taxonomy_id}:
        raise ValidationError("The business profile and reviewed impact use different taxonomies.")
    profile_snapshot = _profile_snapshot(profile, profile_terms)
    outcome, matched, excluded, unmet, unresolved, explanation = _evaluate(
        profile_terms,
        review_snapshot,
    )
    fingerprint_input = {
        "ruleset": MATCH_RULESET,
        "profile": profile_snapshot,
        "impact_review": review_snapshot,
    }
    fingerprint = _fingerprint(fingerprint_input)
    existing = ProfileImpactMatch.objects.filter(input_fingerprint=fingerprint).first()
    if existing:
        return ProfileMatchResult(match=existing, created=False)
    match = ProfileImpactMatch(
        profile=profile,
        impact_review=review,
        outcome=outcome,
        ruleset=MATCH_RULESET,
        input_fingerprint=fingerprint,
        profile_snapshot=profile_snapshot,
        impact_review_snapshot=review_snapshot,
        matched_terms=matched,
        excluded_terms=excluded,
        unmet_dimensions=unmet,
        unresolved_dimensions=unresolved,
        explanation=explanation,
    )
    match.full_clean()
    match.save()
    record_audit_event(
        action="business_profile.impact_evaluated",
        target_type="profile_impact_match",
        target_id=match.id,
        details={
            "profile_id": str(profile.id),
            "impact_review_id": str(review.id),
            "outcome": outcome,
            "ruleset": MATCH_RULESET,
            "input_fingerprint": fingerprint,
        },
    )
    return ProfileMatchResult(match=match, created=True)
