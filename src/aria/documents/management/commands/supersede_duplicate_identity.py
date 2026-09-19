import json

from django.core.management.base import BaseCommand, CommandError

from aria.documents.models import DocumentIdentity
from aria.documents.services import (
    supersede_duplicate_identity,
    validate_duplicate_identity_resolution,
)


class Command(BaseCommand):
    help = "Plan or apply an audited same-content document-identity supersession."

    def add_arguments(self, parser) -> None:
        parser.add_argument("source_identity_id")
        parser.add_argument("target_identity_id")
        parser.add_argument("--reason", required=True)
        parser.add_argument("--actor", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options) -> None:
        if options["apply"] != (options["confirm"] == "SUPERSEDE"):
            raise CommandError("Applying the resolution requires --apply --confirm SUPERSEDE.")
        try:
            source = DocumentIdentity.objects.get(pk=options["source_identity_id"])
            target = DocumentIdentity.objects.get(pk=options["target_identity_id"])
            content_hash = validate_duplicate_identity_resolution(source, target)
        except (DocumentIdentity.DoesNotExist, ValueError) as error:
            raise CommandError(str(error)) from error
        output = {
            "source_identity_id": str(source.id),
            "source_canonical_url": source.canonical_url,
            "target_identity_id": str(target.id),
            "target_canonical_url": target.canonical_url,
            "normalized_content_sha256": content_hash,
            "mode": "dry_run",
        }
        if options["apply"]:
            try:
                result = supersede_duplicate_identity(
                    source,
                    target,
                    reason=options["reason"],
                    actor_type="operator",
                    actor_identifier=options["actor"],
                )
            except ValueError as error:
                raise CommandError(str(error)) from error
            output.update(
                {
                    "mode": "applied",
                    "created": result.created,
                    "completed_workflow_ids": result.completed_workflow_ids,
                    "retried_workflow_ids": result.retried_workflow_ids,
                }
            )
        self.stdout.write(json.dumps(output, indent=2, sort_keys=True))
