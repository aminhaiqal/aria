import json

from django.core.management.base import BaseCommand

from aria.comparisons.lineage import project_active_version_lineage


class Command(BaseCommand):
    help = "Persist append-only provenance and representation assessments for active versions."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options) -> None:
        summary = project_active_version_lineage()
        payload = summary.__dict__
        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return
        details = " ".join(f"{key}={value}" for key, value in payload.items())
        self.stdout.write(self.style.SUCCESS(f"Version lineage classification: {details}"))
