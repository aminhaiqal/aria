import json

from django.core.management.base import BaseCommand, CommandError

from aria.comparisons.models import DocumentComparison
from aria.comparisons.publications import publish_confirmed_comparison_changes


class Command(BaseCommand):
    help = "Create idempotent outbox events for currently confirmed textual changes."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--comparison", required=True)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            comparison = DocumentComparison.objects.get(pk=options["comparison"])
        except (DocumentComparison.DoesNotExist, ValueError) as error:
            raise CommandError("The requested comparison does not exist.") from error
        summary = publish_confirmed_comparison_changes(comparison)
        payload = summary.__dict__
        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return
        details = " ".join(f"{key}={value}" for key, value in payload.items())
        self.stdout.write(self.style.SUCCESS(f"Reviewed change publication: {details}"))
