import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from aria.impacts.taxonomies import (
    DEFAULT_TAXONOMY_PATH,
    TaxonomyDefinitionError,
    apply_taxonomy_definition,
    build_taxonomy_plan,
    load_taxonomy_definition,
)


class Command(BaseCommand):
    help = "Validate, dry-run, or explicitly apply ARIA's repository-backed impact taxonomy."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--path", type=Path, default=DEFAULT_TAXONOMY_PATH)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options) -> None:
        if options["apply"] and options["confirm"] != "APPLY":
            raise CommandError("Applying the taxonomy requires --apply --confirm APPLY.")
        if not options["apply"] and options["confirm"]:
            raise CommandError("--confirm is valid only together with --apply.")
        try:
            definition = load_taxonomy_definition(options["path"])
            plan = build_taxonomy_plan(definition)
            output = {"mode": "dry_run", **plan.as_dict()}
            if options["apply"]:
                taxonomy, created = apply_taxonomy_definition(
                    definition,
                    actor_type="management_command",
                    actor_identifier="sync_impact_taxonomy",
                )
                output = {
                    "mode": "applied",
                    **plan.as_dict(),
                    "taxonomy_id": str(taxonomy.id),
                    "created": created,
                }
        except TaxonomyDefinitionError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(output, indent=2, sort_keys=True))
