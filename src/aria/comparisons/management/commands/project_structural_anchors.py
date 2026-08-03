import json

from django.core.management.base import BaseCommand

from aria.comparisons.anchors import project_active_version_anchors


class Command(BaseCommand):
    help = "Project deterministic bilingual structural anchors for eligible active versions."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options) -> None:
        summary = project_active_version_anchors()
        payload = summary.__dict__
        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return
        details = " ".join(f"{key}={value}" for key, value in payload.items())
        self.stdout.write(self.style.SUCCESS(f"Structural anchor projection: {details}"))
