import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction

from aria.comparisons.models import ComparisonItem, ComparisonReview
from aria.events.services import record_audit_event
from aria.impacts.models import ImpactEvidence, RegulatoryImpact


def _fingerprint(*, item: ComparisonItem, confirmation: ComparisonReview, payload: dict) -> str:
    encoded = json.dumps(
        {
            "comparison_item_id": str(item.id),
            "comparison_item_fingerprint": item.fingerprint,
            "confirmation_review_id": str(confirmation.id),
            **payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _current_confirmation(item: ComparisonItem) -> ComparisonReview:
    review = item.reviews.order_by("-created_at", "-id").first()
    if review is None or review.decision != ComparisonReview.Decision.CONFIRMED:
        raise ValidationError("Impact candidates require a current confirmed change review.")
    return review


@transaction.atomic
def create_regulatory_impact(
    item: ComparisonItem,
    *,
    impact_type: str,
    origin: str,
    title: str,
    statement: str,
    rationale: str = "",
    effective_date_text: str = "",
    actor_type: str = "system",
    actor_identifier: str = "",
) -> tuple[RegulatoryImpact, bool]:
    item = ComparisonItem.objects.select_for_update().select_related("comparison").get(pk=item.pk)
    confirmation = _current_confirmation(item)
    if impact_type not in RegulatoryImpact.ImpactType.values:
        raise ValidationError("Unsupported regulatory impact type.")
    if origin not in RegulatoryImpact.Origin.values:
        raise ValidationError("Unsupported regulatory impact origin.")
    title = title.strip()
    statement = statement.strip()
    rationale = rationale.strip()
    effective_date_text = effective_date_text.strip()
    payload = {
        "impact_type": impact_type,
        "origin": origin,
        "title": title,
        "statement": statement,
        "rationale": rationale,
        "effective_date_text": effective_date_text,
    }
    fingerprint = _fingerprint(item=item, confirmation=confirmation, payload=payload)
    existing = RegulatoryImpact.objects.filter(input_fingerprint=fingerprint).first()
    if existing is not None:
        return existing, False

    impact = RegulatoryImpact(
        comparison_item=item,
        confirmation_review=confirmation,
        input_fingerprint=fingerprint,
        legal_effect_assessed=False,
        **payload,
    )
    impact.full_clean()
    impact.save()
    for side, anchor in (
        (ImpactEvidence.Side.BEFORE, item.before_anchor),
        (ImpactEvidence.Side.AFTER, item.after_anchor),
    ):
        if anchor is None:
            continue
        evidence = ImpactEvidence(
            impact=impact,
            side=side,
            structural_anchor=anchor,
            normalized_section=anchor.normalized_section,
            source_artifact=anchor.source_artifact,
            artifact_sha256=anchor.source_artifact.sha256,
            anchor_text=anchor.text,
            anchor_text_sha256=anchor.text_sha256,
            section_text_sha256=anchor.normalized_section.text_sha256,
            source_locator=anchor.source_locator,
        )
        evidence.full_clean()
        evidence.save()

    record_audit_event(
        action="impact.candidate_created",
        target_type="regulatory_impact",
        target_id=impact.id,
        actor_type=actor_type,
        actor_identifier=actor_identifier[:255],
        details={
            "comparison_item_id": str(item.id),
            "confirmation_review_id": str(confirmation.id),
            "impact_type": impact_type,
            "origin": origin,
            "input_fingerprint": fingerprint,
            "evidence_sides": list(impact.evidence_records.values_list("side", flat=True)),
            "legal_effect_assessed": False,
        },
    )
    return impact, True
