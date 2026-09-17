import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from aria.comparisons.models import ComparisonItem
from aria.impacts.generation import ImpactGenerationError, generate_impact_candidates
from aria.impacts.models import ApplicabilityTaxonomy
from aria.impacts.tasks import generate_impact_candidates_task


class Command(BaseCommand):
    help = "Generate review-required impact candidates from one currently confirmed change."

    def add_arguments(self, parser) -> None:
        parser.add_argument("comparison_item_id")
        parser.add_argument(
            "--provider",
            choices=("deterministic", "openrouter"),
            default="deterministic",
        )
        parser.add_argument("--taxonomy-slug", default="aria-my-business-applicability")
        parser.add_argument("--taxonomy-version", type=int)
        parser.add_argument("--sync", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            item = ComparisonItem.objects.get(pk=options["comparison_item_id"])
        except (ComparisonItem.DoesNotExist, ValueError) as error:
            raise CommandError("The requested comparison item does not exist.") from error
        taxonomies = ApplicabilityTaxonomy.objects.filter(slug=options["taxonomy_slug"])
        if options["taxonomy_version"] is not None:
            taxonomies = taxonomies.filter(version=options["taxonomy_version"])
        taxonomy = taxonomies.order_by("-version").first()
        if taxonomy is None:
            raise CommandError("Apply the requested impact taxonomy before generating candidates.")
        try:
            if options["sync"]:
                result = generate_impact_candidates(
                    item,
                    taxonomy,
                    provider=options["provider"],
                )
                output = {
                    "status": result.generation.status,
                    "generation_id": str(result.generation.id),
                    "created": result.created,
                    "impact_count": result.impact_count,
                }
            else:
                task = generate_impact_candidates_task.delay(
                    str(item.id),
                    str(taxonomy.id),
                    options["provider"],
                )
                output = {"status": "queued", "task_id": str(task.id)}
        except (ImpactGenerationError, ValidationError) as error:
            raise CommandError(f"Impact generation failed: {error}") from error
        self.stdout.write(json.dumps(output, indent=2, sort_keys=True))
