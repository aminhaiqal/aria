from celery import shared_task

from aria.comparisons.models import ComparisonItem
from aria.impacts.generation import (
    RetryableImpactGenerationError,
    generate_impact_candidates,
)
from aria.impacts.models import ApplicabilityTaxonomy


@shared_task(
    bind=True,
    autoretry_for=(RetryableImpactGenerationError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 4},
)
def generate_impact_candidates_task(
    self,
    comparison_item_id: str,
    taxonomy_id: str,
    provider: str = "deterministic",
) -> dict:
    item = ComparisonItem.objects.get(pk=comparison_item_id)
    taxonomy = ApplicabilityTaxonomy.objects.get(pk=taxonomy_id)
    result = generate_impact_candidates(item, taxonomy, provider=provider)
    return {
        "generation_id": str(result.generation.id),
        "created": result.created,
        "impact_count": result.impact_count,
    }
